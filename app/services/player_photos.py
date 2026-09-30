import io
import warnings
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Player, PlayerPhoto
from .player_photo_files import (
    PhotoStorageError,
    StagedPhotoDeletion,
    StoredPhotoFile,
    finalize_photo_deletions,
    reconcile_player_photo_files,
    remove_file_best_effort as _remove_best_effort,
    restore_photo_deletions,
    safe_photo_path as _safe_photo_path,
    stage_photo_file as _stage_photo_file,
    write_pending_photo_file as _write_pending_file,
)
from .players import PlayerDisabledError, PlayerNotFoundError


MAX_PLAYER_PHOTO_BYTES = 5 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_PLAYER_PHOTOS = 3
MIN_PHOTO_SIDE_PIXELS = 320
MAX_PHOTO_SIDE_PIXELS = 4096
MAX_PHOTO_PIXELS = 12_000_000
MIN_FACE_SIDE_PIXELS = 160
ALLOWED_PHOTO_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})


class PhotoValidationError(ValueError):
    """Raised when an uploaded file cannot be stored as a Player photo."""


class UnsupportedPhotoFormatError(PhotoValidationError):
    """Raised when a file is not a PNG or JPG image."""

    def __init__(self) -> None:
        super().__init__("Only PNG and JPG photos are allowed.")


class PhotoTooLargeError(PhotoValidationError):
    """Raised when a photo exceeds MAX_PLAYER_PHOTO_BYTES."""

    def __init__(self) -> None:
        super().__init__("Each photo must be at most 5 MB.")


class InvalidPhotoError(PhotoValidationError):
    """Raised when a PNG or JPG file is empty or cannot be decoded."""

    def __init__(self) -> None:
        super().__init__("The photo is empty or damaged and cannot be read.")


class InvalidPhotoDimensionsError(PhotoValidationError):
    """Raised when dimensions exceed the approved processing envelope."""

    def __init__(self) -> None:
        super().__init__(
            "Photo width and height must each be between 320 and 4096 "
            "pixels, with at most 12000000 total pixels."
        )


class InvalidFaceCountError(PhotoValidationError):
    """Raised unless exactly one face can be detected in a photo."""

    def __init__(self) -> None:
        super().__init__("The photo must contain exactly one visible face.")


class FaceTooSmallError(PhotoValidationError):
    """Raised when the detected face is too small for a reference photo."""

    def __init__(self) -> None:
        super().__init__(
            "The detected face must be at least 160 by 160 pixels."
        )


class PlayerPhotoLimitReachedError(RuntimeError):
    """Raised when a Player already has the approved maximum of photos."""

    def __init__(self) -> None:
        super().__init__(
            f"A Player may have at most {MAX_PLAYER_PHOTOS} photos."
        )


class PlayerPhotoNotFoundError(LookupError):
    """Raised when a photo does not exist for the requested Player."""


class PhotoProcessingUnavailableError(RuntimeError):
    """Raised when the approved face detector is unavailable at runtime."""


@dataclass(frozen=True)
class PhotoFormat:
    content_type: str
    extension: str
    signature: bytes
    pillow_format: str


PNG_FORMAT = PhotoFormat("image/png", ".png", b"\x89PNG\r\n\x1a\n", "PNG")
JPEG_FORMAT = PhotoFormat("image/jpeg", ".jpg", b"\xff\xd8\xff", "JPEG")


def _detect_format(content: bytes) -> PhotoFormat:
    for photo_format in (PNG_FORMAT, JPEG_FORMAT):
        if content.startswith(photo_format.signature):
            return photo_format
    raise UnsupportedPhotoFormatError()


def _validate_dimensions_and_decode(
    content: bytes,
    photo_format: PhotoFormat,
) -> None:
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as error:
        raise PhotoProcessingUnavailableError(
            "Image processing dependencies are unavailable."
        ) from error

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != photo_format.pillow_format:
                    raise InvalidPhotoError()
                width, height = image.size
                if (
                    width < MIN_PHOTO_SIDE_PIXELS
                    or height < MIN_PHOTO_SIDE_PIXELS
                    or width > MAX_PHOTO_SIDE_PIXELS
                    or height > MAX_PHOTO_SIDE_PIXELS
                    or width * height > MAX_PHOTO_PIXELS
                ):
                    raise InvalidPhotoDimensionsError()
                # Dimension checks happen before this full decode, which keeps
                # decompression bombs outside the approved memory envelope.
                image.load()
    except InvalidPhotoDimensionsError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidPhotoDimensionsError() from None
    except (InvalidPhotoError, UnidentifiedImageError, OSError, ValueError):
        raise InvalidPhotoError() from None


def _face_locations(content: bytes) -> list[tuple[int, int, int, int]]:
    try:
        import face_recognition
    except ImportError as error:
        raise PhotoProcessingUnavailableError(
            "Facial detection dependencies are unavailable."
        ) from error

    try:
        image = face_recognition.load_image_file(io.BytesIO(content))
        return list(face_recognition.face_locations(image, model="hog"))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise InvalidPhotoError() from error


def validate_photo(file_name: str, content: bytes) -> PhotoFormat:
    """Validate a reference photo without producing a facial embedding."""
    if Path(file_name).suffix.lower() not in ALLOWED_PHOTO_EXTENSIONS:
        raise UnsupportedPhotoFormatError()
    if not content:
        raise InvalidPhotoError()
    if len(content) > MAX_PLAYER_PHOTO_BYTES:
        raise PhotoTooLargeError()

    photo_format = _detect_format(content)
    _validate_dimensions_and_decode(content, photo_format)
    locations = _face_locations(content)
    if len(locations) != 1:
        raise InvalidFaceCountError()

    top, right, bottom, left = locations[0]
    if right - left < MIN_FACE_SIDE_PIXELS or bottom - top < MIN_FACE_SIDE_PIXELS:
        raise FaceTooSmallError()
    return photo_format


def _lock_player(player_id: int) -> Player:
    player = db.session.execute(
        db.select(Player).where(Player.id == player_id).with_for_update()
    ).scalar_one_or_none()
    if player is None:
        raise PlayerNotFoundError("The Player does not exist.")
    return player


def _lock_enabled_player(player_id: int) -> Player:
    player = _lock_player(player_id)
    if not player.is_enabled:
        raise PlayerDisabledError(
            "A disabled Player cannot receive photos. Enable it first."
        )
    return player


def _photo_count(player_id: int) -> int:
    return db.session.execute(
        db.select(func.count(PlayerPhoto.id)).where(
            PlayerPhoto.player_id == player_id
        )
    ).scalar_one()


def _compensate_committed_upload(photo_id: int | None) -> bool:
    if photo_id is None:
        return False
    try:
        db.session.execute(
            db.delete(PlayerPhoto).where(PlayerPhoto.id == photo_id)
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.error(
            "Could not compensate a committed Player photo after file failure"
        )
        return False
    return True


def add_player_photo(
    player_id: int,
    *,
    file_name: str,
    content: bytes,
) -> PlayerPhoto:
    photo_format = validate_photo(file_name, content)
    pending_path, final_path = _write_pending_file(
        content,
        photo_format.extension,
    )

    photo_id: int | None = None
    try:
        player = _lock_enabled_player(player_id)
        if _photo_count(player.id) >= MAX_PLAYER_PHOTOS:
            raise PlayerPhotoLimitReachedError()
        photo = PlayerPhoto(
            player_id=player.id,
            file_name=final_path.name,
            content_type=photo_format.content_type,
        )
        db.session.add(photo)
        db.session.flush()
        photo_id = photo.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        _remove_best_effort(pending_path)
        raise

    try:
        pending_path.replace(final_path)
    except OSError as error:
        compensated = _compensate_committed_upload(photo_id)
        if compensated:
            _remove_best_effort(pending_path)
        raise PhotoStorageError(
            "The committed photo file could not be finalized."
        ) from error
    return photo


def list_player_photos(player_id: int) -> list[PlayerPhoto]:
    player = db.session.get(Player, player_id)
    if player is None:
        raise PlayerNotFoundError("The Player does not exist.")
    if not player.is_enabled:
        return []
    return list(player.photos[:MAX_PLAYER_PHOTOS])


def get_player_photo_file(player_id: int, photo_id: int) -> StoredPhotoFile:
    photo = db.session.execute(
        db.select(PlayerPhoto)
        .join(Player, Player.id == PlayerPhoto.player_id)
        .where(
            PlayerPhoto.id == photo_id,
            PlayerPhoto.player_id == player_id,
            Player.is_enabled.is_(True),
        )
    ).scalar_one_or_none()
    if photo is None:
        raise PlayerPhotoNotFoundError("The photo does not exist.")

    path = _safe_photo_path(photo.file_name)
    if path.is_symlink() or not path.is_file():
        current_app.logger.error(
            "The stored file of Player photo %s is missing",
            photo.id,
        )
        raise PlayerPhotoNotFoundError("The photo does not exist.")
    return StoredPhotoFile(path=path, content_type=photo.content_type)


def stage_all_player_photo_deletions(
    player_id: int,
) -> list[StagedPhotoDeletion]:
    photos = list(
        db.session.execute(
            db.select(PlayerPhoto)
            .where(PlayerPhoto.player_id == player_id)
            .order_by(PlayerPhoto.id)
        ).scalars()
    )
    staged: list[StagedPhotoDeletion] = []
    try:
        for photo in photos:
            staged_file = _stage_photo_file(photo)
            if staged_file is not None:
                staged.append(staged_file)
            else:
                current_app.logger.warning(
                    "Removing stale row for missing Player photo %s",
                    photo.id,
                )
            db.session.delete(photo)
    except Exception:
        restore_photo_deletions(staged)
        raise
    return staged


def delete_player_photo(player_id: int, photo_id: int) -> None:
    staged: list[StagedPhotoDeletion] = []
    try:
        _lock_player(player_id)
        photo = db.session.execute(
            db.select(PlayerPhoto).where(
                PlayerPhoto.id == photo_id,
                PlayerPhoto.player_id == player_id,
            )
        ).scalar_one_or_none()
        if photo is None:
            raise PlayerPhotoNotFoundError("The photo does not exist.")
        staged_file = _stage_photo_file(photo)
        if staged_file is not None:
            staged.append(staged_file)
        else:
            current_app.logger.warning(
                "Removing stale row for missing Player photo %s",
                photo.id,
            )
        db.session.delete(photo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        restore_photo_deletions(staged)
        raise

    finalize_photo_deletions(staged)
