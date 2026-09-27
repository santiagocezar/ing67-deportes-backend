import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from flask import current_app

from ..extensions import db
from ..models import Player, PlayerPhoto
from .players import PlayerDisabledError, PlayerNotFoundError


MAX_PLAYER_PHOTO_BYTES = 5 * 1024 * 1024
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


class PlayerPhotoNotFoundError(LookupError):
    """Raised when a photo does not exist for the requested Player."""


@dataclass(frozen=True)
class PhotoFormat:
    content_type: str
    extension: str
    signature: bytes


PNG_FORMAT = PhotoFormat("image/png", ".png", b"\x89PNG\r\n\x1a\n")
JPEG_FORMAT = PhotoFormat("image/jpeg", ".jpg", b"\xff\xd8\xff")


@dataclass(frozen=True)
class StoredPhotoFile:
    path: Path
    content_type: str


def _detect_format(content: bytes) -> PhotoFormat:
    for photo_format in (PNG_FORMAT, JPEG_FORMAT):
        if content.startswith(photo_format.signature):
            return photo_format
    raise UnsupportedPhotoFormatError()


def _is_decodable(content: bytes) -> bool:
    buffer = np.frombuffer(content, dtype=np.uint8)
    try:
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    except cv2.error:
        return False
    return image is not None


def validate_photo(file_name: str, content: bytes) -> PhotoFormat:
    """Return the real format of a valid photo or raise the rejection reason.

    The extension filters what the client declares; the stored format always
    comes from the content, so a renamed PDF or SVG is still rejected.
    """
    if Path(file_name).suffix.lower() not in ALLOWED_PHOTO_EXTENSIONS:
        raise UnsupportedPhotoFormatError()
    if not content:
        raise InvalidPhotoError()
    if len(content) > MAX_PLAYER_PHOTO_BYTES:
        raise PhotoTooLargeError()

    photo_format = _detect_format(content)
    if not _is_decodable(content):
        raise InvalidPhotoError()
    return photo_format


def _photos_directory() -> Path:
    return Path(current_app.config["PLAYER_PHOTOS_DIR"])


def _store_photo_file(content: bytes, extension: str) -> Path:
    directory = _photos_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}{extension}"
    # Exclusive creation never overwrites, so cleanup only removes this file.
    stored_file = path.open("xb")
    try:
        with stored_file:
            stored_file.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _lock_enabled_player(player_id: int) -> Player:
    # FOR SHARE keeps the Player enabled until commit without serializing
    # concurrent uploads for the same Player.
    player = db.session.execute(
        db.select(Player)
        .where(Player.id == player_id)
        .with_for_update(read=True)
    ).scalar_one_or_none()
    if player is None:
        raise PlayerNotFoundError("The Player does not exist.")
    if not player.is_enabled:
        raise PlayerDisabledError(
            "A disabled Player cannot receive photos. Enable it first."
        )
    return player


def add_player_photo(
    player_id: int,
    *,
    file_name: str,
    content: bytes,
) -> PlayerPhoto:
    photo_format = validate_photo(file_name, content)

    stored_path: Path | None = None
    try:
        player = _lock_enabled_player(player_id)
        stored_path = _store_photo_file(content, photo_format.extension)
        photo = PlayerPhoto(
            player_id=player.id,
            file_name=stored_path.name,
            content_type=photo_format.content_type,
        )
        db.session.add(photo)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise
    return photo


def list_player_photos(player_id: int) -> list[PlayerPhoto]:
    player = db.session.get(Player, player_id)
    if player is None:
        raise PlayerNotFoundError("The Player does not exist.")
    return list(player.photos)


def get_player_photo_file(player_id: int, photo_id: int) -> StoredPhotoFile:
    photo = db.session.execute(
        db.select(PlayerPhoto).where(
            PlayerPhoto.id == photo_id,
            PlayerPhoto.player_id == player_id,
        )
    ).scalar_one_or_none()
    if photo is None:
        raise PlayerPhotoNotFoundError("The photo does not exist.")

    path = _photos_directory() / photo.file_name
    if not path.is_file():
        current_app.logger.error(
            "The stored file of Player photo %s is missing",
            photo.id,
        )
        raise PlayerPhotoNotFoundError("The photo does not exist.")
    return StoredPhotoFile(path=path, content_type=photo.content_type)
