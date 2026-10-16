"""Recognition processing for one protected Match roster scan."""

import io
import math
from dataclasses import dataclass

from flask import current_app
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import (
    CompetitionRosterPlayer,
    MatchRosterScan,
    Player,
    PlayerPhoto,
    team_players,
)
from .match_eligibility import get_player_match_eligibility
from .match_roster_scans import (
    MatchRosterScanServiceError,
    ScanFaceResult,
    ScanGroupImage,
    ScanSanctionSnapshot,
    record_match_roster_scan,
    validate_match_roster_scan_target,
)
from .player_photo_files import PhotoStorageError, safe_photo_path
from .player_photos import (
    MIN_FACE_SIDE_PIXELS,
    InvalidPhotoDimensionsError,
    InvalidPhotoError,
    PhotoProcessingUnavailableError,
    UnsupportedPhotoFormatError,
    _detect_format,
    _validate_dimensions_and_decode,
)


MAX_SCAN_IMAGE_BYTES = 10 * 1024 * 1024
RECOGNITION_DISTANCE_THRESHOLD = 0.60
AMBIGUITY_DISTANCE_MARGIN = 0.05


class ScanImageValidationError(ValueError):
    """A group image cannot be processed and must not be persisted."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ScanProcessingUnavailableError(RuntimeError):
    """The approved recognition runtime is unavailable or inconsistent."""


@dataclass(frozen=True)
class _Candidate:
    player_id: int
    player_name: str
    encodings: tuple[object, ...]


def _validate_group_image(content: bytes) -> str:
    if not content:
        raise ScanImageValidationError("invalid_image")
    if len(content) > MAX_SCAN_IMAGE_BYTES:
        raise ScanImageValidationError("image_too_large")
    try:
        photo_format = _detect_format(content)
        _validate_dimensions_and_decode(content, photo_format)
    except UnsupportedPhotoFormatError:
        raise ScanImageValidationError("unsupported_image_format") from None
    except InvalidPhotoDimensionsError:
        raise ScanImageValidationError("invalid_image") from None
    except InvalidPhotoError:
        raise ScanImageValidationError("invalid_image") from None
    return photo_format.content_type


def _recognition_module():
    try:
        import face_recognition
    except ImportError as error:
        raise ScanProcessingUnavailableError(
            "Facial recognition dependencies are unavailable."
        ) from error
    return face_recognition


def _load_group_faces(
    content: bytes,
) -> tuple[object, list[tuple[int, int, int, int]], list[object]]:
    face_recognition = _recognition_module()
    try:
        image = face_recognition.load_image_file(io.BytesIO(content))
        locations = list(face_recognition.face_locations(image, model="hog"))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ScanImageValidationError("invalid_image") from error
    if not locations:
        raise ScanImageValidationError("no_detectable_faces")
    if any(
        right - left < MIN_FACE_SIDE_PIXELS
        or bottom - top < MIN_FACE_SIDE_PIXELS
        for top, right, bottom, left in locations
    ):
        raise ScanImageValidationError("image_quality_insufficient")
    try:
        encodings = list(
            face_recognition.face_encodings(
                image,
                known_face_locations=locations,
            )
        )
    except (RuntimeError, TypeError, ValueError) as error:
        raise ScanProcessingUnavailableError(
            "Group image encodings could not be generated."
        ) from error
    if len(encodings) != len(locations):
        raise ScanProcessingUnavailableError(
            "Group image face detection and encoding counts differ."
        )
    return image, locations, encodings


def _candidate_players(team_id: int) -> list[Player]:
    return list(
        db.session.execute(
            db.select(Player)
            .join(
                team_players,
                team_players.c.player_id == Player.id,
            )
            .options(selectinload(Player.photos))
            .where(
                team_players.c.team_id == team_id,
                Player.is_enabled.is_(True),
            )
            .order_by(Player.id)
        ).scalars()
    )


def _read_photo_encoding(photo: PlayerPhoto) -> object | None:
    path = safe_photo_path(photo.file_name)
    if path.is_symlink() or not path.is_file():
        current_app.logger.error(
            "A Player base photo required for roster scanning is unavailable"
        )
        raise PhotoStorageError("A Player base photo is unavailable.")
    try:
        content = path.read_bytes()
        face_recognition = _recognition_module()
        image = face_recognition.load_image_file(io.BytesIO(content))
        locations = list(face_recognition.face_locations(image, model="hog"))
        if len(locations) != 1:
            return None
        encodings = list(
            face_recognition.face_encodings(
                image,
                known_face_locations=locations,
            )
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise PhotoStorageError("A Player base photo cannot be read.") from error
    return encodings[0] if len(encodings) == 1 else None


def _candidates_with_encodings(team_id: int) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for player in _candidate_players(team_id):
        encodings = tuple(
            encoding
            for photo in player.photos
            if (encoding := _read_photo_encoding(photo)) is not None
        )
        if encodings:
            candidates.append(
                _Candidate(
                    player_id=player.id,
                    player_name=player.name,
                    encodings=encodings,
                )
            )
    return candidates


def _roster_player_ids(competition_id: int, team_id: int) -> set[int]:
    return set(
        db.session.execute(
            db.select(CompetitionRosterPlayer.player_id).where(
                CompetitionRosterPlayer.competition_id == competition_id,
                CompetitionRosterPlayer.team_id == team_id,
            )
        ).scalars()
    )


def _best_candidate(
    face_encoding: object,
    candidates: list[_Candidate],
) -> tuple[_Candidate, float] | None:
    face_recognition = _recognition_module()
    distances: list[tuple[_Candidate, float]] = []
    for candidate in candidates:
        try:
            candidate_distance = min(
                float(distance)
                for distance in face_recognition.face_distance(
                    list(candidate.encodings), face_encoding
                )
            )
        except (RuntimeError, TypeError, ValueError) as error:
            raise ScanProcessingUnavailableError(
                "Face comparison could not be completed."
            ) from error
        distances.append((candidate, candidate_distance))

    if not distances:
        return None
    distances.sort(key=lambda item: (item[1], item[0].player_id))
    best_candidate, best_distance = distances[0]
    if best_distance > RECOGNITION_DISTANCE_THRESHOLD:
        return None
    second_distance = distances[1][1] if len(distances) > 1 else None
    if (
        second_distance is not None
        and second_distance - best_distance < AMBIGUITY_DISTANCE_MARGIN
        and not math.isclose(
            second_distance - best_distance,
            AMBIGUITY_DISTANCE_MARGIN,
            abs_tol=1e-12,
        )
    ):
        return None
    return best_candidate, best_distance


def _match_percentage(distance: float) -> int:
    return round(max(0.0, min(1.0, 1.0 - distance)) * 100)


def _processed_faces(
    *,
    match_id: int,
    competition_id: int,
    team_id: int,
    locations: list[tuple[int, int, int, int]],
    encodings: list[object],
) -> list[ScanFaceResult]:
    candidates = _candidates_with_encodings(team_id)
    roster_player_ids = _roster_player_ids(competition_id, team_id)
    provisional: list[
        tuple[tuple[int, int, int, int], _Candidate | None, float | None]
    ] = []
    for location, encoding in zip(locations, encodings, strict=True):
        match = _best_candidate(encoding, candidates)
        candidate, distance = match if match is not None else (None, None)
        provisional.append((location, candidate, distance))
    recognized_counts: dict[int, int] = {}
    for _, candidate, _ in provisional:
        if candidate is not None:
            recognized_counts[candidate.player_id] = (
                recognized_counts.get(candidate.player_id, 0) + 1
            )

    results: list[ScanFaceResult] = []
    for face_index, (location, candidate, distance) in enumerate(provisional):
        top, right, bottom, left = location
        if candidate is None or recognized_counts[candidate.player_id] > 1:
            results.append(
                ScanFaceResult(
                    face_index=face_index,
                    top=top,
                    right=right,
                    bottom=bottom,
                    left=left,
                    status="unknown",
                )
            )
            continue

        eligibility = get_player_match_eligibility(candidate.player_id, match_id)
        sanctions = tuple(
            ScanSanctionSnapshot(
                sanction_id=sanction.sanction_id,
                title=sanction.title,
                reason=sanction.reason,
                remaining_matches=sanction.remaining_matches,
            )
            for sanction in eligibility.applicable_sanctions
        )
        declared = candidate.player_id in roster_player_ids
        if declared:
            status = "declared_sanctioned" if sanctions else "declared_eligible"
        else:
            status = (
                "team_member_undeclared_sanctioned"
                if sanctions
                else "team_member_undeclared"
            )
        results.append(
            ScanFaceResult(
                face_index=face_index,
                top=top,
                right=right,
                bottom=bottom,
                left=left,
                status=status,
                player_id=candidate.player_id,
                player_name_snapshot=candidate.player_name,
                match_percentage=_match_percentage(distance),
                sanctions=sanctions,
            )
        )
    return results


def process_match_roster_scan(
    match_id: int,
    team_id: int,
    *,
    actor_user_id: int,
    actor_role: str,
    content: bytes,
) -> MatchRosterScan:
    """Validate, recognize, classify, and atomically persist one scan."""
    content_type = _validate_group_image(content)
    _, locations, encodings = _load_group_faces(content)
    try:
        match = validate_match_roster_scan_target(
            match_id,
            team_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )
        faces = _processed_faces(
            match_id=match.id,
            competition_id=match.competition_id,
            team_id=team_id,
            locations=locations,
            encodings=encodings,
        )
        group_image = (
            ScanGroupImage(content=content, content_type=content_type)
            if any(face.status == "unknown" for face in faces)
            else None
        )
        return record_match_roster_scan(
            match.id,
            team_id,
            faces,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            group_image=group_image,
        )
    except Exception:
        db.session.rollback()
        raise


__all__ = [
    "AMBIGUITY_DISTANCE_MARGIN",
    "MAX_SCAN_IMAGE_BYTES",
    "RECOGNITION_DISTANCE_THRESHOLD",
    "ScanImageValidationError",
    "ScanProcessingUnavailableError",
    "process_match_roster_scan",
]
