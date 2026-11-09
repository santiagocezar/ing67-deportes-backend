# HU11–HU12 Follow-up — Manual Review Listing by Match and Scan Date

## Objective

- **User story:** As an authorized referee or administrator, I need to list manual reviews for one Match in a predictable scan-date order so a Match panel can locate the relevant `scan.id` without traversing unrelated review pages.
- **Outcome:** `GET /manual-reviews` remains paginated and role-scoped, gains an optional `match_id` filter and a stable scan-date sort, and continues to return the existing nested `scan.created_at` field.
- **Output scope:** backend only. This is a focused contract/query improvement on the implemented HU11–HU12 manual-review listing.

## Evidence

| Source | Exact reference | Used for |
| --- | --- | --- |
| `AGENTS.md` | Flask/API rules; SQLAlchemy/PostgreSQL; Documentation responsibilities; Testing and quality | Pydantic contracts, predictable authorization/errors, generated OpenAPI, bounded queries, documentation, and tests. |
| User clarification | Current conversation | Approves an optional `match_id` filter, scan-date ascending/descending order only, and no date-range filters. |
| Implemented API | `app/routes/match_roster_scans.py:get_manual_reviews` | Confirms the existing general `GET /manual-reviews` route and its role-scoped service call. |
| Implemented contract | `app/schemas/match_roster_scans.py:MatchRosterScanManualReviewListQuery` | Confirms only `status` and `page` are currently accepted. |
| Implemented service | `app/services/match_roster_scan_manual_reviews.py:list_match_roster_scan_manual_reviews` | Confirms fixed 25-item pagination, existing role filtering, and current review-created-time descending order. |
| Implemented response | `app/schemas/match_roster_scans.py:ManualReviewScanResponse` | Confirms `scan.id` and `scan.created_at` are already returned; do not duplicate the scan date. |
| Public documentation | `documentation.md` “Revisión manual histórica y estado de auditoría” | Confirms the current documented pagination, status filter, response data, and authorization. |

## Scope

### In scope

- Optional positive-integer `match_id` query filter on `GET /manual-reviews`.
- `sort=scan_created_at_desc|scan_created_at_asc`, defaulting to `scan_created_at_desc`.
- Stable ordering by `match_roster_scans.created_at`, with scan ID in the same direction as the deterministic tie-breaker.
- Existing fixed 25-item pagination, status filtering, response shape, role filtering, OpenAPI, documentation, and focused tests.

### Out of scope

- A new nested `/matches/{match_id}/manual-reviews` endpoint.
- Date-range filters, free-text search, new pagination modes, configurable page size, or new sorting fields.
- Schema/model/migration/ERD/index changes unless review of the actual query plan proves an existing index is insufficient; do not add an index speculatively.
- Changes to review creation, expiry, decisions, evidence access, `requires_biometric_audit`, recognition, sanctions, or frontend code.

## Current baseline

- `GET /manual-reviews?status=pending&page=1` is a general collection. It uses fixed 25-item pages and supports `pending`, `resolved`, `expired`, or `all` status values.
- Administrators see all reviews; referees see only reviews for Matches assigned to them.
- Each list item already exposes `scan.id` and `scan.created_at`, nested under `scan`.
- The current SQL order is stable but fixed to manual-review creation time descending, then scan ID descending. There is no Match filter or caller-selected ordering.

## Required behavior

### Query contract

```http
GET /manual-reviews?match_id=12&status=pending&sort=scan_created_at_desc&page=1
Authorization: Bearer <access-token>
```

| Query field | Accepted values | Default | Behavior |
| --- | --- | --- | --- |
| `match_id` | Positive strict integer | Omitted | Restricts the existing review collection to scans belonging to that Match. |
| `status` | `pending`, `resolved`, `expired`, `all` | `pending` | Preserves current lifecycle filtering. |
| `sort` | `scan_created_at_desc`, `scan_created_at_asc` | `scan_created_at_desc` | Orders by the persisted scan timestamp, then scan ID in the same direction. |
| `page` | Positive integer | `1` | Preserves the fixed 25-item page contract. |

Unknown query fields, a malformed/non-positive `match_id` or `page`, and an unsupported `sort` must receive the existing safe `422 validation_error` envelope. Do not silently fall back to a default sort for an invalid value.

### Authorization and visibility

- Preserve current collection authorization exactly: an administrator can list all matching reviews; a referee can list only reviews whose Match is assigned to that referee.
- With `match_id`, the role predicate remains part of the same bounded query. A non-assigned referee receives an empty page rather than information about the Match or its review state.
- This endpoint continues to return metadata only. It does not return evidence URLs/bytes, internal filenames, embeddings, filesystem metadata, or new biometric fields.

### Ordering and pagination

- `scan_created_at_desc`: `MatchRosterScan.created_at DESC, MatchRosterScan.id DESC`.
- `scan_created_at_asc`: `MatchRosterScan.created_at ASC, MatchRosterScan.id ASC`.
- Apply Match, status, and role predicates identically to the count and data queries before calculating `total_items` and `total_pages`.
- The existing response remains unchanged: `manual_reviews`, `pagination`, and each item's nested `scan.id`/`scan.created_at` are sufficient. Do not add a duplicate top-level scan date.

## Confirmed API contract

| Operation | Method and endpoint | Success | Errors/auth |
| --- | --- | --- | --- |
| List manual reviews | `GET /manual-reviews?match_id={id}&status={status}&sort={sort}&page={page}` | `200 MatchRosterScanManualReviewListResponse` with existing nested scan date and pagination | Existing access-token authorization; `422` for invalid/unknown query fields; `503 manual_review_unavailable` for unexpected service/storage/database failure. |

## Backend implementation plan

| Area | Existing path | Minimal change |
| --- | --- | --- |
| Query schema | `app/schemas/match_roster_scans.py` | Add optional positive `match_id` and strict sort literal with the approved default. |
| Route | `app/routes/match_roster_scans.py` | Forward the validated query fields without adding a route or altering the response serializer. |
| Service | `app/services/match_roster_scan_manual_reviews.py` | Add the Match predicate and switch the existing order clause according to `sort`; retain bounded count/data queries and role filter. |
| Tests | `tests/test_match_roster_scan_processing.py` and API-contract coverage | Cover query validation, authorization visibility, filtering, ascending/descending stable order, pagination totals, and OpenAPI drift. |
| Documentation | `documentation.md`, generated `docs/openapi.json` | Document filters, default/order semantics, nested scan date, and retained pagination behavior. |

## Acceptance and verification

| Requirement | Implementation requirement | Verification |
| --- | --- | --- |
| Match-specific listing | An optional `match_id` narrows the collection before count/order/pagination. | Administrator receives only that Match's reviews; assigned referee receives their subset; non-assigned referee receives an empty page. |
| Paged results | Preserve 25 items per page and exact metadata after every supported filter. | First, later, empty, and out-of-range pages maintain correct `total_items`/`total_pages`. |
| Ascending/descending scan order | Implement only the two approved scan-date values with stable ID ties. | Equal timestamps and both directions produce deterministic IDs; default is descending. |
| Scan date availability | Preserve `manual_reviews[].scan.created_at`. | Assert the documented response and generated OpenAPI include the existing nested field without duplicate fields. |
| Safe contracts | Reject invalid query input without changing authorization or leaking review data. | Pydantic unknown/invalid query tests, role tests, and error-envelope tests. |

- Run `python -m unittest discover -s tests -v`.
- Run `python -m flask --app app export-openapi` after implementation and verify `docs/openapi.json` matches the generated contract.
- Inspect the query plan before adding any index. If no index change is justified, do not create a migration or change `docs/erd.puml`.
- Perform manual API checks for the default, both sorts, each status, one Match filter, an unassigned-referee request, and page boundaries.

## Documentation impact

- Update the manual-review section of `documentation.md` with `match_id`, both sort values, default sort, stable ordering, and the fact that the scan timestamp remains at `scan.created_at`.
- Regenerate `docs/openapi.json`; never hand-edit it.
- No README, model, migration, or ERD change is expected unless an implementation-specific index is demonstrably justified.

## Size and partition

- **Expected size:** small.
- **Spec boundary:** one existing collection query gains two approved query capabilities; it does not change review lifecycle, persistence, or evidence behavior.

## Implementation stop conditions

Stop and report a blocker if this change requires exposing manual-review metadata to an unassigned referee, changing the existing safe response fields, introducing a date-range/filtering feature, or adding a database index without measured justification.
