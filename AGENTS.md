# AGENTS.md — Sports Competition & Player Recognition Platform

> Read this before any task.
> If a request conflicts with a rule here, stop and ask.
> Keep changes small, explicit, testable, and aligned with the approved business model.

---

## Project

Web application for managing sports competitions, teams, players, rosters, matches,
disciplinary sanctions, and pre-match player verification using facial recognition.

Main users:

- **Administrator**: manages sports, competitions, teams, players, rosters, player photos,
  historical imports, configuration, and account approval.
- **Federation delegate**: manages Teams and reads Sport configuration; additional
  federation workflows remain future scope.
- **Referee**: manages assigned matches, performs pre-match group scans, reviews player
  eligibility, manually resolves recognition failures, and records/consults sanctions.
- **User**: pending public account with no access to business resources until approval.

Initial sports:
- Football
- Basketball

---

## Stack

### Frontend
- Vue
- TypeScript

### Backend
- Python
- Flask
- SQLAlchemy

### Database
- PostgreSQL

### Facial recognition
- `face_recognition`
- `cv2`
- `numpy`

Do not introduce additional frameworks, ORMs, migration tools, recognition libraries,
databases, queues, caches, or infrastructure unless explicitly approved.

---

## Repository structure

Backend structure:

```text
app/
├── app.py
├── models.py
├── routes/
├── services/
└── .env
```

Respect this structure unless the team explicitly changes it.

- `app.py`: Flask application creation/configuration.
- `models.py`: SQLAlchemy models and relationships.
- `routes/`: HTTP endpoints/controllers.
- `services/`: business logic and reusable application operations.
- `.env`: local environment configuration; never commit real secrets.

Do not invent paths. Verify a file/folder exists before editing it.

---

## Hard rules

1. **The current approved requirements and versioned schema are the data-model source
   of truth. Do not rely on obsolete DER versions.**
2. Do not add/remove entities, foreign keys, constraints, or relationships without approval.
3. Do not silently change business rules to simplify implementation.
4. Never store persistent business data only in Python memory.
5. PostgreSQL is the persistent data source.
6. The frontend never connects directly to PostgreSQL.
7. Business rules must be enforced in backend code and, where appropriate, by DB constraints.
8. Never commit `.env`, passwords, DB credentials, tokens, secrets, or real biometric data.
9. Never log passwords, password hashes, facial embeddings, raw image contents, or secrets.
10. Do not expose facial embeddings in normal API responses.
11. Never swallow exceptions with `except: pass`.
12. On database failure, preserve consistency and roll back the transaction when required.
13. Do not disable tests, linters, type checks, or security checks merely to make a build pass.
14. No unrelated refactors inside feature work.
15. No new dependency without justification and approval.
16. Never invent command output. If a command fails, report the real failure.
17. **Never choose or implement a facial recognition model, embedding dimension,
    similarity metric, threshold, or PostgreSQL representation without explicit approval.**
18. If domain behavior is ambiguous, ask before implementing.

---

## Core business rules

### Account approval and roles

The canonical roles are `user`, `referee`, `federation_delegate`, and
`administrator`.

Public signup always creates `role = user` and requires `requested_role` to be either
`referee` or `federation_delegate`. Only an administrator may approve a pending account,
and approval assigns exactly the stored requested role.

Pending users may use only authentication self-service. Disabled approved accounts keep
their role and may log in, refresh, log out, and consult their own account, but every
business endpoint must reject them.

Authorization must use JWT identity and active session for authentication, then load the
current `User` row from PostgreSQL for role and account-state decisions. Never authorize
business access from a JWT role claim alone.

### Sport match configuration

`Sport.max_players`, `Sport.match_duration`, and `Sport.resolution_methods` are required
and immutable after creation. Match duration is a positive whole number of minutes.
Resolution methods are stored as an ordered, non-empty PostgreSQL JSONB array of unique
`{code, name}` objects; codes use English `snake_case` identifiers.

### Team management

A Team persists only its name, normalized name, Sport, gender category, administrative
state, creation timestamp, and optional disablement timestamp.

Team names are unique after case folding and accent removal within the same Sport and
gender category. Disabled Teams continue reserving their names. Sport and gender are
immutable after creation, and a disabled Team cannot be renamed.

Teams are never permanently deleted through the API. Use explicit disable and re-enable
operations. A Sport referenced by any enabled or disabled Team cannot be deleted.

Active administrators and federation delegates may list, view, create, rename, disable,
and re-enable Teams. Federation delegates may read Sports but cannot mutate them.

Keep administrative state and competition eligibility separate:

```text
is_eligible_for_competition =
    Team.is_enabled
    AND active Plantel count == Team.sport.max_players
```

Never persist eligibility or a mutable player-count cache. Until Player and Plantel are
implemented, Team responses expose zero current players, false eligibility, and an empty
player list.

### Player-team membership

Use `Plantel`, not `Player.team`.

Approved future rules:

- A Player may belong to at most one enabled Team globally at a time.
- Player availability is derived from the absence of active membership; never persist a
  drifting `selection_status`.
- Never delete or overwrite membership history.
- Active membership count cannot exceed `Team.sport.max_players`.
- Removing or replacing a member makes the Team ineligible until the exact required
  count is restored.
- A member may be removed or replaced only while the Team is not in an in-progress
  Competition.
- Disabling a Team will close active memberships without disabling Players.
- Team is the stable identity; Plantel records historical membership and never replaces
  Team.

Implementation handoff for `Player` and `Plantel`:

- `Player` and `Plantel` are not implemented yet. Before implementing them, use the
  current approved requirements and versioned schema for their exact fields, keys,
  timestamps, and API contract. Do not infer missing domain data.
- Do not add `team_id` to `Player`, and do not persist derived values such as player
  availability, active player count, or Team competition eligibility.
- Treat an active `Plantel` row as the source of truth for current membership. Derive
  availability, counts, and eligibility on the server from PostgreSQL; never accept
  these values from the client.
- Membership writes must be transactional. Enforce both Team capacity and at most one
  active Team membership per Player in backend logic and, where the approved schema
  permits it, with database constraints that remain safe under concurrent requests.
- Keep every closed membership as history. Do not physically delete or overwrite an
  old membership when a Player changes Team.
- Do not invent how a membership becomes active or closed. If the approved schema does
  not define it, ask before choosing fields such as `is_active`, `status`, `joined_at`,
  or `left_at`.
- Competition-dependent removal and replacement rules remain deferred until
  `Competition` and `Participation` exist. Do not create speculative entities only to
  implement `Player` or `Plantel`.

### Competition membership

Use `Participation` for Team ↔ Competition.

Do not infer participation only because a team appears in a match.

### Duplicate player rule

A player must not be assigned to more than one team within the same competition.

The approved global active-membership rule is stricter: while a Plantel membership is
active, the Player cannot simultaneously belong to any other enabled Team. Enforcing
that global rule must also preserve this competition-level invariant.

This is a critical invariant.

Do not rely only on frontend validation.
Backend logic must reject the conflict and the database should enforce it where the
final schema permits a reliable constraint.

### Match validation

A match must reference:
- one competition;
- two different teams;
- one referee.

Teams must be valid participants in the match competition.

### Recognition vs eligibility

Never treat facial recognition as automatic authorization.

```text
Face recognition
      ↓
Player identification
      ↓
Business-rule validation
      ↓
Eligibility result
```

A correctly recognized player may still be ineligible.

### Manual validation

When automatic recognition fails or is ambiguous, the referee must be able to resolve
the case manually according to the approved workflow.

Do not silently convert an unresolved face into a confirmed player.

### Recognition incidents

If a scan/photo has recognition problems, the system must preserve the information
required to resolve it later.

Do not discard the scan simply because automatic recognition failed.

### Historical import

Importing teams/rosters from previous competitions must not:
- duplicate existing players unintentionally;
- duplicate teams unintentionally;
- destroy historical Plantel records;
- violate the one-team-per-player-per-competition rule.

If conflict behavior is not defined, ask.

---

## Facial recognition rules

Libraries currently approved:
- `face_recognition`
- `cv2`
- `numpy`

Do not change or add recognition libraries without approval.

Expected conceptual flow:

```text
group image
  ↓
face detection
  ↓
face encoding / embedding
  ↓
candidate comparison
  ↓
recognition result
  ↓
eligibility validation
  ↓
manual resolution if needed
```

Do not assume:
- embedding dimensionality;
- distance/similarity metric;
- comparison threshold;
- number of reference images per player;
- retention period;
- storage format in PostgreSQL;
- synchronous vs asynchronous processing.

Ask before implementing any of these as fixed decisions.

Biometric data is sensitive:
- never log embeddings;
- never expose embeddings unnecessarily;
- never use real player images in tests without authorization;
- validate image uploads;
- avoid unnecessary copies.

---

## Backend style

Python code must be clear, typed where practical, and small enough to test.

Prefer:

```text
route
  ↓
service
  ↓
SQLAlchemy model / DB
```

Routes handle HTTP concerns.
Services handle business logic.
Models represent persisted data and relationships.

Do not move complex business rules into route functions.

Use explicit transactions for multi-step writes.

Use specific exceptions where possible.

Never return raw SQL/database errors to API clients.

---

## Flask / API rules

Use consistent REST-style endpoints and HTTP methods.

Typical patterns:

```text
GET    /sports
POST   /sports
GET    /sports/{id}
PUT    /sports/{id}
DELETE /sports/{id}
```

Exact routes must follow the existing repository conventions.

Use meaningful status codes:
- `200` successful read/update;
- `201` created;
- `204` successful delete without body;
- `400` invalid request;
- `401` unauthenticated;
- `403` forbidden;
- `404` not found;
- `409` business conflict;
- `500` unexpected server error.

Keep error responses predictable and do not expose stack traces to clients.

---

## SQLAlchemy / PostgreSQL

Models in `models.py` represent database tables.

Use:
- primary keys;
- foreign keys;
- `NOT NULL`;
- `UNIQUE`;
- `CHECK`;
- indexes;

when justified by the DER and business invariants.

Do not add indexes blindly.

Do not manually edit production/shared schemas as a substitute for reproducible schema
changes.

The project is **not using Flask-Migrate/Alembic** unless the team later approves it.

Do not introduce those tools automatically.

---

## Frontend rules

Frontend stack:
- Vue
- TypeScript

Use TypeScript strictly where practical.

Avoid `any` unless explicitly justified.

Do not put raw API URLs and duplicated request logic across multiple components.
Prefer a reusable API/service layer.

Frontend validation improves UX but does not replace backend validation.

Always account for:
- loading;
- empty;
- success;
- error states.

---

## Naming

Keep machine-readable identifiers in English:
- variables;
- functions;
- classes;
- modules;
- folders;
- database tables/columns;
- JSON fields;
- API routes;
- environment variables;
- tests.

Examples:

```text
player_id
competition_id
get_players
create_match
scan_result
```

Do not mix languages inside identifiers.

Avoid names such as:

```text
get_jugadores
player_nombre
crear_match
```

Use consistent `snake_case` for Python/DB identifiers unless the existing code already
establishes another convention.

Use `PascalCase` for Python classes.

Use `UPPER_SNAKE_CASE` for constants/environment variable names.

---

## Environment configuration

Never hard-code environment-specific credentials.

Use `.env` locally.

Example structure only:

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE
```

The real `.env` is local and must not be committed.

If needed, provide an `.env.example` with placeholders only.

---

## Testing and quality

Every meaningful feature should include tests appropriate to its risk.

Prioritize tests for:
- duplicate player assignments;
- Plantel history;
- participation validation;
- match validation;
- sanctions;
- recognition-result handling;
- manual validation;
- failed transactions.

Test both happy paths and failure paths.

Important negative cases include:
- nonexistent IDs;
- duplicate assignments;
- invalid team/competition combinations;
- same team on both sides of a match;
- malformed payloads;
- failed image processing;
- unresolved face recognition.

Do not claim tests passed unless they were actually run.

---

## Documentation and API client synchronization

Documentation is part of every feature. Update the existing documents in the same
change that modifies behavior; do not leave obsolete or duplicated instructions.

Use `documentation.md` for the functional API contract and business-facing technical
documentation. When a feature adds or changes behavior, document in Spanish:

- the complete user and authorization flow;
- endpoints, HTTP methods, authentication and required roles;
- path/query parameters and request bodies;
- successful responses and relevant error responses;
- validations, state transitions, relationships, and business invariants.

Keep machine-readable names in English even when the explanation is in Spanish.

Use `readme.md` only for operational and repository instructions, including:

- prerequisites and local installation;
- environment configuration with placeholders only;
- application startup;
- database creation, migrations, and seed/initialization procedures;
- test commands;
- instructions for importing or running development tools.

Do not turn `readme.md` into a duplicate API reference. Link to `documentation.md` for
endpoint and flow details.

When an endpoint, method, path, authorization rule, parameter, request body, or
test workflow changes, update the importable files under `hoppscotch/`:

- keep the collection aligned with every available endpoint;
- keep environment variables aligned with the collection;
- use environment variables for base URLs, credentials, tokens, and reusable IDs;
- include useful request descriptions and scripts when a response feeds a later request;
- keep credentials, tokens, and other secret values empty;
- validate the JSON files and ensure they remain importable by Hoppscotch.

Purely internal changes with no API or manual-test impact do not require artificial
Hoppscotch edits.

---

## Definition of Done

A task is done when:
- acceptance criteria are met;
- business rules are preserved;
- database consistency is protected;
- relevant tests pass;
- configured quality checks pass;
- no critical known defect remains;
- no secret/sensitive data was committed;
- API behavior is coherent;
- `documentation.md` reflects changed API behavior, flows, and business rules;
- `readme.md` reflects changed setup, migration, initialization, or tooling steps;
- Hoppscotch importables are synchronized when the API or manual-test flow changed;
- changes are reviewable and scoped.

Never silence a failing check just to mark work as done.

---

## Git workflow

No specific branching convention is currently defined.

Therefore:
- inspect the repository before assuming branch names;
- do not invent mandatory `develop`, issue prefixes, or PR rules;
- keep commits atomic;
- do not mix unrelated changes;
- do not push directly to protected branches if repository settings/workflow prohibit it.

Prefer Conventional Commit style when compatible with the repository:

```text
feat(sports): add sport creation
fix(roster): reject duplicate player assignment
refactor(matches): move validation to service
test(players): add roster-history tests
docs(setup): document database configuration
```

---

## Versioning and changelog

Use Semantic Versioning when releases are created:

```text
MAJOR.MINOR.PATCH
```

- `MAJOR`: breaking/incompatible change.
- `MINOR`: backward-compatible feature.
- `PATCH`: backward-compatible fix.

When converting commits/features/changes into release documentation, produce concise,
professional entries using categories such as:

```markdown
### Added
### Changed
### Fixed
### Removed
### Security
```

Do not invent changes.

Prefer user/business impact over low-level implementation noise.

---

## Agent workflow

Before writing code:

1. Read this file.
2. Inspect the relevant repository area.
3. Identify the affected entity/HU.
4. Read only the files needed for the task.
5. Check existing patterns before creating new ones.
6. Identify DB/API/test impact.
7. Ask if a required business decision is undefined.

During implementation:
- make small changes;
- reuse existing patterns;
- avoid speculative abstractions;
- test incrementally;
- keep unrelated code untouched.

Before finishing:
- run relevant tests/checks;
- inspect the diff;
- verify no secrets/sensitive data were added;
- mention unresolved risks or decisions;
- update changelog/version information if requested.

---

## Do not guess

Stop and ask if a task requires an undefined decision about:
- DER/schema changes;
- player eligibility;
- roster transfer rules;
- sanction duration/expiration;
- match rules not present in the DER;
- facial recognition threshold;
- embedding dimensions;
- similarity metric;
- embedding PostgreSQL type;
- biometric retention;
- authentication/authorization behavior;
- image-storage strategy;
- deployment/infrastructure;
- new dependencies or technologies.

A precise question is cheaper than a wrong implementation.
