import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from flask import current_app

from ..extensions import db
from ..models import PlayerPhoto


STORED_PHOTO_NAME = re.compile(r"^[0-9a-f]{32}\.(?:png|jpg)$")


class PhotoStorageError(OSError):
    """Raised when local photo storage cannot complete safely."""


@dataclass(frozen=True)
class StoredPhotoFile:
    path: Path
    content_type: str


@dataclass(frozen=True)
class StagedPhotoDeletion:
    final_path: Path
    deleting_path: Path


@dataclass
class ReconciliationReport:
    promoted_pending: int = 0
    removed_pending: int = 0
    restored_deleting: int = 0
    removed_deleting: int = 0
    removed_orphan_final: int = 0
    missing_files: int = 0
    unsafe_entries: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def photos_directory(*, create: bool = False) -> Path:
    directory = Path(current_app.config["PLAYER_PHOTOS_DIR"]).resolve()
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_photo_path(file_name: str, *, create_directory: bool = False) -> Path:
    if not STORED_PHOTO_NAME.fullmatch(file_name):
        raise PhotoStorageError("Unsafe stored photo reference.")
    directory = photos_directory(create=create_directory)
    path = directory / file_name
    if path.parent != directory:
        raise PhotoStorageError("Unsafe stored photo reference.")
    return path


def write_pending_photo_file(
    content: bytes,
    extension: str,
) -> tuple[Path, Path]:
    file_name = f"{uuid.uuid4().hex}{extension}"
    final_path = safe_photo_path(file_name, create_directory=True)
    pending_path = final_path.with_name(f"{final_path.name}.pending")
    stored_file = pending_path.open("xb")
    try:
        with stored_file:
            stored_file.write(content)
            stored_file.flush()
            os.fsync(stored_file.fileno())
    except Exception:
        pending_path.unlink(missing_ok=True)
        raise
    return pending_path, final_path


def remove_file_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        current_app.logger.error("Could not remove a staged Player photo file")


def stage_photo_file(photo: PlayerPhoto) -> StagedPhotoDeletion | None:
    final_path = safe_photo_path(photo.file_name, create_directory=True)
    deleting_path = final_path.with_name(f"{final_path.name}.deleting")
    if final_path.is_symlink() or deleting_path.is_symlink():
        raise PhotoStorageError("Unsafe Player photo storage entry.")
    if final_path.is_file():
        if deleting_path.exists():
            raise PhotoStorageError("Conflicting Player photo storage states.")
        final_path.replace(deleting_path)
        return StagedPhotoDeletion(final_path, deleting_path)
    if deleting_path.is_file():
        return StagedPhotoDeletion(final_path, deleting_path)
    return None


def restore_photo_deletions(staged: list[StagedPhotoDeletion]) -> None:
    for item in reversed(staged):
        try:
            if item.deleting_path.is_file() and not item.final_path.exists():
                item.deleting_path.replace(item.final_path)
        except OSError:
            current_app.logger.error(
                "Could not restore a staged Player photo after rollback"
            )


def finalize_photo_deletions(staged: list[StagedPhotoDeletion]) -> None:
    first_error: OSError | None = None
    for item in staged:
        try:
            item.deleting_path.unlink(missing_ok=True)
        except OSError as error:
            current_app.logger.error(
                "Could not remove a deleted Player photo file"
            )
            first_error = first_error or error
    if first_error is not None:
        raise PhotoStorageError(
            "A deleted Player photo file could not be removed."
        ) from first_error


def _entry_state(name: str) -> tuple[str, str] | None:
    for suffix, state in ((".pending", "pending"), (".deleting", "deleting")):
        if name.endswith(suffix):
            final_name = name[: -len(suffix)]
            if STORED_PHOTO_NAME.fullmatch(final_name):
                return final_name, state
            return None
    if STORED_PHOTO_NAME.fullmatch(name):
        return name, "final"
    return None


def reconcile_player_photo_files() -> ReconciliationReport:
    """Reconcile recoverable file states while the backend is stopped."""
    report = ReconciliationReport()
    directory = photos_directory(create=True)
    rows = db.session.execute(
        db.select(PlayerPhoto.id, PlayerPhoto.file_name)
    ).all()
    row_names: set[str] = set()

    for photo_id, file_name in rows:
        if not STORED_PHOTO_NAME.fullmatch(file_name):
            report.unsafe_entries += 1
            current_app.logger.warning(
                "Skipped unsafe file reference for Player photo %s",
                photo_id,
            )
            continue
        row_names.add(file_name)
        final_path = directory / file_name
        pending_path = directory / f"{file_name}.pending"
        deleting_path = directory / f"{file_name}.deleting"
        if any(
            path.is_symlink()
            for path in (final_path, pending_path, deleting_path)
        ):
            report.unsafe_entries += 1
            current_app.logger.warning(
                "Skipped unsafe storage state for Player photo %s",
                photo_id,
            )
            continue
        if final_path.is_file():
            if pending_path.is_file():
                pending_path.unlink()
                report.removed_pending += 1
            if deleting_path.is_file():
                deleting_path.unlink()
                report.removed_deleting += 1
            continue
        if pending_path.is_file() and deleting_path.is_file():
            report.unsafe_entries += 1
            current_app.logger.warning(
                "Conflicting recoverable states for Player photo %s",
                photo_id,
            )
            continue
        if pending_path.is_file():
            pending_path.replace(final_path)
            report.promoted_pending += 1
            current_app.logger.info(
                "Promoted pending file for Player photo %s",
                photo_id,
            )
        elif deleting_path.is_file():
            deleting_path.replace(final_path)
            report.restored_deleting += 1
            current_app.logger.info(
                "Restored deleting file for Player photo %s",
                photo_id,
            )
        else:
            report.missing_files += 1
            current_app.logger.warning(
                "No file exists for Player photo %s",
                photo_id,
            )

    for entry in directory.iterdir():
        if entry.is_symlink():
            report.unsafe_entries += 1
            continue
        parsed = _entry_state(entry.name)
        if parsed is None:
            report.unsafe_entries += 1
            continue
        final_name, state = parsed
        if final_name in row_names:
            continue
        if state == "pending":
            entry.unlink()
            report.removed_pending += 1
        elif state == "deleting":
            entry.unlink()
            report.removed_deleting += 1
        else:
            entry.unlink()
            report.removed_orphan_final += 1
    return report
