"""Safe private filesystem operations for retained Match group images."""

import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from flask import current_app

from ..extensions import db
from ..models import MatchRosterScan


STORED_SCAN_IMAGE_NAME = re.compile(r"^[0-9a-f]{32}$")


class ScanImageStorageError(OSError):
    """Raised when group-image storage cannot complete safely."""


@dataclass
class ScanImageReconciliationReport:
    promoted_pending: int = 0
    removed_pending: int = 0
    removed_orphan_final: int = 0
    missing_files: int = 0
    unsafe_entries: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def scan_photos_directory(*, create: bool = False) -> Path:
    directory = Path(current_app.config["MATCH_SCAN_PHOTOS_DIR"]).resolve()
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def safe_scan_photo_path(
    file_name: str, *, create_directory: bool = False
) -> Path:
    if not STORED_SCAN_IMAGE_NAME.fullmatch(file_name):
        raise ScanImageStorageError("Unsafe stored Match scan image reference.")
    directory = scan_photos_directory(create=create_directory)
    path = directory / file_name
    if path.parent != directory:
        raise ScanImageStorageError("Unsafe stored Match scan image reference.")
    return path


def write_pending_scan_image(content: bytes) -> tuple[Path, Path]:
    file_name = uuid.uuid4().hex
    final_path = safe_scan_photo_path(file_name, create_directory=True)
    pending_path = final_path.with_name(f"{file_name}.pending")
    if (
        final_path.exists()
        or final_path.is_symlink()
        or pending_path.exists()
        or pending_path.is_symlink()
    ):
        raise ScanImageStorageError("Conflicting Match scan image storage state.")
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


def remove_scan_file_best_effort(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        current_app.logger.error("Could not remove a staged Match scan image")


def _entry_state(name: str) -> tuple[str, str] | None:
    if name.endswith(".pending"):
        final_name = name[: -len(".pending")]
        if STORED_SCAN_IMAGE_NAME.fullmatch(final_name):
            return final_name, "pending"
        return None
    if STORED_SCAN_IMAGE_NAME.fullmatch(name):
        return name, "final"
    return None


def reconcile_match_scan_photo_files() -> ScanImageReconciliationReport:
    """Repair interrupted group-image writes while the backend is stopped."""
    report = ScanImageReconciliationReport()
    directory = scan_photos_directory(create=True)
    rows = db.session.execute(
        db.select(
            MatchRosterScan.id,
            MatchRosterScan.group_image_file_name,
        ).where(MatchRosterScan.group_image_file_name.is_not(None))
    ).all()
    row_names: set[str] = set()

    for scan_id, file_name in rows:
        if not STORED_SCAN_IMAGE_NAME.fullmatch(file_name):
            report.unsafe_entries += 1
            current_app.logger.warning(
                "Skipped unsafe file reference for Match scan %s", scan_id
            )
            continue
        row_names.add(file_name)
        final_path = directory / file_name
        pending_path = directory / f"{file_name}.pending"
        if final_path.is_symlink() or pending_path.is_symlink():
            report.unsafe_entries += 1
            current_app.logger.warning(
                "Skipped unsafe storage state for Match scan %s", scan_id
            )
            continue
        if final_path.is_file():
            if pending_path.is_file():
                pending_path.unlink()
                report.removed_pending += 1
        elif pending_path.is_file():
            pending_path.replace(final_path)
            report.promoted_pending += 1
            current_app.logger.info(
                "Promoted pending image for Match scan %s", scan_id
            )
        else:
            report.missing_files += 1
            current_app.logger.warning(
                "No image exists for Match scan %s", scan_id
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
        else:
            entry.unlink()
            report.removed_orphan_final += 1
    return report
