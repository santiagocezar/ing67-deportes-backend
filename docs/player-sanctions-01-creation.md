# Player Sanctions — Specification 1: Match-Origin Creation

## Status and delivery boundary

- Status: approved for implementation.
- Depends on: the implemented Player, Team, Competition roster, Match, User, and
  authorization domains.
- Followed by:
  - `player-sanctions-02-consumption.md` for global suspension consumption and
    enrollment restrictions;
  - `player-sanctions-03-management.md` for reads, editing, annulment, and the
    complete management API.
- Target implementation size: 300–900 changed source/test/documentation lines.
  Generated `docs/openapi.json` lines do not count toward this estimate.
- Suggested commit: `feat(sanctions): add match-origin sanction creation`

This specification is backend-only. It does not implement facial recognition,
pre-match scanning, match-day lineups, cards, expulsions, results, or frontend UI.

## Goal

Allow an administrator or the referee assigned to an in-progress Match to create a
global Player suspension from that Match. The suspension starts after the source
Match ends and initially applies to a frozen snapshot of the Player's Teams.

## Domain decisions

1. A sanction belongs to one Player and records one source Match.
2. The source Match indirectly and unambiguously identifies the source Competition.
   Do not duplicate `competition_id` in the sanction table.
3. A sanction may be created only while the source Match is `in_progress`.
4. The source Match never consumes one of the suspension Matches.
5. The sanction becomes effective after `source_match.ends_at`. A Match qualifies only
   when its `starts_at` is strictly later than that timestamp; equality does not qualify.
6. The sanctioned Player must appear in the historical Competition roster of one of
   the two Teams in the source Match.
7. The Player may be enabled or disabled at creation time. Historical roster
   membership, rather than current administrative state, determines validity.
8. An administrator may create a sanction in any in-progress Match.
9. A referee may create a sanction only when `match.referee_id` equals the authenticated
   User identifier.
10. Multiple sanctions may originate from the same Match for the same Player.
11. Every sanction is created with status `active`.
12. `suspension_matches` is an integer from 1 through 10 inclusive.
13. The title and reason are free text, required after whitespace normalization, and
    limited to 30 and 250 characters respectively.
14. Unknown request fields are rejected.
15. The frozen Team set is the union of:
    - all general Teams currently associated with the Player; and
    - the Team represented by the Player in the source Match roster.
16. Frozen Teams remain historical even if the Player association is later removed or
    either entity is disabled.

## Persistence model

Add the following SQLAlchemy models to `app/models.py`. The migration and
`docs/erd.puml` must represent the same structure.

### `player_sanctions`

| Column | Type and nullability | Rule |
| --- | --- | --- |
| `id` | integer primary key | Generated identifier |
| `player_id` | integer, not null, FK to `players.id` | `ON DELETE RESTRICT` |
| `source_match_id` | integer, not null, FK to `matches.id` | `ON DELETE RESTRICT` |
| `title` | varchar(30), not null | Trimmed, non-blank |
| `reason` | varchar(250), not null | Trimmed, non-blank |
| `suspension_matches` | integer, not null | Between 1 and 10 |
| `remaining_matches` | integer, not null | Initially equals `suspension_matches`; between 0 and the original duration |
| `status` | varchar(20), not null | `active`, `served`, or `annulled` |
| `effective_at` | timezone-aware datetime, not null | Snapshot of `source_match.ends_at` |
| `created_by_user_id` | integer, not null, FK to `users.id` | `ON DELETE RESTRICT` |
| `updated_by_user_id` | integer, not null, FK to `users.id` | Initially equals the creator |
| `annulled_by_user_id` | integer, nullable, FK to `users.id` | Null until annulment |
| `created_at` | timezone-aware datetime, not null | Database-generated |
| `updated_at` | timezone-aware datetime, not null | Database-generated and updated on writes |
| `annulled_at` | timezone-aware datetime, nullable | Null until annulment |
| `served_at` | timezone-aware datetime, nullable | Null until fully consumed |

Required constraints and indexes:

- Check trimmed `title` and `reason` are not blank.
- Check `suspension_matches BETWEEN 1 AND 10`.
- Check `remaining_matches BETWEEN 0 AND suspension_matches`.
- Check the status vocabulary.
- Check lifecycle consistency:
  - `active`: positive remaining value, `served_at` and annulment fields are null;
  - `served`: zero remaining value, `served_at` is non-null, annulment fields are null;
  - `annulled`: `annulled_at` and `annulled_by_user_id` are non-null and
    `served_at` is null.
- Add a unique constraint on `(id, player_id)` for the composite consumption FK added
  by Specification 2.
- Add an index supporting Player/status/newest-first access, using
  `(player_id, status, created_at)` unless migration inspection demonstrates that a
  narrower existing index is sufficient.

Do not add a physical-delete endpoint or cascading Player/Match/User deletion.

### `player_sanction_teams`

| Column | Type and nullability | Rule |
| --- | --- | --- |
| `sanction_id` | integer, PK component, FK to `player_sanctions.id` | Cascade only when the owning sanction row is physically removed by migration/test cleanup |
| `team_id` | integer, PK component, FK to `teams.id` | `ON DELETE RESTRICT` |

The composite primary key prevents duplicate frozen Teams. The application has no
physical sanction deletion behavior.

Add explicit bidirectional relationships for Player sanctions, source Match sanctions,
audit Users, and frozen Teams. Relationships with multiple User foreign keys must set
`foreign_keys` explicitly.

## Public contract

### Create a sanction

```http
POST /matches/{match_id}/players/{player_id}/sanctions
Authorization: Bearer <access-token>
Content-Type: application/json
```

Request:

```json
{
  "title": "Serious misconduct",
  "reason": "The player committed a disciplinary offense.",
  "suspension_matches": 3
}
```

Success: `201` with the sanction detail response defined in Specification 3. The
creation response must already expose at least the final fields required there; do not
introduce a temporary response shape that will be broken by a later commit.

The response exposes the source Competition as a nested summary obtained through the
source Match. It must not imply that the sanction is scoped only to that Competition.

### Error behavior

Use the existing safe error envelope and established HTTP semantics:

| Condition | Status | Stable code |
| --- | --- | --- |
| Missing, malformed, or non-object JSON | `400` | Existing JSON error convention |
| Contract failure or unknown field | `422` | Existing validation convention |
| Player does not exist | `404` | `player_not_found` |
| Match does not exist | `404` | `match_not_found` |
| Player is absent from both source Match rosters | `409` | `player_not_in_match_roster` |
| Match is not in progress | `409` | `match_not_in_progress` |
| Referee is not assigned to the Match | `403` | `match_referee_required` |
| Unsupported authenticated role | `403` | Existing application-role convention |
| Database unavailable | `503` | `service_unavailable` |

Do not expose SQL details or stack traces.

## Service and transaction requirements

Create focused sanction schema, route, and service modules following
route → service → model.

The service must, in one transaction:

1. Parse and normalize title/reason without changing their intended internal spacing
   beyond the repository's established whitespace normalization pattern.
2. Lock the source Match and Player in a deterministic order.
3. Reconcile the source Match status using the established time-derived mechanism.
4. Verify the Match remains `in_progress` and has a non-null schedule.
5. Find exactly one source roster entry for the Player among the Match Teams.
6. Enforce administrator/assigned-referee authorization using trusted JWT identity and
   role claims.
7. Lock and read current Team associations.
8. Build the frozen Team union and persist the sanction, audit fields, and Team snapshot.
9. Commit atomically.

Any failure rolls back the transaction. Do not log request text, raw database errors,
or unrelated sensitive information.

The route may translate known service exceptions but must not contain roster,
authorization, or snapshot business logic.

## Required tests

Add focused schema, service, route, and migration coverage:

- title/reason boundary lengths, blank-after-trim values, strict integer duration,
  duration 0/11, booleans, and unknown fields;
- malformed, missing, and non-object JSON;
- successful administrator creation;
- successful assigned-referee creation;
- refusal for an unassigned referee;
- refusal for `incomplete`, `scheduled`, and `finished` Matches;
- Player missing from both Match rosters;
- disabled Player accepted when present in the historical roster;
- frozen Team union includes the source Team and removes duplicates;
- multiple sanctions for the same Player and source Match are allowed;
- source Match does not reduce the initial remaining count;
- transaction rollback leaves no sanction or snapshot rows after failure;
- database checks and foreign keys exist in PostgreSQL migration inspection;
- authentication and unsupported-role responses;
- generated OpenAPI operation/security/response coverage and drift check.

Use synthetic records only. No facial images or biometric data are involved.

## Documentation and completion checklist

- Register the new blueprint and include its URL patterns in CORS only if the existing
  `/matches` rule does not already cover the nested route.
- Update `docs/erd.puml` with implemented entities, columns, constraints, and
  relationships only.
- Document creation, authorization, validation, and errors in `documentation.md`.
- Update `readme.md` only where its migration/setup or implemented-feature inventory
  requires it; do not duplicate API documentation there.
- Regenerate, never hand-edit, `docs/openapi.json` in this same implementation.
- Run relevant unit/API tests, migration tests against real PostgreSQL where configured,
  and OpenAPI drift checks.
- Inspect the diff and verify that no secret, real biometric data, or unrelated refactor
  was introduced.

## Acceptance criteria

- An authorized actor can create a 1–10 Match global sanction from an in-progress Match.
- The source Player/Match/Team context and creator audit are durable.
- The source Match is excluded, and only Matches starting strictly after its end
  timestamp may consume the sanction.
- The frozen Team snapshot is correct and immutable through supported APIs.
- Invalid state, roster, authorization, and payload cases fail predictably without
  partial writes.
- Models, migration, PlantUML, public documentation, and generated OpenAPI agree.
