# Player Photo Feature Corrections Specification

## 1. Purpose

This specification defines the required corrections and completion work for the Player
base-photo feature introduced by commit
`0559f8f099c20b5808fe6021af6691c7746c0d19`.

Implementation must be performed on the analyzed `sprint2/hu06` branch. Do not apply
these changes directly to `main`, and do not discard or overwrite unrelated work.

The implementation must follow the repository's updated `AGENTS.md`. If this
specification and `AGENTS.md` appear to conflict, stop and ask before changing code.

## 2. Dependency approval checkpoint

The approved upload detector is `face_recognition.face_locations` using the HOG model.
This design approval does not authorize silent package installation.

Before changing `requirements.txt`, installing `face_recognition`, or installing any
other direct or transitive dependency, the implementer must:

1. Inspect the current Python and operating-system compatibility.
2. Identify the exact package change and important transitive requirements, including
   `dlib` and Pillow when applicable.
3. Explain the reason, compatibility risks, and proposed version constraint.
4. Ask for explicit approval.

If the approved HOG dependency cannot be installed, stop and report the real failure.
Do not silently replace it with Haar cascades, another detector, another model, or a
custom heuristic.

## 3. Approved business rules

- Only an authenticated administrator may upload, list, download, or delete Player
  base photos.
- Only an enabled Player may receive photos.
- An enabled Player may store zero through three base photos.
- At least one base photo is required for automatic recognition. An enabled Player
  with zero photos remains valid but requires manual resolution.
- The maximum of three must remain correct under concurrent requests.
- The administrator may delete any photo, including the last remaining photo.
- Disabling a Player permanently removes all current base-photo database rows and
  files as well as the existing Team associations.
- Re-enabling a Player restores neither prior Team associations nor prior photos.
- Competition roster snapshots retain Player membership but do not retain copies of
  base photos. A disabled Player therefore cannot be automatically recognized from
  deleted references in a later scan.
- A base photo is retained until it is manually deleted or its Player is disabled.
  There is no other automatic expiration.
- The approved storage design is the local disk of one backend machine. Multi-instance
  and ephemeral-filesystem deployments are out of scope.

## 4. Photo acceptance rules

An uploaded file is accepted only when all the following conditions are true:

1. The multipart request contains exactly one file part named `photo`.
2. No unknown multipart form or file fields are present.
3. The original filename ends in `.png`, `.jpg`, or `.jpeg`, case-insensitively.
4. The file is not empty.
5. The file contains at most 5 MiB (5,242,880 bytes). The boundary value is accepted.
6. The actual signature is PNG or JPEG.
7. The image can be decoded safely.
8. Width and height are each at least 320 pixels.
9. Width and height are each at most 4096 pixels.
10. `width * height` is at most 12,000,000 pixels.
11. The decoded OpenCV BGR image is converted to RGB before face detection.
12. `face_recognition.face_locations` with `model="hog"` and the library's other
    documented defaults returns exactly one face location.
13. The detected face box, measured against the validated decoded image, is at least
    160 pixels wide and 160 pixels high.

Dimension limits must protect the decoder before it allocates the full image, not only
reject the image after a potentially dangerous allocation. Use capabilities of the
approved libraries where possible. If safe pre-decode enforcement requires another
library, stop and request dependency approval instead of adding custom, unreviewed
image-parser code.

Face detection is upload validation only. This task must not:

- create or persist facial embeddings;
- compare a face against a Player;
- select a similarity or distance metric;
- introduce a recognition threshold;
- implement group-image recognition or eligibility validation.

Tests must not use real Player images. Detector behavior should be isolated through
mocks or authorized non-biometric fixtures. Any proposal to add an actual face fixture
requires explicit approval and documented provenance.

## 5. HTTP API

### 5.1 Upload a photo

`POST /players/{player_id}/photos`

- Requires an administrator access token before parsing the request body.
- Accepts `multipart/form-data` only.
- Accepts exactly one `photo` file part.
- Returns `201` with `PlayerPhotoResponse` when successful.
- Must not expose the stored UUID filename.

New and existing error behavior:

| Status | Error code | Condition |
| --- | --- | --- |
| `400` | `invalid_request` | The body is not `multipart/form-data`. |
| `404` | `player_not_found` | The Player does not exist. |
| `409` | `player_disabled` | The Player is disabled. |
| `409` | `player_photo_limit_reached` | The Player already has three photos. |
| `413` | `photo_too_large` | The file or allowed multipart request size is exceeded. |
| `415` | `unsupported_photo_format` | Extension or actual format is not PNG/JPEG. |
| `422` | `validation_error` | Missing, repeated, non-file, or unknown multipart field. |
| `422` | `invalid_photo` | Empty, damaged, or undecodable image. |
| `422` | `invalid_photo_dimensions` | Pixel dimensions violate an approved boundary. |
| `422` | `invalid_face_count` | The image contains zero or more than one detected face. |
| `422` | `face_too_small` | The single detected face is smaller than 160x160 pixels. |
| `503` | `service_unavailable` | PostgreSQL is temporarily unavailable. |
| `503` | `photo_storage_unavailable` | Local photo storage is temporarily unavailable. |

For repeated `photo` parts, return `422 validation_error` with a safe detail whose
field is `form.photo`, type is `multiple_files`, and message states that exactly one
photo file is required. Do not silently use the first file.

### 5.2 List photos

`GET /players/{player_id}/photos`

- Remains administrator-only.
- Returns enabled or disabled Players when the Player exists.
- Returns at most three items, ordered by upload identifier.
- Does not require pagination because the enforced maximum is three.
- Does not expose internal filenames or filesystem metadata.

After a Player is disabled successfully, this endpoint returns an empty list for that
Player.

### 5.3 Download a photo

`GET /players/{player_id}/photos/{photo_id}`

- Remains administrator-only.
- Returns the complete PNG or JPEG with status `200`.
- Disable conditional and range responses unless a separately documented API change
  is approved. A `Range` request must not produce an undocumented `206` response.
- Set `Cache-Control: no-store` and `X-Content-Type-Options: nosniff`.
- Do not expose the internal UUID through `Content-Disposition`.
- Do not emit a file-derived `ETag` or `Last-Modified` value.
- A controlled inline disposition without a filename is acceptable.

### 5.4 Delete a photo

Add:

`DELETE /players/{player_id}/photos/{photo_id}`

- Requires an administrator access token.
- Confirms that the photo belongs to the Player in the path.
- Returns `204` with no body on success.
- Returns `404 player_not_found` when the Player does not exist.
- Returns `404 photo_not_found` when the photo does not exist for that Player.
- May delete the last photo of an enabled Player, leaving zero photos and requiring
  manual resolution.
- Must remove both the PostgreSQL row and the local file through the recoverable
  workflow defined below.
- If the row exists but the final file is already missing, remove the stale row, log
  only safe identifiers, and return `204`. Do not expose the storage path.

## 6. Flask 3.0-compatible request limiting

Keep the declared compatibility with `Flask>=3,<4`. Do not assign
`request.max_content_length`, because request-level assignment is unavailable in Flask
3.0.

Instead:

- Configure the application-wide `MAX_CONTENT_LENGTH` to
  `MAX_PLAYER_PHOTO_BYTES + MULTIPART_OVERHEAD_BYTES`.
- Keep the exact 5 MiB file-size validation in the service.
- Preserve a structured JSON `413 photo_too_large` response for oversized multipart
  requests.
- Do not mutate global application configuration during a request.
- Add regression coverage proving the implementation does not depend on the Flask 3.1
  request property setter.

The global request limit is approved because the current API has no other endpoint that
requires a larger request body. A future larger upload feature must revisit this design.

## 7. Concurrency and the three-photo maximum

The current shared Player lock is insufficient for a count limit. Upload must:

1. Validate the file before writing persistent state.
2. Lock the Player row with PostgreSQL `FOR UPDATE`.
3. Reject a missing or disabled Player as already documented.
4. Count the Player's current photos inside the same transaction.
5. Return `409 player_photo_limit_reached` without creating a file when the count is
   already three.
6. Keep the Player lock until the photo row is committed or the transaction rolls back.

Uploads, manual photo deletion, and Player disable must use the same Player-row locking
order so concurrent operations cannot exceed the limit, preserve a deleted photo, or
deadlock because of inconsistent lock ordering.

Do not implement the limit only in Python memory. Do not use a process-local lock.

## 8. Recoverable PostgreSQL/filesystem workflow

PostgreSQL and the local filesystem cannot participate in one ACID transaction. The
implementation must therefore use recoverable file states and compensating cleanup.

All generated names must remain server-generated UUID names inside
`PLAYER_PHOTOS_DIR`. User filenames must never become filesystem paths.

### 8.1 Upload workflow

1. Perform format, size, dimension, and face validation before persistent writes.
2. Generate the final UUID filename and create the file exclusively under a
   `.pending` suffix in the same directory and filesystem as the final file.
3. Lock the Player, enforce enabled state and the maximum of three, and insert the row
   referencing the final filename.
4. Commit PostgreSQL.
5. Atomically rename the matching `.pending` file to the final filename.
6. On ordinary failure before commit, roll back and remove the `.pending` file.
7. On ordinary rename failure after commit, perform best-effort compensation and return
   a safe `503`; never claim that rollback can reverse an already successful commit.

### 8.2 Delete and Player-disable workflow

1. Lock the Player row with `FOR UPDATE`.
2. Atomically rename every affected final file to a `.deleting` name in the same
   directory before changing PostgreSQL.
3. Delete the photo rows. For Player disable, also update Player state and remove Team
   associations in the existing transaction.
4. Commit PostgreSQL.
5. Permanently remove the `.deleting` files.
6. On ordinary failure before commit, roll back PostgreSQL and restore the renamed
   files.

The implementation must handle up to three photos as one disable operation. A failed
disable must not leave the Player disabled with retained photo rows or enabled with
silently lost files.

### 8.3 Reconciliation command

Add an explicit Flask CLI command named `reconcile-player-photos`. It must be documented
for execution while the backend is not serving requests.

The command must:

- promote `UUID.ext.pending` to `UUID.ext` when the corresponding committed row exists
  and the final file is absent;
- delete `.pending` files that have no corresponding row;
- restore `.deleting` to the final name when the corresponding row still exists;
- delete `.deleting` files when no corresponding row exists;
- delete final UUID photo files that have no corresponding row;
- report database rows whose final and recoverable files are both missing without
  inventing image contents or silently fabricating files;
- operate only inside the resolved `PLAYER_PHOTOS_DIR`;
- validate every generated filename before any rename or deletion;
- never follow a path outside that directory;
- log safe photo identifiers and actions, never raw image data or secrets;
- be idempotent so a second run makes no additional changes.

Do not add a queue, scheduler, background worker, object store, or new infrastructure.

## 9. Model and migration impact

The existing `player_photos` table remains the approved persistence model. Prefer
implementing pending/deleting states through recoverable filenames and reconciliation
without adding speculative columns.

If implementation proves that another column, constraint, or table is required, stop
and request explicit schema approval before modifying `models.py`, migrations, or the
ERD.

Do not store image bytes or facial embeddings in PostgreSQL.

## 10. Required tests

### 10.1 Unit and API tests

Cover at least:

- administrator authorization for upload, list, download, and delete;
- missing, malformed, non-multipart, and unknown multipart fields;
- zero, one, and multiple `photo` parts;
- exact 5 MiB acceptance and one-byte-over rejection;
- PNG/JPEG extension, signature, and decoding behavior;
- every inclusive dimension boundary and one-pixel violations;
- total-pixel boundary and one-pixel-over rejection;
- zero, one, and multiple detected faces using mocks;
- 160x160 face-box acceptance and one-pixel-under rejection on either dimension;
- HOG invocation without embedding generation or identity comparison;
- zero through three photos and fourth-photo conflict;
- deleting a middle photo and deleting the last photo;
- disabling a Player with zero, one, and three photos;
- re-enabling without photo restoration;
- safe database and storage errors;
- no internal filename, ETag, or filesystem timestamp in download responses;
- a `Range` request does not produce an undocumented `206` response;
- committed OpenAPI output matches generated OpenAPI deterministically;
- the reconciliation command is safe and idempotent for every recoverable state.

### 10.2 Real PostgreSQL integration tests

Use a disposable PostgreSQL test database, never a shared or production database.
Do not add a container, service, or other infrastructure without approval.

Integration coverage must verify:

- the migration upgrades and downgrades successfully;
- the foreign key and content-type constraint behave as declared;
- two concurrent uploads starting from two photos produce exactly one success, one
  `409`, three committed rows, and three final files;
- upload and disable use compatible Player locks;
- database rollback performs ordinary filesystem compensation;
- manual delete and Player disable remove the intended rows and preserve competition
  roster rows;
- a failed disable rolls back Player state, Team-association removal, and photo-row
  deletion while restoring recoverable files.

If PostgreSQL is unavailable, report the integration suite as unexecuted. Do not replace
it with mocks and claim integration coverage.

### 10.3 Compatibility and regression checks

- Run the targeted suite under an approved Flask 3.0 environment and the project's
  current environment.
- Run the complete existing backend test suite.
- Run the generated OpenAPI drift check.
- Run migration-head verification.
- Run `git diff --check`.
- Inspect the final diff for secrets, real biometric data, and unrelated changes.

## 11. Documentation requirements

Update in the same change:

- `documentation.md`: limits, face validation, errors, deletion, disable behavior,
  manual resolution, download headers, and reconciliation flow;
- `readme.md`: local single-machine storage, backup requirements, dependency setup,
  and the reconciliation command;
- `docs/openapi.json`: regenerate it from the application; never hand-edit it;
- `docs/erd.puml`: only if an explicitly approved schema change is required;
- `AGENTS.md`: keep the approved Player-photo rules synchronized if implementation
  reveals a wording correction, without weakening any rule.

Document that a PostgreSQL backup alone does not contain the photos. Restoring or
moving the application requires restoring `PLAYER_PHOTOS_DIR` together with the
database.

## 12. Out of scope

- Facial embeddings and their PostgreSQL representation.
- Face-to-Player comparison.
- Similarity metrics and recognition thresholds.
- Group-image recognition.
- Match eligibility decisions.
- Recognition-result persistence or retention.
- Cloud/object storage, network filesystems, deployment, or multiple backend instances.
- Queues, caches, schedulers, and background workers.
- Changes to Competition roster membership semantics.

## 13. Definition of done

The correction work is complete only when:

- every approved rule in this specification is implemented;
- no Player can have more than three photos, including under PostgreSQL concurrency;
- deleting the last photo leaves an enabled Player with zero photos and manual
  resolution;
- disabling a Player removes all of that Player's photo rows and files without changing
  historical roster membership;
- exactly one sufficiently large face is enforced with the approved HOG detector;
- malformed and ambiguous multipart requests are rejected predictably;
- normal failures are compensated and crash-interrupted file states are recoverable;
- downloads do not expose internal file metadata or undocumented partial responses;
- Flask 3.0 remains supported;
- required unit, API, PostgreSQL integration, migration, and contract checks pass;
- documentation and generated OpenAPI match actual behavior;
- no real biometric data, secrets, or unrelated changes are included;
- all new dependency installation or dependency-file changes received explicit prior
  approval.
