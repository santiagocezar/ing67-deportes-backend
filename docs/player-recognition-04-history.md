# HU09 — Specification 4: Protected Scan History

## Delivery boundary

- Status: approved for implementation after Specifications 1–3.
- Target size: 300–650 changed source/test lines, excluding generated OpenAPI.
- Suggested commit: `feat(scans): add protected roster scan history`
- Does not add manual verification, group-image retrieval, scan editing/deletion, or frontend work.

## Public endpoints

```http
GET /matches/{match_id}/teams/{team_id}/roster-scans?page=1
GET /roster-scans/{scan_id}
```

Administrators may read every scan. Referees may read only scans belonging to Matches to which they are assigned. Revalidate Match/Team membership for the nested list. Missing resources return `404`; a valid resource outside referee assignment returns `403`; bad paths/query fields return existing `422` validation responses.

Order lists by newest `created_at DESC, id DESC`, use fixed bounded pagination, and eager-load result Players and sanction snapshots without N+1 queries. A detail response contains persisted count values, non-blocking count warning, face boxes, recognized Player name/id when present, percentage when present, status, and frozen sanction details. It must never contain embeddings, internal filenames, group-image URLs, raw image bytes, or database internals.

Known Player previews may be resolved by a later frontend through the already-existing Player-photo API. A preview is intentionally not snapshotted; deleting a base photo does not rewrite or invalidate scan history.

## Integrity and tests

- scan records and result/sanction snapshots are read-only through HU09 APIs;
- list/detail authorization for administrator, assigned referee, and other referee;
- pagination, stable ordering, empty/out-of-range page, missing resources, and Team mismatch;
- response redaction of image storage and biometric data;
- bounded relationship loading and PostgreSQL FK/constraint inspection;
- OpenAPI operation/security/error coverage and contract-drift check.

Update `documentation.md`, regenerate `docs/openapi.json`, and inspect the final four-commit diff for secrets, real biometric images, and unrelated changes.

## Completion

HU09 ends with durable, private, auditable scan results. The next HU owns every manual decision for `unknown` faces, including access to the retained group image and its final retention/deletion policy.
