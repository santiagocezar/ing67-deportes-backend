# HU11–HU12 — Specification 5: Consented Scan Retention and Protected Evidence

## Objective

- **User stories:** HU11, *Registro de incidencias en el reconocimiento*, and the evidence-retrieval dependency of HU12, *Validación manual de jugadores*.
- **Outcome:** an authorized actor can preview a roster scan without persistence, and an unknown-face group image is retained only after explicit referee confirmation. Retained evidence is private, time-bounded, and removable.
- **Output scope:** backend only. This specification defines storage, APIs, lifecycle, contracts, tests, and documentation; it does not implement a frontend screen or face-crop endpoint.

## Evidence

| Source | Exact reference | Used for |
| --- | --- | --- |
| `AGENTS.md` | Hard rules 7–10, 17; Facial recognition rules; Flask/API rules; SQLAlchemy/PostgreSQL; Do not guess | Sensitive-data handling, no new biometric calibration, Pydantic/OpenAPI/migration requirements, and explicit retention decisions. |
| HU11 | Acceptance criteria supplied in this conversation | Reject non-processable photos without storage; retain unknown-face evidence; protect access. |
| HU12 | Acceptance criteria and later scope clarification supplied in this conversation | Manual validation needs evidence, but sanctions and match-sheet behavior are outside this delivery. |
| `docs/player-recognition-01-scan-foundation.md` | “Private group-image storage” and “Completion” | Existing group-image storage, rollback, reconciliation, and HU11 ownership of final retention. |
| `docs/player-recognition-03-processing.md` | “Approved image and biometric policy” and “Candidate scope and outcomes” | Preserve validation, recognition, and `unknown` semantics; do not retain rejected images. |
| `docs/player-recognition-04-history.md` | “Protected Scan History” and “Completion” | Existing authorization/redaction and its explicit deferral of group-image retrieval to the next HU. |
| Backend | `app/services/match_roster_scan_processing.py`, `app/services/match_roster_scans.py` | Current processing atomically persists every completed scan and automatically retains an image whenever it has an `unknown` face. |
| Backend | `app/routes/match_roster_scans.py`, `app/schemas/match_roster_scans.py` | Current multipart/path contracts and protected history conventions. |
| Backend | `app/models.py`, `app/services/match_scan_photo_files.py` | Existing immutable scan snapshots, private file references, and recoverable staged-file workflow. |

## Scope

### In scope

- A non-persistent preview for a candidate group image.
- Explicit confirmation before an unknown-face group image and its scan are persisted.
- A private group-image download available only to an administrator or the assigned referee.
- A persisted, time-bounded manual-review lifecycle: `pending`, `resolved`, or `expired`.
- Safe evidence deletion when a review expires; Specification 6 owns deletion after manual resolution.
- Pydantic schemas, migration, PlantUML, OpenAPI, documentation, and tests.

### Out of scope

- Frontend components, Bootstrap styling, camera controls, modal behavior, and client-side crop implementation.
- Persisting face crops, exposing crop URLs, embeddings, image hashes, distances, or internal filenames.
- Any new recognition threshold, blur/darkness metric, model, library, or dependency.
- Manual identity conclusions, player selection, sanctions, player-profile behavior, match-sheet behavior, and Match-status blocking (Specification 6 owns only manual membership conclusions).
- A scheduler, queue, or background worker.

## Current baseline

- `POST /matches/{match_id}/teams/{team_id}/roster-scans` validates and processes one image, persists a successful scan, and retains the group image whenever at least one face is `unknown`.
- A malformed/non-processable image creates neither file nor scan row. Existing HOG detection, size/dimension, and minimum-face-size checks are the complete approved quality policy.
- `GET /roster-scans/{scan_id}` and the nested scan list are already restricted to administrators and assigned referees, but intentionally never return group-image bytes or a URL.
- A scan and its face snapshots are immutable public history. Retention metadata may change only as required to remove private evidence; detection results must never be rewritten.

## Approved behavior

### Two-step processing

1. The actor submits the image to the preview operation. It runs the same approved validation, HOG detection, in-memory encoding/comparison, candidate scope, historical sanction snapshot logic, and unknown classification as final processing.
2. The preview returns its computed result without a scan row, review row, image file, staged file, file reference, or durable biometric value. Its result is not historical evidence and must not be used for manual conclusions.
3. If the preview contains no `unknown` face, the client may submit the image to the final operation immediately.
4. If it contains an `unknown` face, the client must obtain the referee's explicit decision before final submission. Declining retention means no final request: no scan/image/review is persisted and the actor captures a new image.
5. After acceptance, the client resubmits the original image to the final operation with explicit retention confirmation. That operation reprocesses the image; only its newly computed result is persisted. It retains an image and creates a pending review only if that final result still contains at least one `unknown` face.

The preview is intentionally recomputed at final submission. No preview state, image digest, server-side temporary file, or face data survives between requests.

### Persistence

Add `match_roster_scan_manual_reviews` in a reviewed migration and update `docs/erd.puml`.

| Field | Rule |
| --- | --- |
| `scan_id` | Primary key and `RESTRICT` FK to `match_roster_scans.id`; there is at most one review per scan. |
| `status` | `pending`, `resolved`, or `expired`; only `pending` is created by this specification. |
| `expires_at` | Non-null timezone-aware snapshot of `Match.ends_at + 2 hours`. |
| `created_at` | Timezone-aware database-created timestamp. |
| Resolution audit fields | Added by Specification 6, together with their checks. |

Create this row only in the same transaction that persists an `unknown` result and its group-image reference. A scan without unknown faces has no manual-review row. A declined preview has neither scan nor review row.

The service—not a speculative database trigger—must enforce that a pending review has at least one persisted `unknown` face and a paired private image reference. When private evidence is deleted, clear `group_image_file_name` and `group_image_content_type`; `manual_review.status` preserves the historical lifecycle without exposing a filename.

### Evidence lifetime and expiry

- An assigned referee may review evidence while its Match is `in_progress`, and until `Match.ends_at + 2 hours`.
- At or after that deadline, every still-pending review for the Match becomes `expired`; its image must no longer be served, its private file reference is cleared, and its group-image file is permanently deleted through the established staged-delete/reconciliation pattern.
- An expired review remains in history as `expired`; it is not manually resolvable and does not contribute to an active-audit flag.
- There is no background infrastructure. Reconcile expiry before protected evidence/review reads and writes, and extend `reconcile-match-scan-photos` to complete interrupted or overdue private-evidence removal. This follows the existing time-derived reconciliation approach; the command remains operational recovery/cleanup, not a scheduler.
- File deletion failures must preserve a recoverable state, log only safe operational details, and never disclose the storage path or bytes. A later reconciliation must remove orphaned/deleting evidence.

## Confirmed API contract

| Operation | Method and endpoint | Request | Success | Errors/auth |
| --- | --- | --- | --- | --- |
| Preview a scan | `POST /matches/{match_id}/teams/{team_id}/roster-scans/preview` | Strict `multipart/form-data` with exactly `photo` | `200 MatchRosterScanPreviewResponse`; no database/file side effect | Existing scan authorization/window/image errors; administrator or assigned referee only. |
| Persist a confirmed scan | `POST /matches/{match_id}/teams/{team_id}/roster-scans` | Strict form with exactly `photo` and `retain_for_manual_review=true` | `201 MatchRosterScanResponse`; creates a pending review and stores the image only when final faces include `unknown` | Existing scan errors; `422` for missing/false/unknown form fields. |
| Download retained evidence | `GET /roster-scans/{scan_id}/group-image` | Path only | `200` raw original PNG/JPEG bytes | `401` unauthenticated; `403 scan_forbidden` for another referee; `404 group_image_not_available` when no retained, resolved, or expired image exists. |

`MatchRosterScanPreviewResponse` mirrors the safe count, face, recognized-player, status, percentage, and sanction-snapshot fields needed to decide whether unknown faces exist. It omits `id`, `created_by_user_id`, `created_at`, file data, URLs, embeddings, and internal storage metadata.

The final response and later history response may add a safe `manual_review` summary (`status`, `expires_at`) but must not add image URLs, filenames, bytes, hashes, embeddings, or filesystem metadata.

Evidence responses must use the actual stored PNG/JPEG content type with `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`. Do not emit `ETag`, `Last-Modified`, `Content-Disposition`, byte-range support, or a storage-derived filename. Authorization remains authoritative in the backend; a browser/client must request the binary with its access token and handle any local display itself.

## Backend implementation plan

| Area | Existing/new path | Minimal change |
| --- | --- | --- |
| Preview contracts | `app/schemas/match_roster_scans.py` | Add strict multipart/path and safe preview response schemas. |
| Routes | `app/routes/match_roster_scans.py` | Add preview and protected image operations; change final multipart validation to require explicit retention confirmation. |
| Processing | `app/services/match_roster_scan_processing.py` | Extract shared compute-only processing so preview and final processing use identical approved recognition behavior without persisting preview data. |
| Scan services | `app/services/match_roster_scans.py` | Create/expire review lifecycle records under Match locks and preserve scan/face public immutability. |
| File services | `app/services/match_scan_photo_files.py` | Implement staged permanent deletion and reconcile pending/deleting/expired evidence safely. |
| Persistence | `app/models.py`, `migrations/versions/` | Add the review model, constraints, relationships, and reviewed migration. |
| Public contract/docs | `docs/openapi.json`, `documentation.md`, `readme.md`, `docs/erd.puml` | Regenerate the contract and document consent, authorization, storage, expiry, and reconciliation. |

Do not create a crop API. The existing unknown-face coordinates in the safe scan detail and this protected original-image operation are the sole backend data needed by a future client to make local previews.

## Acceptance and verification

| Criterion | Implementation requirement | Verification |
| --- | --- | --- |
| HU11: invalid/zero-face photo is rejected and not stored | Preview and final reuse current image validation; neither creates a scan, review, file, or staged file when validation fails. | Unit and PostgreSQL/file tests for damaged, no-face, small-face, oversized, and unsupported images. |
| HU11: unknown faces enable later audit using persisted group image | Preview returns safe unknown results; only confirmed final processing creates a pending review and retained image. Declining leaves no record and requires a new capture. | Assert preview has no persistence; accepted final unknown scan creates all paired state; known final scan creates no image/review. |
| HU11: only administrator/assigned referee can access image | Apply existing scan authorization to the binary operation before any file access. | Administrator and assigned-referee `200`; other referee `403`; anonymous `401`; missing/expired image `404`. |
| Privacy and expiry | Expired pending reviews are marked `expired`, image references are cleared, files are removed/reconciled, and bytes are never served afterward. | Time-boundary, interrupted deletion, reconciliation, no-store/header, and redaction tests. |

- Run `python -m unittest discover -s tests -v`.
- Run the configured real-PostgreSQL file/migration tests with the disposable test database described in `readme.md`.
- Run `python -m flask --app app export-openapi` and verify the committed contract is deterministic.
- Inspect the migration and final diff for real images, secrets, filenames, embeddings, and unrelated changes.

## Documentation impact

- Update `documentation.md` with preview/final distinction, explicit retention confirmation, image authorization, expiration, and safe error behavior.
- Update `readme.md` with the extended reconciliation/deletion behavior only; keep environment/storage assumptions unchanged.
- Regenerate, never hand-edit, `docs/openapi.json`.
- Update `docs/erd.puml` in the same change as the model/migration.

## Size and partition

- **Expected size:** medium.
- **Spec boundary:** this document establishes consent, private evidence, and lifecycle state. Manual conclusions and the match audit flag depend on its pending/resolved/expired lifecycle and are isolated in Specification 6.

## Implementation stop conditions

Stop and report a blocker if the approved HOG/distance behavior, existing scan authorization/window, local private-storage model, Pydantic/OpenAPI conventions, or reconciled-file guarantees cannot be preserved without a new dependency, threshold, scheduler, or unapproved retention behavior.
