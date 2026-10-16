# HU09 — Specification 3: Controlled Roster Recognition

## Delivery boundary

- Status: approved for implementation after Specifications 1–2.
- Target size: 650–1,000 changed source/test lines, excluding generated OpenAPI.
- Suggested commit: `feat(scans): process protected team roster scans`
- Adds the sole HU09 write endpoint. It does not implement manual verification or frontend camera UI.

## Public endpoint

```http
POST /matches/{match_id}/teams/{team_id}/roster-scans
Authorization: Bearer <access-token>
Content-Type: multipart/form-data
```

The only form field is `photo`; reject extra/repeated fields. The response is `201` with the immutable scan and all face results. Apply existing malformed-multipart and Pydantic unknown-field conventions.

An administrator may scan either Match Team. A referee may scan only a Match assigned to them. Reconcile Match state in the transaction and allow only an in-progress Match or a finished Match whose end is not more than two hours old. Reject another Team, a future, incomplete, scheduled, or expired finished Match with documented safe `409` errors.

## Approved image and biometric policy

- Accept real PNG/JPEG content only; maximum 10 MiB, 320–4096 pixels per side, and 12,000,000 pixels total. Validate headers before full decode.
- Raise the application multipart transport ceiling only to `10 MiB + multipart overhead` so this route can receive its approved input; retain the existing 5 MiB service-level limit for Player base-photo uploads.
- Detect with `face_recognition.face_locations(..., model="hog")`.
- A zero-face image returns `422 image_not_processable` with `no_detectable_faces`; damaged/undecodable content returns the same envelope with `invalid_image`; a detected face smaller than 160×160 returns it with `image_quality_insufficient`. Do not persist any of these images or scan rows.
- Generate encodings only in process memory through `face_recognition.face_encodings` with documented defaults. Never log, return, or persist embeddings.
- Compare with `face_recognition.face_distance`; a match requires one unique best Player candidate at distance `<= 0.60`. Publish `match_percentage = round(clamp((1 - distance) * 100, 0, 100))` only for recognized faces. The percentage is a presentation score, not a replacement comparison metric.

## Final calibration gate

Before implementation, approve `AMBIGUITY_DISTANCE_MARGIN`: when the smallest and second-smallest distinct-Player distances differ by less than this value, classify the face as `unknown`. This is a second biometric threshold and must not be guessed. A proposed starting value is `0.05`; it requires explicit approval.

## Candidate scope and outcomes

Load only enabled Players currently associated with the selected Team and having at least one valid base photo. Never search other Teams or the whole Sport. For each candidate, evaluate all base photos and use that Player's smallest distance. A Player with no base photo is not a service failure: any such person remains unresolved for the next HU.

| Condition | Result |
| --- | --- |
| unique candidate, in historical Competition roster, no historical Match sanction | `declared_eligible` |
| unique candidate, in roster, one or more sanctions | `declared_sanctioned` |
| unique candidate, current Team member but outside roster, no sanction | `team_member_undeclared` |
| unique candidate, outside roster, with sanctions | `team_member_undeclared_sanctioned` |
| no eligible-photo candidate, two best candidates within the approved ambiguity margin, or duplicate Player in one image | `unknown` |

`unknown` is a completed HU09 result, not a server error and not manual resolution. If any face is unknown, atomically retain the private group image and its boxes for the next HU. Dependency, database, or storage failures return safe `503` errors and leave no scan/file state behind.

## Count warning and atomic write

Set `expected_players` from `sport.max_players_in_game`. Persist detected-face and unique-recognized-Player counts. Return a non-blocking warning when the unique count is below or above expected; do not name allegedly missing/extra Players because the current domain has no starting-lineup entity.

Within one rollback-safe transaction, lock/validate Match and Team, compute candidates and historical roster membership, call Specification 2 for recognized Players, create scan/face/sanction snapshots, and conditionally stage the group image. Every result must be persisted or none must be persisted.

## Tests and documentation

Test auth and Match-window boundaries; Team scope; all five result classes; several base photos per Player; no-photo Players; distance threshold/percentage boundaries; ties and duplicates; image validation; count warnings; sanction snapshots; successful unknown image storage; every rollback/failure path; no embeddings in logs/responses; and real PostgreSQL migration/file behavior where configured.

Use Pydantic v2 multipart/path/response models, register the route, regenerate `docs/openapi.json`, and document request, response, errors, authorization, privacy, and no-frontend scope in `documentation.md`. Update README only for private storage configuration/reconciliation.
