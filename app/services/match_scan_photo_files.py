"""Safe private filesystem operations for retained Match group images."""

import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from flask import current_app

from ..extensions import db
from ..models import MatchRosterScan, MatchRosterScanManualReview


STORED_SCAN_IMAGE_NAME = re.compile(r"^[0-9a-f]{32}$")


class ScanImageStorageError(OSError):
    """Raised when group-image storage cannot complete safely."""


@dataclass(frozen=True)
class StoredScanImage:
    path: Path
    content_type: str


@dataclass(frozen=True)
class StagedScanImageDeletion:
    final_path: Path
    deleting_path: Path


@dataclass
class ScanImageReconciliationReport:
    promoted_pending: int = 0
    removed_pending: int = 0
    restored_deleting: int = 0
    removed_deleting: int = 0
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


def stage_scan_image_file(
    scan: MatchRosterScan,
) -> StagedScanImageDeletion | None:
    """Move retained evidence aside until its metadata deletion is committed."""
    if scan.group_image_file_name is None:
        return None
    final_path = safe_scan_photo_path(
        scan.group_image_file_name,
        create_directory=True,
    )
    deleting_path = final_path.with_name(f"{final_path.name}.deleting")
    if final_path.is_symlink() or deleting_path.is_symlink():
        raise ScanImageStorageError("Unsafe Match scan image storage entry.")
    if final_path.is_file():
        if deleting_path.exists():
            raise ScanImageStorageError(
                "Conflicting Match scan image storage states."
            )
        final_path.replace(deleting_path)
        return StagedScanImageDeletion(final_path, deleting_path)
    if deleting_path.is_file():
        return StagedScanImageDeletion(final_path, deleting_path)
    if final_path.exists() or deleting_path.exists():
        raise ScanImageStorageError("Unsafe Match scan image storage entry.")
    return None


def restore_scan_image_deletions(
    staged: list[StagedScanImageDeletion],
) -> None:
    """Restore evidence when its database transaction cannot be committed."""
    for item in reversed(staged):
        try:
            if item.deleting_path.is_file() and not item.final_path.exists():
                item.deleting_path.replace(item.final_path)
        except OSError:
            current_app.logger.error(
                "Could not restore a staged Match scan image after rollback"
            )


def finalize_scan_image_deletions(
    staged: list[StagedScanImageDeletion],
) -> None:
    """Permanently remove evidence after its private reference is cleared."""
    first_error: OSError | None = None
    for item in staged:
        try:
            item.deleting_path.unlink(missing_ok=True)
        except OSError as error:
            current_app.logger.error("Could not remove expired Match scan evidence")
            first_error = first_error or error
    if first_error is not None:
        raise ScanImageStorageError(
            "Expired Match scan evidence could not be removed."
        ) from first_error


def _entry_state(name: str) -> tuple[str, str] | None:
    for suffix, state in ((".pending", "pending"), (".deleting", "deleting")):
        if name.endswith(suffix):
            final_name = name[: -len(suffix)]
            if STORED_SCAN_IMAGE_NAME.fullmatch(final_name):
                return final_name, state
            return None
    if STORED_SCAN_IMAGE_NAME.fullmatch(name):
        return name, "final"
    return None


def reconcile_match_scan_photo_files() -> ScanImageReconciliationReport:
    """Repair writes and complete overdue private-evidence removal."""
    # Imported lazily because scan lifecycle services use this module's staged
    # file operations. Expiry metadata is reconciled before filesystem states.
    from .match_roster_scans import reconcile_expired_match_roster_scan_reviews

    reconcile_expired_match_roster_scan_reviews()
    report = ScanImageReconciliationReport()
    directory = scan_photos_directory(create=True)
    rows = db.session.execute(
        db.select(
            MatchRosterScan.id,
            MatchRosterScan.group_image_file_name,
        )
        .join(
            MatchRosterScanManualReview,
            MatchRosterScanManualReview.scan_id == MatchRosterScan.id,
        )
        .where(
            MatchRosterScanManualReview.status == "pending",
            MatchRosterScan.group_image_file_name.is_not(None),
        )
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
        deleting_path = directory / f"{file_name}.deleting"
        if any(
            path.is_symlink()
            for path in (final_path, pending_path, deleting_path)
        ):
            report.unsafe_entries += 1
            current_app.logger.warning(
                "Skipped unsafe storage state for Match scan %s", scan_id
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
                "Conflicting recoverable states for Match scan %s", scan_id
            )
            continue
        if pending_path.is_file():
            pending_path.replace(final_path)
            report.promoted_pending += 1
            current_app.logger.info(
                "Promoted pending image for Match scan %s", scan_id
            )
        elif deleting_path.is_file():
            deleting_path.replace(final_path)
            report.restored_deleting += 1
            current_app.logger.info(
                "Restored deleting image for Match scan %s", scan_id
            )
        else:
            report.missing_files += 1
            current_app.logger.warning(
                "No image exists for Match scan %s", scan_id
            )

    for entry in directory.iterdir():
        if entry.is_symlink() or not entry.is_file():
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
