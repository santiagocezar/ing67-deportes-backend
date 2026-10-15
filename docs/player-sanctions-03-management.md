# Player Sanctions — Specification 3: Query and Lifecycle Management

## Status and delivery boundary

- Status: approved for implementation after Specifications 1 and 2.
- Prerequisites:
  - `player-sanctions-01-creation.md`;
  - `player-sanctions-02-consumption.md`.
- Target implementation size: 300–900 changed source/test/documentation lines,
  excluding generated `docs/openapi.json`.
- Suggested commit: `feat(sanctions): add sanction query and lifecycle management`

This specification completes the backend management surface. Frontend profile pages,
scanning, recognition, lineup eligibility, cards, and expulsions remain outside scope.

## Goal

Provide safe, paginated sanction reads for administrators and referees, controlled edits,
administrator-only annulment, and read access to Players for both supported roles.

## Authorization matrix

| Operation | Administrator | Referee |
| --- | --- | --- |
| `GET /players` | Allowed | Allowed |
| `GET /players/{player_id}` | Allowed | Allowed |
| `GET /players/{player_id}/sanctions` | Allowed | Allowed |
| `GET /sanctions/{sanction_id}` | Allowed | Allowed |
| Create sanction | Any in-progress Match | Assigned in-progress Match only |
| Edit sanction | Any active sanction | Own sanction while source Match remains in progress |
| Annul sanction | Allowed for active sanctions | Forbidden |

"Own sanction" means `created_by_user_id` equals the authenticated User identifier.
All referees may read all Players and sanctions; Match assignment limits only creation
and referee editing.

Changing Player read authorization must not broaden Player create, update, enable,
disable, photo, Competition-write, or Match-write authorization. Those operations retain
their existing rules.

## Player response boundary

Change the decorators/descriptions for `GET /players` and `GET /players/{player_id}` to
accept both supported application roles. Keep their response payloads unchanged.

Do not embed sanctions inside `PlayerResponse` or `PlayerListItemResponse`. Sanctions are
unbounded, paginated, and independently filtered, so the Player profile must consume the
dedicated nested endpoint. This avoids hidden partial lists and nested pagination.

## Public contracts

### List one Player's sanctions

```http
GET /players/{player_id}/sanctions?status=active&created_from=...&created_to=...&page=1
```

Query fields:

| Field | Type | Default | Rule |
| --- | --- | --- | --- |
| `status` | `active`, `served`, or `annulled` | omitted/all | Exact status filter |
| `created_from` | timezone-aware datetime | omitted | Inclusive `created_at >=` boundary |
| `created_to` | timezone-aware datetime | omitted | Inclusive `created_at <=` boundary |
| `page` | positive integer | `1` | Fixed page size of 25 |

Reject `created_to < created_from` with `422`. Order results by
`created_at DESC, id DESC`. An out-of-range page returns an empty list with accurate
pagination metadata, following existing list conventions.

Reconcile the Player before filtering and serializing so returned state and remaining
counts are current. A nonexistent Player returns `404 player_not_found` even when the
requested page would otherwise be empty.

Response:

```json
{
  "sanctions": [],
  "pagination": {
    "page": 1,
    "per_page": 25,
    "total_items": 0,
    "total_pages": 0
  }
}
```

### Get one sanction

```http
GET /sanctions/{sanction_id}
```

Reconcile the owning Player before returning the detail. A nonexistent identifier
returns `404 sanction_not_found`.

### Replace editable sanction fields

```http
PUT /sanctions/{sanction_id}
Content-Type: application/json
```

Request:

```json
{
  "title": "Updated title",
  "reason": "Corrected disciplinary description.",
  "suspension_matches": 4
}
```

Rules:

1. Reject unknown, missing, malformed, or non-object input using existing conventions.
2. Reconcile before authorization and lifecycle validation.
3. Only an `active` sanction may be edited. `served` or `annulled` returns
   `409 sanction_not_active`.
4. Administrators may edit any active sanction.
5. The creating referee may edit only while the source Match remains `in_progress`.
   Other referees receive `403 sanction_owner_required`.
6. Title and reason may change while the sanction remains active.
7. Duration may change within 1–10 only when the sanction has no consumption records.
8. After any consumption, submitting the unchanged duration is allowed but changing it
   returns `409 sanction_duration_locked`.
9. With zero consumption records, set both `suspension_matches` and
   `remaining_matches` to the new duration atomically.
10. Player, source Match, effective time, frozen Team snapshot, creator, status,
    consumption history, and creation time are immutable through this sanction endpoint.
    This does not freeze the Teams themselves: they may manage other Players and enter
    Competitions under the rules in Specification 2.
11. Set `updated_by_user_id` to the acting User and update `updated_at`.

The response is `200` with current sanction detail.

### Annul an active sanction

```http
PATCH /sanctions/{sanction_id}/annul
```

This operation has no request body and is administrator-only.

Rules:

1. Reconcile first.
2. Only `active` may transition to `annulled`.
3. A partially consumed active sanction may be annulled.
4. A `served` sanction cannot be annulled and returns `409 sanction_already_served`.
5. Repeated annulment returns `409 sanction_already_annulled`; do not silently treat it
   as a new action.
6. Preserve `remaining_matches` and all consumption records.
7. Set `status`, `annulled_at`, `annulled_by_user_id`, `updated_at`, and
   `updated_by_user_id` atomically.
8. Annulment immediately stops future consumption and removes Team/roster enrollment
   blocks when no other active sanction exists.

Return `200` with the annulled sanction detail. Never physically delete sanctions or
their consumption history.

## Sanction response shape

Use Pydantic v2 response models and avoid raw model serialization. The detail/list item
must expose:

- `id`;
- Player summary: `id`, `name`;
- `title`, `reason`;
- `suspension_matches`, `remaining_matches`;
- `status`;
- source Match summary: `id`, `starts_at`, `ends_at`;
- source Competition summary obtained through the Match: `id`, `name`;
- frozen Team summaries: `id`, `name`, in stable identifier order;
- creator summary: `id`, `name`;
- updater summary: `id`, `name`;
- annuller summary when present: `id`, `name`;
- `effective_at`, `created_at`, `updated_at`, `annulled_at`, `served_at`.

Do not expose User email, JWT data, internal normalized fields, database errors, or
unnecessary relationship internals. Consumption history is retained in PostgreSQL for
integrity and auditing but is not exposed by the endpoints in these specifications.

Creation from Specification 1 must use this same response model.

## Service and query requirements

- Keep normalization and validation shared with creation; do not duplicate title/reason
  rules in routes.
- Keep route functions limited to HTTP parsing, exception mapping, and response shaping.
- Eager-load Player, source Match/Competition, frozen Teams, and audit User summaries in
  bounded queries. Avoid per-row relationship loads on the 25-item list.
- Count and page in SQL. Do not load all sanctions and slice in Python.
- Perform reconciliation and each edit/annul operation within explicit, rollback-safe
  transactions.
- Lock the Player/sanction in the same deterministic order used by Specification 2.
- Treat database timestamps as timezone-aware and preserve the project UTC behavior.
- Do not add dependencies.

## Error behavior

Document stable safe errors, including:

| Condition | Status | Code |
| --- | --- | --- |
| Player missing on nested list | `404` | `player_not_found` |
| Sanction missing | `404` | `sanction_not_found` |
| Unsupported role | `403` | Existing role error |
| Referee is not creator | `403` | `sanction_owner_required` |
| Referee edit window ended | `409` | `sanction_edit_window_closed` |
| Closed sanction edit | `409` | `sanction_not_active` |
| Duration already consumed | `409` | `sanction_duration_locked` |
| Served sanction annulment | `409` | `sanction_already_served` |
| Repeated annulment | `409` | `sanction_already_annulled` |
| Invalid date range or request contract | `422` | Existing validation format |
| Database unavailable | `503` | `service_unavailable` |

Missing authentication remains `401`. No response may contain stack traces or raw SQL.

## Required tests

### Authorization

- administrators and referees can list Players and get Player detail;
- referees still cannot create/update/enable/disable Players or manage photos through
  administrator-only endpoints;
- both roles can list/get sanctions;
- administrator edit/annul succeeds under valid lifecycle conditions;
- creator referee can edit only during the in-progress source Match;
- non-creator referee and creator after Match completion are rejected;
- referee cannot annul.

### Query contract

- status filters for all three values;
- omitted status returns all states;
- inclusive `created_from` and `created_to` boundaries;
- aware-datetime requirement and reversed-range rejection;
- strict positive page and unknown-query-field rejection;
- fixed 25-item page, total counts/pages, stable newest-first tie-break;
- nonexistent Player and empty/out-of-range pages;
- list query has bounded eager loading and no N+1 relationship access.

### Editing and annulment

- whitespace/length/duration validation matches creation;
- title/reason edit before and after partial consumption while active;
- duration update before first consumption adjusts original and remaining values;
- duration change after first consumption is rejected without partial writes;
- immutable fields cannot be supplied;
- served and annulled edits are rejected;
- partial sanction can be annulled while preserving remaining count and history;
- served sanction and repeated annulment are rejected;
- audit User/timestamps update correctly;
- annulment releases Team/roster guards only when no other active sanction remains;
- database failure rolls back every lifecycle/audit change.

### API and documentation

- missing/malformed/non-object JSON and unknown fields;
- response models include no email or internal fields;
- OpenAPI includes all operations, security, request/response schemas, filters, and
  documented errors;
- generated OpenAPI drift test passes.

## Documentation and completion checklist

- Update `documentation.md` with the full creation, global consumption, accumulation,
  enrollment, query, edit, annulment, authorization, payload, and error flows.
- Update `readme.md` only for installation/migration commands and its concise implemented
  feature inventory. Keep detailed API examples in `documentation.md`.
- Extend the existing CORS configuration for `/sanctions(?:/.*)?`; the existing
  `/players` and `/matches` rules already cover the other sanction routes.
- Ensure `docs/erd.puml` exactly matches all implemented sanction entities and audit
  relationships from the three specifications.
- Regenerate, never hand-edit, `docs/openapi.json` after registering every endpoint and
  authorization change.
- Run relevant sanction, Player, Team, Competition, Match, authorization, migration,
  PostgreSQL concurrency, and OpenAPI tests.
- Inspect the complete three-commit diff and verify no secrets, sensitive images,
  unrelated refactors, disabled checks, or unsupported technologies were added.

## Acceptance criteria

- Both supported roles can safely read Players and paginated sanctions.
- Sanction lists remain separate from Player payloads and support approved state/date
  filters.
- Authorized edits obey lifecycle, ownership, Match window, and duration-lock rules.
- Administrator annulment preserves history and immediately affects future writes.
- Closed sanctions cannot be mutated or deleted through public APIs.
- API behavior, tests, OpenAPI, PlantUML, `documentation.md`, and `readme.md` are
  synchronized.
