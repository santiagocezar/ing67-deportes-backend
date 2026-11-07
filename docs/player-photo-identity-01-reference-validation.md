# Player photo identity — Specification 1: Initial reference validation

## Delivery boundary

- Status: approved for implementation with the decisions below.
- Suggested commit: `feat(players): valida identidad en fotos base`
- Extends the existing Player base-photo upload and deletion endpoints. It does
  not add a new recognition endpoint, compare against other Players, create a
  biometric database, or alter Match roster-scan behaviour.

## Goal

An administrator must not be able to add a photo of a different person to an
existing Player profile. For example, after Lionel Messi has an initial reference
photo, a photo of Leandro Paredes uploaded to Messi's profile is rejected.

This feature validates identity only within the selected Player profile. It does
not prove that an administrator selected the correct Player for that Player's
first photo, and it does not search other Player profiles for duplicate or
similar faces.

## Approved decisions

- The first accepted photo of a Player is the trusted administrator-enrolled
  identity reference and is marked `is_initial_reference = true`.
- The first photo keeps all current PNG/JPEG, size, dimensions, single-face,
  HOG-detection, and face-size validations. It is not compared to another
  identity because the Player has no prior reference.
- Any later photo is accepted only if it matches at least one existing photo of
  that same Player.
- Use `face_recognition.face_encodings` and `face_recognition.face_distance`
  only in process memory. A later photo is a match when its smallest distance
  to any existing base photo of the selected Player is `<= 0.60`.
- `AMBIGUITY_DISTANCE_MARGIN = 0.05` remains approved for comparisons with
  distinct Player candidates in roster scans. It is deliberately not evaluated
  by this feature: the approved candidate scope contains one Player identity,
  so a second distance from another photo of that same Player is not an
  identity-ambiguity signal.
- Do not search photos belonging to another Player. Therefore this feature does
  not reject two different Player profiles whose trusted initial references were
  enrolled incorrectly by an administrator.
- A nonmatching later photo is rejected with `422 photo_identity_mismatch`; no
  file, photo row, embedding, distance, or identity-comparison result is kept.
- The initial reference is visible to administrators as
  `is_initial_reference: true` in the existing photo responses.
- An initial reference photo cannot be permanently deleted while the Player has
  one or more other photos. Reject that deletion with
  `409 initial_reference_photo_required`. If it is the Player's only photo,
  deletion remains allowed and leaves the Player without a reference; a later
  newly accepted photo then becomes the new initial reference.

## Persistence and migration

Add `is_initial_reference` to `player_photos`:

- Boolean, non-null, default `false`.
- Add a PostgreSQL partial unique index on `player_id` where
  `is_initial_reference = true`, ensuring at most one marked initial reference
  per Player. A Player may have zero photos and therefore zero references.
- Preserve the existing photo file/reference model; do not persist encodings,
  distances, face boxes, raw image data, or comparison outcomes.

The reviewed Alembic migration must backfill existing data deterministically:

1. Add the nullable/new column safely with a temporary default of `false`.
2. For each Player with existing photos, mark exactly its lowest `player_photos.id`
   as `true`; leave all later photos `false`.
3. Make the column non-null, establish its normal default, and create the
   partial unique index.

Update `app/models.py` and `docs/erd.puml` with the new column and index in the
same change. No other entities or relationships are added.

## Existing API changes

The routes remain:

```http
POST   /players/{player_id}/photos
GET    /players/{player_id}/photos
GET    /players/{player_id}/photos/{photo_id}
DELETE /players/{player_id}/photos/{photo_id}
```

Authorization stays unchanged: upload, list, and deletion require an
administrator access token; the public binary download behaviour is unchanged.

Extend `PlayerPhotoResponse`, its list response, and generated OpenAPI contract
with:

```json
{
  "id": 1,
  "player_id": 4,
  "content_type": "image/jpeg",
  "is_initial_reference": true,
  "created_at": "2026-11-07T15:00:00Z"
}
```

The boolean exposes the role of a photo, not any biometric value. The binary
download must continue to hide UUID filenames, ETags derived from stored files,
and filesystem timestamps.

## Upload flow

All multipart and general image validation rules remain as implemented: exactly
one `photo` file; no unknown/repeated fields; allowed extension and genuine
PNG/JPEG content; no more than 5 MiB; approved dimensions; exactly one HOG face;
and the minimum 160×160 face box.

After that validation, the service must perform this transaction-safe sequence:

1. Lock the Player row with `FOR UPDATE` and reject a missing or disabled Player
   exactly as the current endpoint does.
2. Load and lock/order that Player's existing photo rows. The Player lock is the
   serialization boundary for uploads and deletions of this Player.
3. If there are no existing rows, prepare an initial-reference row. Do not create
   an embedding for this first trusted enrollment.
4. If there are existing rows, safely load every existing file, obtain its single
   in-memory encoding and the candidate upload encoding, and calculate the
   smallest `face_distance` over all of the Player's existing photos.
5. Reject the candidate with `photo_identity_mismatch` if that smallest distance
   exceeds `0.60`. Do not continue to staging or persistence.
6. Stage the validated file under its generated UUID name, add the row with
   `is_initial_reference` set only for the no-existing-photo case, flush, and
   commit.
7. Promote the staged file only after commit. Preserve the existing rollback
   cleanup, compensation, reconciliation, safe-path, and no-sensitive-logging
   guarantees.

The service must not log or return embeddings, distances, decoded pixels, image
contents, or a reason that exposes the existing Player's biometric data.

## Errors

Existing image, request, Player-state, capacity, storage, and authorization
responses remain stable. Add only these documented outcomes:

| Status | `error.code` | Meaning |
| --- | --- | --- |
| `422` | `photo_identity_mismatch` | The valid candidate image does not match any existing photo of the selected Player. |
| `409` | `initial_reference_photo_required` | The requested photo is the Player's initial reference and another photo still exists. |
| `503` | `photo_identity_validation_unavailable` | A required existing reference file cannot be safely read, or the approved face-encoding/comparison runtime is unavailable. |

An unavailable service condition must not silently treat the candidate as a new
reference or a mismatch. It returns the safe `503` response and leaves no file
or database state behind.

## Concurrency and lifecycle invariants

- The maximum of three photos remains correct under concurrent requests.
- At most one row per Player can have `is_initial_reference = true`.
- Two concurrent first uploads are serialized by the Player lock. The first
  successful upload becomes the initial reference; the second is validated as a
  later photo against it and is rejected if it does not match.
- Upload and deletion share the Player lock. A deletion cannot remove the
  reference between validation and persistence of a later photo.
- Deleting a non-reference photo is unchanged. Deleting the only initial
  reference is allowed and restores the zero-photo state; it does not promote a
  different photo because none remains.
- Disabling a Player keeps its current approved cleanup: every photo row and
  file, including the initial reference, is permanently removed. Re-enabling
  does not restore it.
- The roster-scan candidate logic continues to use every valid stored photo of
  an enabled Player and does not rely only on the initial reference marker.

## Implementation structure

Keep the route responsible only for multipart/HTTP error mapping. Extend the
Player-photo service with small helpers for:

- loading and encoding an already validated candidate image;
- safely reading and encoding existing same-Player references;
- calculating the minimum approved distance; and
- enforcing the initial-reference deletion rule.

Reuse the approved `face_recognition`, Pillow, and local private-file services.
Do not add a recognition library, dependency, queue, cache, background worker,
new storage strategy, embedding column, or a global player-photo matching query.

## Required tests

Use generated/synthetic images and mocks; never add real Player images. Cover at
least:

- first upload creates the only photo with `is_initial_reference = true`;
- a matching second/third photo is accepted and remains `false`;
- a mismatch is rejected at distances just above `0.60`, while the exact `0.60`
  boundary is accepted;
- all existing same-Player photos participate and the smallest distance wins;
- an upload does not query other Players' photos;
- an initial reference cannot be deleted while another photo exists, while the
  only initial reference can be deleted;
- migration backfill chooses the lowest photo ID, default/index semantics, and
  partial-unique enforcement;
- concurrent first uploads, upload/delete serialization, photo-limit handling,
  database rollback, staged-file cleanup, failed promotion compensation, and
  stopped-process reconciliation;
- unavailable/missing/corrupt existing reference storage and unavailable face
  encoding/comparison return safe `503` without a new row or file;
- API schemas/OpenAPI include the marker and documented errors;
- no embeddings, distances, raw image bytes, or internal file names appear in
  logs or API responses; and
- real PostgreSQL migration, locking, partial-index, rollback, and filesystem
  behaviour when the integration database is configured.

## Documentation and completion

During implementation, update `documentation.md` with the administrator flow,
first-reference trust limitation, API response field, comparison policy, deletion
rule, errors, privacy controls, and no-cross-Player-search scope. Update
`readme.md` only if installation, configuration, storage, or migration operation
changes. Regenerate and commit `docs/openapi.json`.

The feature is complete when the migration, model, ERD, endpoint contracts,
private filesystem handling, documentation, and tests agree; no unsupported
biometric data is persisted or exposed.
