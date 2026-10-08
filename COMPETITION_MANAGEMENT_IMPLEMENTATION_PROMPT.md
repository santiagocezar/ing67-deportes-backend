# Implement Competition and Match Management

Implement this backend feature now. Read `AGENTS.md` completely first, inspect the current repository and reuse its established Flask, SQLAlchemy, Pydantic v2, authorization, error, transaction, testing, migration and documentation patterns.

## Scope and constraints

- Backend only. Do not read, create or modify frontend code.
- Keep the change cohesive, concise and production-quality. Avoid unrelated refactors, unnecessary abstractions, dependencies and comments.
- Use English identifiers and `/competitions` and `/matches` routes.
- Do not implement progressive enrollment, phases, manually selected matchups, points, standings, results, cards, expulsions, suspensions, images, facial recognition or competition transfers.
- Do not add a background scheduler. Reconcile time-derived statuses on relevant reads and writes as described below.
- If the repository reveals a genuine conflict not resolved here, stop and ask one precise question before editing.

## Domain model

Add the smallest clear SQLAlchemy model set for the following persisted domain. Use explicit names for constraints and justified foreign-key/filter indexes.

### Competition

- Generated integer `id`.
- `name`: required, whitespace-compacted, nonblank, at most 100 characters. Duplicate names are allowed.
- Internal normalized name for accent/case-insensitive search; it is neither accepted nor exposed.
- Required `sport_id` referencing an existing Sport with restricted deletion.
- `gender`: only `male` or `female`.
- Timezone-aware `starts_at` and `ends_at`; store consistently in UTC.
- `status`: `scheduled`, `in_progress`, `finished` or `discarded`.
- `is_enabled`, `created_at` and nullable `disabled_at`, following the existing Team/Player convention.
- Do not persist `team_capacity`, `team_count` or `phase`. Return `team_count` as the count of registered Teams.

### Registration snapshot

Persist Competition-Team, Competition-roster and Competition-referee associations. They are historical snapshots, not the mutable general Team membership.

- A Competition contains one even number of distinct Teams from 4 through 16, all supplied at creation.
- It contains exactly `team_count / 2` distinct existing Users whose role is `referee`.
- Each Team registration contains `team_id` and its complete `player_ids` snapshot.
- Enforce at the database level that a Team and referee belong to the referenced Competition where practical.
- Enforce uniqueness of `(competition_id, player_id)` so a Player cannot represent two Teams in one Competition.
- General Team/Player changes after registration never alter the snapshot.
- A Team may participate in multiple, even overlapping, Competitions.

### Match

- Generated integer `id`.
- Required Competition, two distinct registered Teams and one registered referee.
- Read-only `round_number` from 1 through 3. Do not add `phase`.
- Nullable timezone-aware `starts_at` and `ends_at`; both must be null or both present.
- `status`: `incomplete`, `scheduled`, `in_progress` or `finished`.
- Prevent duplicate pairings within a Competition. Team order has no home/away meaning; normalize it consistently if useful for the constraint.
- A Team appears exactly once per round and faces three different opponents overall.
- Each selected referee handles exactly one Match per round and three Matches overall.

## Creation and registration rules

`POST /competitions` is administrator-only and atomically accepts exactly:

```json
{
  "name": "Tournament 2026",
  "sport_id": 1,
  "gender": "male",
  "starts_at": "2026-10-01T18:00:00-03:00",
  "ends_at": "2026-11-30T23:00:00-03:00",
  "teams": [
    {"team_id": 1, "player_ids": [10, 11, 12]}
  ],
  "referee_ids": [30, 31]
}
```

Validate the complete payload before committing:

- Reject naive datetimes. `ends_at` must be strictly after `starts_at` and strictly in the future. `starts_at` may be past or future.
- Use the stable `competition_date_range_invalid` error code, associate the detail with `ends_at`, and use the message `Competition end date must be later than its start date.` when the range is reversed or equal.
- Team count must be even and between 4 and 16; IDs must be distinct.
- Every Team must exist, be enabled, and match the Competition Sport and gender.
- Every selected Player must exist, be enabled, currently belong to that Team, and match its Sport and gender.
- Each roster must contain between `sport.max_players_in_game` and `sport.max_players` distinct Players.
- A Player may appear in only one roster in the Competition.
- Referee IDs must be distinct, exist, have role `referee`, and total exactly half the Team count.
- Roll back everything if any validation or database write fails.
- Initial status is `scheduled` when now is before `starts_at`; otherwise it is `in_progress`.

Provide one concise atomic correction endpoint, `PUT /competitions/{competition_id}/participants`, receiving the complete `teams` and `referee_ids` collections. It may replace the registration snapshot only while the Competition is enabled, has not ended and has no Matches. Preserve the original Team count and reapply every creation validation. Do not expose progressive or individual roster mutation endpoints.

Once any Match exists, Teams, rosters, referees, Sport, gender and Team count are permanently immutable. Later disabling a registered Player preserves the snapshot. Later disabling a Team preserves all historical data but prevents randomization or scheduling involving it until it is enabled again.

## Competition operations

Implement:

| Method | Route | Authorization | Behavior |
| --- | --- | --- | --- |
| `POST` | `/competitions` | Administrator | Atomic creation with all participants |
| `GET` | `/competitions` | Administrator or referee | Filtered, stable, paginated list |
| `GET` | `/competitions/{competition_id}` | Administrator or referee | Detail with Team rosters and referees |
| `PUT` | `/competitions/{competition_id}` | Administrator | Replace only `name`, `starts_at`, `ends_at` |
| `PUT` | `/competitions/{competition_id}/participants` | Administrator | Correct the complete registration before Matches exist |
| `PATCH` | `/competitions/{competition_id}/disable` | Administrator | Idempotent soft disable |
| `PATCH` | `/competitions/{competition_id}/enable` | Administrator | Idempotent re-enable when allowed |

The current API has no way for an administrator to discover referee IDs. Add the minimal supporting `GET /referees` endpoint, administrator-only, returning paginated `{id, name, email}` summaries for Users whose role is `referee`. Support `search` by normalized name or email and `page`, use the existing fixed page size, and never expose password or session data.

`PUT /competitions/{id}` requires all three editable fields and works only for an enabled Competition with no Matches. Revalidate timezone, range and future end. All other fields are immutable.

Disabling is allowed only before any Match exists. It sets `is_enabled=false`, `disabled_at=now` and `status=discarded` without deleting snapshots. Re-enabling is allowed only when no Match exists and `ends_at` is still future; it clears `disabled_at` and recalculates `scheduled` or `in_progress`. A Competition automatically discarded after ending cannot be re-enabled.

The list uses the existing fixed page size of 25 and supports `search`, `sport_id`, `gender`, `lifecycle_status`, `availability` (`enabled`, `disabled`, `all`, default `enabled`), `starts_from`, `starts_to`, `sort` (`starts_at_asc` or `starts_at_desc`) and `page`. Filters combine with AND. An out-of-range page returns an empty list with real totals.

## Random schedule

Implement administrator-only `POST /competitions/{competition_id}/matches/randomize`.

- Require an enabled, non-ended Competition whose currently registered Teams are enabled and selected Users are still referees.
- Randomly shuffle Teams, then use a finite round-robin/circle algorithm to generate exactly three rounds. Do not use retry-until-valid random pairing.
- Each round contains `team_count / 2` Matches; total Matches are `3 * team_count / 2`.
- Every Team appears once per round and faces no opponent twice.
- Independently shuffle the registered referees per round and assign each exactly once in that round.
- Create all Matches atomically with null datetimes and `incomplete` status.
- If no Matches exist, generate them. If all existing Matches are still incomplete, atomically replace the complete draw, including referee assignments. If any Match is scheduled or has started, return a conflict and change nothing.
- Serialize randomization against concurrent registration, state and Match mutations. Tests must be deterministic without weakening production randomness.

## Match operations and scheduling

Implement:

| Method | Route | Authorization | Behavior |
| --- | --- | --- | --- |
| `GET` | `/competitions/{competition_id}/matches` | Administrator or referee | All Matches ordered by round and stable ID |
| `GET` | `/matches/{match_id}` | Administrator or referee | Match detail |
| `PUT` | `/matches/{match_id}` | Administrator | Set or replace exactly `starts_at` and `ends_at` |

There is no manual Match creation or Match deletion endpoint. Responses must show Competition reference, round, schedule, status, assigned referee, both Teams and each Team's frozen Competition roster.

Scheduling rules:

- Require both timezone-aware datetimes, `ends_at > starts_at`, and `starts_at > now`; retroactive scheduling is forbidden.
- The complete half-open Match interval `[starts_at, ends_at)` must fit inside the Competition interval. Boundary-touching is allowed.
- An incomplete Match may be scheduled. A scheduled Match may be changed only before its existing start. Started or finished Matches are immutable.
- The Competition and both Teams must be enabled and the Competition must not be finished or discarded.
- Reject overlapping half-open intervals for either Team or the referee across all Competitions; adjacent intervals are allowed.
- Protect overlap checks from cross-Competition races by locking shared Team/User resources in deterministic order. Exclude the current Match during an update and roll back on failure.

## Time-derived states

Centralize a timezone-aware UTC clock so tests can control it. Do not add a scheduler.

Before relevant Competition/Match reads and writes, reconcile and persist stale states efficiently:

- Match: null schedule -> `incomplete`; before start -> `scheduled`; `starts_at <= now < ends_at` -> `in_progress`; at/after end -> `finished`.
- Enabled Competition: before start -> `scheduled`; inside its interval -> `in_progress`.
- At/after Competition end, set `finished` only when exactly `3 * team_count / 2` Matches exist and every one is completely and validly scheduled. Otherwise set `discarded`, set `is_enabled=false`, and use the Competition end as `disabled_at`.
- Manually disabled Competitions remain `discarded` until validly re-enabled.

The API must never return a stale status after reconciliation. Avoid N+1 queries and unnecessary commits, especially on list operations.

## API behavior

- Use Pydantic v2 request/path/query/response models and forbid unknown request fields.
- Preserve existing behavior: missing, malformed, incorrectly declared or non-object JSON -> `400`; valid objects failing contracts -> structured `422` without echoing inputs.
- Use `401` for authentication, `403` for roles, `404` for missing resources, `409` for state/business conflicts and safe `503` responses for database failures.
- Add stable, specific English error codes for disabled resources, participant incompatibility, roster limits, forbidden mutations, draw state and schedule overlap. Never expose raw database errors.
- Authenticate before processing protected request bodies.
- All writes spanning validation/associations are atomic and roll back on every failure.
- Register the blueprints, CORS paths and OpenAPI tags/operations. Competition and Match GET operations accept both current roles; mutations and the referee lookup remain administrator-only.

## Persistence, documentation and tests

- Add one reviewed Alembic migration following the current head. Include all tables, foreign keys, checks, uniqueness constraints and justified indexes; provide a safe downgrade.
- Update Sport deletion conflict handling for Competition references.
- Update `docs/erd.puml`, `documentation.md`, application API summary and the applicable Competition section of `AGENTS.md` so implemented behavior is no longer described as deferred. Update `readme.md` only if commands/setup change.
- Regenerate `docs/openapi.json` through the existing command; never hand-edit it.
- Add focused tests using current patterns. Cover schemas, authorization for both roles, safe referee lookup, atomic creation and rollback, every eligibility/roster/referee rule, immutable fields, soft-state transitions, status reconciliation at boundaries, all even Team counts 4-16, random schedule invariants, safe rerandomization, date/range errors, global Team/referee overlaps, concurrency protections, missing/unknown/malformed input, database errors, response allowlists, migration metadata and OpenAPI drift.
- Do not weaken or delete existing tests. Run the relevant test suite and configured quality checks, inspect the final diff and report only commands actually run and their real results.

Finish with a concise per-file summary, verification results and any genuine remaining risk. Do not commit or push.
