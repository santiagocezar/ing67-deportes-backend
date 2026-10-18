"""Persistence for already-computed, immutable Match roster scan results."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (
    ADMIN_USER_ROLE,
    RECOGNIZED_SCAN_FACE_STATUSES,
    SCAN_FACE_STATUSES,
    Match,
    MatchRosterScan,
    MatchRosterScanFace,
    MatchRosterScanFaceSanction,
    Team,
)
from .competition_state import utc_now
from .match_scan_photo_files import (
    ScanImageStorageError,
    remove_scan_file_best_effort,
    write_pending_scan_image,
)
from .matches import reconcile_matches


SANCTIONED_SCAN_FACE_STATUSES = frozenset(
    {"declared_sanctioned", "team_member_undeclared_sanctioned"}
)
ROSTER_SCANS_PER_PAGE = 25


class MatchRosterScanServiceError(Exception):
    """Safe business-rule error for future scan processing callers."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class MatchRosterScanPage:
    scans: list[MatchRosterScan]
    page: int
    per_page: int
    total_items: int
    total_pages: int


@dataclass(frozen=True)
class ScanSanctionSnapshot:
    sanction_id: int
    title: str
    reason: str
    remaining_matches: int


@dataclass(frozen=True)
class ScanFaceResult:
    face_index: int
    top: int
    right: int
    bottom: int
    left: int
    status: str
    player_id: int | None = None
    player_name_snapshot: str | None = None
    match_percentage: int | None = None
    sanctions: tuple[ScanSanctionSnapshot, ...] = ()


@dataclass(frozen=True)
class ScanGroupImage:
    content: bytes
    content_type: str


def _lock_match(match_id: int) -> Match:
    match = db.session.execute(
        db.select(Match)
        .where(Match.id == match_id)
        .with_for_update(of=Match)
    ).scalar_one_or_none()
    if match is None:
        raise MatchRosterScanServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    return match


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_snapshot(snapshot: ScanSanctionSnapshot) -> None:
    if not _is_integer(snapshot.sanction_id) or snapshot.sanction_id <= 0:
        raise MatchRosterScanServiceError(
            "invalid_scan_sanction", "A scan sanction identity is invalid.", 422
        )
    if not (
        isinstance(snapshot.title, str)
        and snapshot.title.strip()
        and len(snapshot.title) <= 30
        and isinstance(snapshot.reason, str)
        and snapshot.reason.strip()
        and len(snapshot.reason) <= 250
        and _is_integer(snapshot.remaining_matches)
        and snapshot.remaining_matches >= 0
    ):
        raise MatchRosterScanServiceError(
            "invalid_scan_sanction", "A scan sanction snapshot is invalid.", 422
        )


def _validate_face(face: ScanFaceResult) -> None:
    if face.status not in SCAN_FACE_STATUSES:
        raise MatchRosterScanServiceError(
            "invalid_scan_face_status", "A scan face status is invalid.", 422
        )
    if not all(
        _is_integer(value)
        for value in (face.face_index, face.top, face.right, face.bottom, face.left)
    ) or min(face.face_index, face.top, face.right, face.bottom, face.left) < 0:
        raise MatchRosterScanServiceError(
            "invalid_scan_face_box", "A scan face box is invalid.", 422
        )
    if face.right <= face.left or face.bottom <= face.top:
        raise MatchRosterScanServiceError(
            "invalid_scan_face_box", "A scan face box is invalid.", 422
        )

    if face.status == "unknown":
        if (
            face.player_id is not None
            or face.player_name_snapshot is not None
            or face.match_percentage is not None
            or face.sanctions
        ):
            raise MatchRosterScanServiceError(
                "invalid_unknown_scan_face",
                "An unknown scan face cannot have recognition data.",
                422,
            )
        return

    if (
        not _is_integer(face.player_id)
        or face.player_id <= 0
        or not _is_integer(face.match_percentage)
        or not 0 <= face.match_percentage <= 100
    ):
        raise MatchRosterScanServiceError(
            "invalid_recognized_scan_face",
            "A recognized scan face requires Player and percentage data.",
            422,
        )
    if face.player_name_snapshot is not None and not isinstance(
        face.player_name_snapshot, str
    ):
        raise MatchRosterScanServiceError(
            "invalid_recognized_scan_face",
            "A recognized scan face name snapshot is invalid.",
            422,
        )
    has_sanctions = bool(face.sanctions)
    if (face.status in SANCTIONED_SCAN_FACE_STATUSES) != has_sanctions:
        raise MatchRosterScanServiceError(
            "invalid_scan_face_sanctions",
            "Scan face sanctions do not match the supplied face status.",
            422,
        )
    seen_sanction_ids: set[int] = set()
    for snapshot in face.sanctions:
        _validate_snapshot(snapshot)
        if snapshot.sanction_id in seen_sanction_ids:
            raise MatchRosterScanServiceError(
                "duplicate_scan_sanction",
                "A scan face cannot repeat a sanction.",
                422,
            )
        seen_sanction_ids.add(snapshot.sanction_id)


def _validate_scan_input(
    faces: Sequence[ScanFaceResult], group_image: ScanGroupImage | None
) -> tuple[ScanFaceResult, ...]:
    immutable_faces = tuple(faces)
    seen_indexes: set[int] = set()
    has_unknown = False
    for face in immutable_faces:
        _validate_face(face)
        if face.face_index in seen_indexes:
            raise MatchRosterScanServiceError(
                "duplicate_scan_face_index",
                "A scan cannot contain duplicate face indexes.",
                422,
            )
        seen_indexes.add(face.face_index)
        has_unknown = has_unknown or face.status == "unknown"

    if group_image is None:
        return immutable_faces
    if not has_unknown:
        raise MatchRosterScanServiceError(
            "scan_image_not_retained",
            "A group image is retained only when a scan has an unknown face.",
            422,
        )
    if (
        not isinstance(group_image.content, bytes)
        or not group_image.content
        or not isinstance(group_image.content_type, str)
        or not group_image.content_type.strip()
        or len(group_image.content_type) > 127
    ):
        raise MatchRosterScanServiceError(
            "invalid_scan_image", "The group image data is invalid.", 422
        )
    return immutable_faces


def _authorize_scan(match: Match, actor_user_id: int, actor_role: str) -> None:
    if actor_role == ADMIN_USER_ROLE or match.referee_id == actor_user_id:
        return
    raise MatchRosterScanServiceError(
        "scan_forbidden",
        "Only an administrator or the assigned referee may access this scan.",
        403,
    )


def _scan_read_options():
    """Load immutable face snapshots without a query per face or sanction."""
    return (
        selectinload(MatchRosterScan.faces)
        .joinedload(MatchRosterScanFace.player),
        selectinload(MatchRosterScan.faces).selectinload(
            MatchRosterScanFace.sanction_snapshots
        ),
    )


def _get_readable_match(
    match_id: int, *, actor_user_id: int, actor_role: str
) -> Match:
    match = db.session.execute(
        db.select(Match).where(Match.id == match_id)
    ).scalar_one_or_none()
    if match is None:
        raise MatchRosterScanServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    _authorize_scan(match, actor_user_id, actor_role)
    return match


def _validate_readable_match_team(match: Match, team_id: int) -> None:
    team = db.session.get(Team, team_id)
    if team is None:
        raise MatchRosterScanServiceError(
            "scan_team_not_found", "The selected Team does not exist.", 404
        )
    if team_id not in (match.team_1_id, match.team_2_id):
        raise MatchRosterScanServiceError(
            "scan_team_not_in_match",
            "The selected Team is not part of this Match.",
            404,
        )


def list_match_roster_scans(
    match_id: int,
    team_id: int,
    *,
    page: int,
    actor_user_id: int,
    actor_role: str,
) -> MatchRosterScanPage:
    """Return a bounded historical page after Match-side authorization."""
    match = _get_readable_match(
        match_id, actor_user_id=actor_user_id, actor_role=actor_role
    )
    _validate_readable_match_team(match, team_id)
    filters = (
        MatchRosterScan.match_id == match.id,
        MatchRosterScan.team_id == team_id,
    )
    total_items = db.session.execute(
        db.select(func.count()).select_from(MatchRosterScan).where(*filters)
    ).scalar_one()
    total_pages = (
        (total_items + ROSTER_SCANS_PER_PAGE - 1) // ROSTER_SCANS_PER_PAGE
        if total_items
        else 0
    )
    scans = list(
        db.session.execute(
            db.select(MatchRosterScan)
            .options(*_scan_read_options())
            .where(*filters)
            .order_by(MatchRosterScan.created_at.desc(), MatchRosterScan.id.desc())
            .limit(ROSTER_SCANS_PER_PAGE)
            .offset((page - 1) * ROSTER_SCANS_PER_PAGE)
        ).scalars()
    )
    return MatchRosterScanPage(
        scans=scans,
        page=page,
        per_page=ROSTER_SCANS_PER_PAGE,
        total_items=total_items,
        total_pages=total_pages,
    )


def get_match_roster_scan(
    scan_id: int, *, actor_user_id: int, actor_role: str
) -> MatchRosterScan:
    """Return one immutable scan only when its Match is readable by the actor."""
    scan = db.session.execute(
        db.select(MatchRosterScan)
        .options(*_scan_read_options())
        .where(MatchRosterScan.id == scan_id)
    ).scalar_one_or_none()
    if scan is None:
        raise MatchRosterScanServiceError(
            "roster_scan_not_found", "The roster scan does not exist.", 404
        )
    _get_readable_match(
        scan.match_id, actor_user_id=actor_user_id, actor_role=actor_role
    )
    return scan


def validate_match_roster_scan_target(
    match_id: int,
    team_id: int,
    *,
    actor_user_id: int,
    actor_role: str,
) -> Match:
    """Lock and validate a Match/Team target before recognition reads data."""
    match = _lock_match(match_id)
    _validate_match_window(match)
    _authorize_scan(match, actor_user_id, actor_role)
    if team_id not in (match.team_1_id, match.team_2_id):
        raise MatchRosterScanServiceError(
            "scan_team_not_in_match",
            "The selected Team is not part of this Match.",
            409,
        )
    team = db.session.execute(
        db.select(Team)
        .where(Team.id == team_id)
        .with_for_update(of=Team)
    ).scalar_one_or_none()
    if team is None:
        raise MatchRosterScanServiceError(
            "scan_team_not_found", "The selected Team does not exist.", 404
        )
    return match


def _validate_match_window(match: Match) -> None:
    now = utc_now()
    reconcile_matches(now, match_id=match.id)
    if match.status == "in_progress":
        return
    if (
        match.status == "finished"
        and match.ends_at is not None
        and match.ends_at >= now - timedelta(hours=2)
    ):
        return
    raise MatchRosterScanServiceError(
        "scan_window_closed",
        "Scans are allowed only during a Match or within two hours after it ends.",
    )


def _compensate_committed_scan(scan_id: int | None) -> bool:
    if scan_id is None:
        return False
    try:
        db.session.execute(
            db.delete(MatchRosterScan).where(MatchRosterScan.id == scan_id)
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return False
    return True


def record_match_roster_scan(
    match_id: int,
    team_id: int,
    faces: Sequence[ScanFaceResult],
    *,
    actor_user_id: int,
    actor_role: str,
    group_image: ScanGroupImage | None = None,
) -> MatchRosterScan:
    """Persist a completed scan without running recognition or sanction logic."""
    immutable_faces = _validate_scan_input(faces, group_image)
    pending_path = final_path = None
    if group_image is not None:
        pending_path, final_path = write_pending_scan_image(group_image.content)

    scan_id: int | None = None
    try:
        match = validate_match_roster_scan_target(
            match_id,
            team_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
        )

        recognized_player_ids = {
            face.player_id
            for face in immutable_faces
            if face.status in RECOGNIZED_SCAN_FACE_STATUSES
        }
        scan = MatchRosterScan(
            match_id=match.id,
            team_id=team_id,
            created_by_user_id=actor_user_id,
            expected_players=match.competition.sport.max_players_in_game,
            detected_faces=len(immutable_faces),
            unique_recognized_players=len(recognized_player_ids),
            group_image_file_name=(final_path.name if final_path else None),
            group_image_content_type=(
                group_image.content_type.strip() if group_image else None
            ),
        )
        for face in immutable_faces:
            persisted_face = MatchRosterScanFace(
                face_index=face.face_index,
                top=face.top,
                right=face.right,
                bottom=face.bottom,
                left=face.left,
                player_id=face.player_id,
                player_name_snapshot=face.player_name_snapshot,
                match_percentage=face.match_percentage,
                status=face.status,
            )
            persisted_face.sanction_snapshots = [
                MatchRosterScanFaceSanction(
                    sanction_id=snapshot.sanction_id,
                    player_id=face.player_id,
                    title_snapshot=snapshot.title,
                    reason_snapshot=snapshot.reason,
                    remaining_matches_snapshot=snapshot.remaining_matches,
                )
                for snapshot in face.sanctions
            ]
            scan.faces.append(persisted_face)
        db.session.add(scan)
        db.session.flush()
        scan_id = scan.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        if pending_path is not None:
            remove_scan_file_best_effort(pending_path)
        raise

    if pending_path is not None and final_path is not None:
        try:
            pending_path.replace(final_path)
        except OSError as error:
            if _compensate_committed_scan(scan_id):
                remove_scan_file_best_effort(pending_path)
            raise ScanImageStorageError(
                "The committed Match scan image could not be finalized."
            ) from error
    return scan
