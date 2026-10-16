# HU09 — Specification 1: Persistent Scan Foundation

## Delivery boundary

- Status: approved for implementation after the decisions recorded below.
- Target size: 350–750 changed source/test lines, excluding generated OpenAPI.
- Suggested commit: `feat(scans): plantel inmutable y persistencia`
- Depends on: implemented Matches, Competition roster snapshots, Player photos, and Player sanctions.
- Does not add a public scan-processing endpoint, facial encodings, matching, frontend, or manual verification.

Before implementation, read and follow `AGENTS.md` in full.

## Approved decisions

- HU09 is backend-only.
- A scan is allowed for an administrator, or for the referee assigned to the Match.
- Eligible Match windows are `in_progress`, or `finished` with `ends_at >= now - 2h`.
- A scan never fails solely because counts differ from `sport.max_players_in_game`.
- Persist a group image only when a completed scan contains at least one `unknown` face.
- Do not retain rejected/non-processable image bytes, face crops, or embeddings.

## Persistence

Add reviewed SQLAlchemy models, Alembic migration, and matching PlantUML changes.

### `match_roster_scans`

Store one immutable successful scan attempt:

- generated `id`; `match_id`, selected `team_id`, and `created_by_user_id` FKs with `RESTRICT` deletion behavior;
- `expected_players`, `detected_faces`, and `unique_recognized_players`, all non-negative; `expected_players` is the Sport's in-game capacity snapshot;
- nullable internal group-image filename and content type, paired by a database check;
- timezone-aware database-created timestamp.

The service, under locks, verifies that `team_id` is one of the Match sides. This cross-row invariant cannot be represented by the current Match schema as a simple FK. The group-image fields are null unless at least one persisted result is `unknown`; enforce that relationship transactionally, not with speculative triggers.

### `match_roster_scan_faces`

Store one result per detected face, ordered by a stable `face_index` within its scan:

- `scan_id` and `face_index` composite primary key;
- non-negative `top`, `right`, `bottom`, `left` box coordinates, with `right > left` and `bottom > top` checks;
- nullable recognized `player_id` with `RESTRICT` FK and nullable Player-name snapshot;
- nullable `match_percentage` integer from 0 through 100;
- one status: `declared_eligible`, `declared_sanctioned`, `team_member_undeclared`, `team_member_undeclared_sanctioned`, or `unknown`.

Database checks require Player and percentage fields for recognized statuses and require them to be null for `unknown`. UI colours are derived by clients: green, yellow, red, orange, and manual-review respectively; never persist presentation colours.

### `match_roster_scan_face_sanctions`

For each sanctioned recognized face, preserve every sanction relevant to the target Match: `scan_id`, `face_index`, `sanction_id`, plus immutable title, reason, and remaining-match-count snapshots. Use composite FKs to the face result and the existing sanction identity, and a composite primary key preventing duplicates. This prevents a history response from changing when a sanction is later edited or served.

Do not copy Player base photos. A later deletion/disable may make a current preview unavailable, but scan identity and sanction snapshots remain auditable.

## Private group-image storage

Add `MATCH_SCAN_PHOTOS_DIR` to `.env.example` and README with the same single, persistent-backend limitation as `PLAYER_PHOTOS_DIR`. Use generated UUID filenames, strict safe-path validation, staged writes, rollback compensation, and a stopped-process reconciliation command or extension of the existing reconciliation command. PostgreSQL stores only a reference and MIME type. No endpoint may expose an internal filename, ETag, filesystem timestamp, or public group image.

Storage changes must be transaction-safe: do not leave a file after a database rollback; if moving a committed staged file fails, compensate the database row and log only a safe operational error.

## Service boundary and tests

Create focused scan persistence/storage services. They accept already-computed immutable results; they do not contain detector, matcher, or sanction decisions. Lock the Match before inserting a scan and use the established safe database-error handling.

Cover migration constraints/FKs, both file-reference states, traversal/symlink defenses, rollback compensation, refusal to retain images without unknown results, immutable result insertion, and no embedding/raw-image logging. Update `docs/erd.puml`; defer OpenAPI until a public endpoint exists.

## Completion

The migration, models, PlantUML, storage configuration, and tests agree. No endpoint, frontend screen, manual-resolution behavior, or recognition parameter is added here.
