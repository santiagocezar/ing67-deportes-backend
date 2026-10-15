# Player Sanctions — Specification 2: Global Consumption and Enrollment Guards

## Status and delivery boundary

- Status: approved for implementation after Specification 1.
- Prerequisite: `player-sanctions-01-creation.md` is implemented and passing.
- Followed by: `player-sanctions-03-management.md`.
- Target implementation size: 300–900 changed source/test/documentation lines,
  excluding generated `docs/openapi.json`.
- Suggested commit: `feat(sanctions): reconcile global suspension consumption`

This specification implements the suspension counter and write-time enrollment guards.
It does not implement scanning, recognition, lineup selection, or frontend behavior.

## Goal

Reliably consume active sanctions from completed Matches involving the sanction's frozen
Teams, across every Competition, while serializing multiple sanctions and preserving a
complete audit of which Match consumed each suspension unit.

## Qualifying Match rules

A Match participates in a qualifying group for a sanction only when all of the following
are true:

1. Its identifier differs from the sanction's source Match.
2. At least one side is in the sanction's frozen Team snapshot.
3. `starts_at > sanction.effective_at`.
4. It has a complete schedule. The group becomes consumable only when every Match in it
   has `ends_at <= now`, regardless of whether a persisted Match status was stale before
   reconciliation.
5. The group's canonical Match has not already consumed any sanction for the same Player.

Additional decisions:

- A Match between two frozen Teams consumes exactly one suspension unit.
- Any number of qualifying Matches in the same overlapping-time group consume one unit
  in total.
- Consumption does not depend on Player attendance, Match roster membership, scanning,
  or current Player/Team enabled state.
- Removing a current Team association does not alter the frozen Team snapshot.
- Disabling a Player or Team does not stop consumption from already frozen Teams.
- Only completed Matches consume units; scheduled or in-progress Matches do not reserve
  future units.

## Overlapping Match groups

Treat Match intervals as half-open ranges `[starts_at, ends_at)`. Two Matches overlap
when each starts before the other ends. Adjacent Matches, where one starts exactly when
the other ends, do not overlap.

Build deterministic connected groups ordered by `starts_at, ends_at, id`: a Match joins
the current group when its start is earlier than the greatest end already present in
that group. This means a chain such as A overlapping B and B overlapping C is one group,
even when A does not directly overlap C.

Each group consumes one suspension unit only after every Match in that group has
finished. Use the first Match by `starts_at ASC, ends_at ASC, id ASC` as the canonical
representative stored in the consumption row. The group's effective completion time is
its greatest `ends_at`; use that timestamp when the group serves a sanction. This
canonical rule prevents request or query order from changing the result.

## Consecutive accumulation

Multiple sanctions for one Player are cumulative and served consecutively.

1. Order non-annulled, unserved sanctions by `created_at ASC, id ASC`.
2. Only the oldest active sanction may consume Matches.
3. A newer sanction cannot consume while an older sanction remains active, even when a
   Match would qualify for the newer sanction but not for the older one.
4. When the oldest sanction reaches zero, set it to `served` and continue with the next
   sanction using only later qualifying Matches.
5. One overlapping-time group never spills into two sanctions. The group that serves the
   last unit of one sanction cannot also serve the first unit of the next sanction.
6. For each sanction after the first, its queue activation boundary is the later of:
   - its own `effective_at`; and
   - the preceding sanction's `served_at`.
7. A Match for a queued sanction must have `starts_at >` that activation boundary.
8. When a sanction is completed, `served_at` is the greatest `ends_at` in its final
   consumption group, not the later time when reconciliation happened.

Annulled sanctions are skipped and never consume additional Matches. Their previous
consumption records remain historical.

## Persistence model

Add a reviewed migration and SQLAlchemy model for
`player_sanction_match_consumptions`.

| Column | Type and nullability | Rule |
| --- | --- | --- |
| `sanction_id` | integer, PK component | Part of composite FK to the sanction and Player |
| `player_id` | integer, not null | Denormalized only to enforce one consumption per Player/canonical Match |
| `match_id` | integer, PK component, FK to `matches.id` | Canonical representative of the consumed overlap group; `ON DELETE RESTRICT` |
| `consumed_at` | timezone-aware datetime, not null | Database insertion/audit time |

Required integrity:

- Composite FK `(sanction_id, player_id)` references
  `player_sanctions(id, player_id)` with `ON DELETE CASCADE` only for migration/test
  cleanup of the owning sanction.
- Unique `(player_id, match_id)` guarantees that one canonical Match consumes at most one
  sanction unit for a Player, including under concurrent reconciliation.
- Primary key `(sanction_id, match_id)` prevents duplicate application to one sanction.
- Indexes must support lookups by Player and Match without duplicating indexes already
  supplied by the primary/unique constraints.

The additional `player_id` is intentional integrity data, not a second source of truth
for sanction ownership. The composite FK prevents mismatch.

## Reconciliation service

Implement a dedicated service operation that can reconcile one Player and a batched
variant for sorted unique Player identifiers. It must be safe to call repeatedly.

For each Player, in one transaction scope:

1. Lock the Player row to serialize sanction reconciliation and Team-membership writes.
2. Lock relevant sanctions ordered by `created_at, id`.
3. Read existing Player consumption Match identifiers once.
4. Skip `served` and `annulled` sanctions while retaining their history.
5. For the oldest active sanction, query distinct scheduled candidate Matches using its
   frozen Teams and strict effective boundary, including unfinished candidates needed to
   determine complete overlap groups.
6. Build the deterministic overlapping-time groups, retain only groups whose greatest
   end is not later than `now`, and discard groups whose canonical Match was already
   consumed for the Player.
7. Insert at most `remaining_matches` canonical consumption rows.
8. Decrement once per inserted group, never once per Match inside the group.
9. On zero, set `status='served'`, set `served_at` to the greatest Match end in the final
   consumed group, update audit timestamps without inventing an acting User, and evaluate
   the next queued sanction.
9. Commit only in the caller's transaction boundary.

Sort completed groups by their greatest `ends_at ASC`, then canonical Match identifier
`ASC`. Use SQL joins/`IN` predicates and batched loading; do not issue one query per
frozen Team or per Match.

Reconciliation must run before any operation that requires current sanction state:

- sanction creation, reads, editing, or annulment;
- adding Team associations to a Player;
- creating or replacing Competition participants;
- future eligibility checks, outside the current implementation scope.

It does not need to run on every general Match read. There is no scheduler or background
worker: elapsed time alone may leave persisted states stale until one of these relevant
operations occurs, matching the project's existing reconciliation approach.

If two requests reconcile the same Player concurrently, row locking plus database
uniqueness must produce one canonical history record per Player/overlap group and no lost
decrement.
Known integrity races may be retried only if the transaction is fully rolled back first;
otherwise return the established safe database error.

## General Team-association guard

An active sanction blocks adding or re-adding the Player to a general Team.
It does not block those Teams from adding, removing, or updating other Players under the
existing Sport, gender, enabled-state, and capacity rules.

Apply this rule in the existing Player membership write service:

1. Reconcile the Player's sanctions before validating the requested replacement set.
2. Compare current Team identifiers with requested identifiers.
3. If the request introduces any new identifier while at least one sanction remains
   `active`, reject the entire update with `409 player_active_sanction`.
4. Allow requests that preserve the same Teams or remove one or more Teams.
5. Never restore removed associations automatically when the sanction becomes served or
   annulled.

Player creation requires no additional check because a new Player cannot already own a
sanction. Player disable behavior remains unchanged: it may remove current Team
associations, while frozen sanction Team rows remain.

Lock ordering must remain deterministic and compatible with existing Player/Team locks.

## Competition-roster guard

An active sanction blocks adding the Player to any new or replacement Competition roster.
It does not block a frozen Team from registering for another Competition when the Team
submits a valid roster that excludes the sanctioned Player.

In participant creation/replacement:

1. Preserve all existing Sport, gender, enabled-state, membership, capacity, and unique
   Team-per-Competition validations.
2. Reconcile selected Players in a batch before the final participant write.
3. If any selected Player has an active sanction, reject the entire atomic operation with
   `409 player_active_sanction`.
4. Do not modify historical rosters that already contain a subsequently sanctioned
   Player.
5. A `served` or `annulled` sanction does not block enrollment.
6. Do not reject a Team or Competition merely because another Player associated with the
   Team is sanctioned; validate each proposed roster Player individually.

Avoid N+1 queries: selected Player identifiers are already collected and sorted by the
competition participant service and should be reused.

## Required tests

### Consumption behavior

- source Match never consumes;
- exact `starts_at == effective_at` boundary does not consume;
- a Match starting before effectiveness never consumes even if it ends afterward;
- scheduled and in-progress Matches do not consume;
- finished Match of either frozen Team consumes one unit;
- Match between two frozen Teams consumes once;
- two or more overlapping qualifying Matches consume once in total;
- transitively overlapping Match chains form one consumption group;
- adjacent non-overlapping Matches form separate consumption groups;
- canonical representative and greatest-end selection are deterministic;
- attendance, roster presence, Player enabled state, and Team enabled state do not affect
  consumption;
- deterministic group-end and canonical-Match ordering;
- `served_at` equals the greatest end time of the final consumption group;
- repeated reconciliation is idempotent;
- partial consumption remains auditable after annulment in Specification 3.

### Accumulation

- only the oldest active sanction consumes;
- newer sanction waits when only its Teams play;
- completion activates the next sanction at the correct boundary;
- final overlap group of one sanction does not also consume the next;
- annulled sanctions are skipped without losing consumption history;
- equal creation timestamps are ordered by identifier.

### Write guards

- adding or re-adding a Team with an active sanction returns `409`;
- preserving or removing existing Teams succeeds;
- disabled Player cleanup remains possible and snapshots survive;
- Competition creation and participant replacement reject active sanctioned Players;
- Teams containing sanctioned Players can enter Competitions with valid rosters that
  exclude those Players;
- unrelated Player membership and capacity-compliant Team management remain available;
- served and annulled sanctions permit Team and roster additions;
- participant failure rolls back every Team, roster, and referee change.

### PostgreSQL and concurrency

- inspect migration foreign keys, unique constraints, and indexes;
- concurrent reconciliation cannot create duplicate Player/Match consumption or decrement
  twice;
- concurrent reconciliation and Team-assignment writes serialize safely;
- failed insertion rolls back counter/status changes;
- migration upgrade and downgrade work on a real PostgreSQL test database where configured.

## Documentation and completion checklist

- Update `docs/erd.puml` with the consumption entity and integrity notes.
- Update `documentation.md` with global qualification, accumulation, reconciliation,
  disabled-state behavior, and both enrollment guards.
- Update `readme.md` only if its migration/feature inventory requires a synchronized note.
- Regenerate `docs/openapi.json` if existing operation descriptions or documented conflict
  behavior change; never hand-edit it.
- Keep generic `ErrorResponse` contracts consistent and update API contract tests for new
  documented `409` cases.
- Run Player, Competition, Match, sanction, PostgreSQL migration/concurrency, and OpenAPI
  checks relevant to this change.
- Inspect the diff for unrelated changes or sensitive data.

## Acceptance criteria

- Each distinct qualifying overlapping-time group consumes exactly one unit from the
  correct oldest sanction.
- Consumption is deterministic, idempotent, auditable, and safe under concurrency.
- Global behavior spans Competitions but only uses the immutable frozen Team set.
- Active sanctions block new Team associations and Competition roster enrollment without
  rewriting existing associations or historical rosters.
- Disabled Players/Teams and membership removals do not corrupt sanction history.
- Database model, migration, PlantUML, documentation, and API conflict behavior agree.
