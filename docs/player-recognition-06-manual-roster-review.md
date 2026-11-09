# HU11–HU12 — Specification 6: Historical Manual Roster Review

## Objective

- **User stories:** HU11, *Registro de incidencias en el reconocimiento*, and HU12, *Validación manual de jugadores*, as clarified in this conversation.
- **Outcome:** the assigned referee can make one complete, immutable, historical membership conclusion for every unknown face in a pending review. A Match requires biometric audit only while at least one of its retained reviews is pending.
- **Output scope:** backend only. This specification consumes Specification 5; it provides contracts and data for a future client but does not implement frontend views, face crops, sanctions, or match sheets.

## Evidence

| Source | Exact reference | Used for |
| --- | --- | --- |
| `AGENTS.md` | Implemented player/competition domain; hard rules 2, 7–10, 17–20; Flask/API and SQLAlchemy/PostgreSQL rules | Preserve roster snapshots and immutable history; no sanction/recognition-policy invention; require migration, PlantUML, contracts, docs, tests, and safe authorization. |
| HU11 | Acceptance criteria supplied in this conversation | Close the active biometric-audit condition only after every unknown face has a terminal manual conclusion. |
| HU12 | Acceptance criteria and later scope clarification supplied in this conversation | Manual choices are team membership, non-membership, or inconclusive; sanctions and planilla behavior are explicitly excluded. |
| `docs/player-recognition-01-scan-foundation.md` | `match_roster_scan_faces` invariants and “Completion” | Automated scan outcomes remain immutable and a future HU owns manual decisions. |
| `docs/player-recognition-02-match-eligibility.md` | Rules 1–6 | Existing sanction behavior is historical and must not be changed or reused as a manual-review decision. |
| `docs/player-recognition-03-processing.md` | “Candidate scope and outcomes” | Automatic unknown status remains an algorithmic result, not an error or a mutable player association. |
| `docs/player-recognition-04-history.md` | “Completion” | HU11/HU12 own every manual decision for unknown faces and group-image access/retention. |
| Specification 5 | `docs/player-recognition-05-consented-scan-retention.md` | Pending review lifecycle, protected evidence, expiration, and consented scan persistence. |
| Backend | `app/models.py`, `app/services/match_roster_scans.py`, `app/routes/match_roster_scans.py` | Existing immutable face snapshots, Match/Team authorization, and protected history baseline. |

## Scope

### In scope

- A one-time, atomic manual review of all `unknown` faces in one pending scan.
- Historical conclusions: `belongs_to_team`, `does_not_belong_to_team`, and `inconclusive`.
- Validation against the frozen Competition roster of the scan's selected Match Team.
- Auditable reviewer/time/player-name snapshots.
- Authorized read contracts for a referee review section and Match-level `requires_biometric_audit` state.
- Review closure, image deletion integration, expiration behavior, migration, PlantUML, OpenAPI, documentation, and tests.

### Out of scope

- Recognition reruns, matching thresholds, embeddings, face crops, player-photo changes, or edits to automated face statuses.
- Sanction checks, sanction alerts, red/orange presentation, player-profile workflows, match sheets, lineups, or any Match-start blocking behavior.
- Allowing an administrator to submit manual conclusions.
- Partial drafts, edits, reopens, or deletion of manual conclusions.
- Frontend layout, Bootstrap components, interaction flow, or client-side image cropping.

## Current baseline

- `MatchRosterScanFace.status` has five immutable automated values. `unknown` requires `player_id`, `player_name_snapshot`, and `match_percentage` to be null by a database check.
- Current automatic recognition evaluates a candidate against the selected Team's current eligible-photo candidates, then categorizes roster membership with the Competition's historical roster and captures applicable historical sanctions.
- `GET /matches/{match_id}` is readable by every authenticated user. It must not become the delivery mechanism for manual-review state because review/evidence access is restricted to an administrator or the assigned referee.
- Specification 5 introduces a pending review only for a confirmed retained unknown-face scan and expires it at Match end plus two hours.

## Approved behavior

### Authorization and time window

- Only the referee assigned to the review's Match may submit manual conclusions.
- Administrators may list/read review metadata and protected evidence under Specification 5 but cannot submit, edit, reopen, or delete a conclusion.
- A pending review can be resolved while the Match is `in_progress` and until its persisted `expires_at`. On or after expiry, it must be reconciled to `expired`, reject writes, delete evidence as specified in Specification 5, and not contribute to the active flag.

### Manual conclusion rules

One final request supplies exactly one decision for every persisted `unknown` face in the scan:

| Outcome | Required data | Business validation |
| --- | --- | --- |
| `belongs_to_team` | `player_id` | Player belongs to the selected Team's frozen Competition roster; the same Player cannot be selected for two faces in the scan. |
| `does_not_belong_to_team` | No `player_id` | Used for a coach, supporter, ball retriever, or other person outside that roster. |
| `inconclusive` | No `player_id` | Terminal historical conclusion when the referee cannot determine membership. |

The service validates all indexes before writing. It rejects duplicate, missing, recognized, non-existent, or non-unknown face indexes; an invalid/mismatched player; extra JSON fields; repeated Player assignments; non-pending/expired reviews; and unauthorized actors. It then inserts all decisions and marks the review resolved in one transaction.

`MatchRosterScanFace.status` always remains `unknown`. The manual decision is a separate auditable fact; it never mutates automatic detection, face boxes, automatic player fields, count values, or sanction snapshots. For a membership conclusion, persist both the Player FK and the Player-name snapshot so later Player edits or disabling do not rewrite history.

No sanction is checked, copied, returned as a new manual status, or used to block any behavior in this workflow. Sanction management remains exclusively in the existing player/sanction domain.

### Audit-flag semantics

- `requires_biometric_audit` is derived, never stored as a redundant Match column.
- It is `true` only when the Match has at least one pending, unexpired manual review.
- It becomes `false` when all reviews are `resolved` or `expired`; `inconclusive` is terminal and therefore closes its review once every unknown face has a decision.
- A later consented unknown-face scan creates another pending review and can make the flag `true` again.
- The Match's normal time-derived `status` and all scheduling rules remain unchanged.

## Persistence

Add `match_roster_scan_manual_review_decisions` in a reviewed migration and update `docs/erd.puml`.

| Field | Rule |
| --- | --- |
| `scan_id`, `face_index` | Composite primary key and composite FK to `match_roster_scan_faces`; a face receives at most one manual decision. |
| `outcome` | Check-constrained to the three approved outcomes. |
| `player_id` | Nullable `RESTRICT` FK to `players`; required only for `belongs_to_team`. |
| `player_name_snapshot` | Required and nonblank only for `belongs_to_team`; null otherwise. |
| `created_at` | Timezone-aware database-created timestamp. |

Extend `match_roster_scan_manual_reviews` from Specification 5 with nullable `resolved_by_user_id` (`RESTRICT` FK) and `resolved_at`. Checks must require both only when status is `resolved`, and require both to be null for `pending` and `expired`.

Service-level rules enforce frozen-roster membership, full decision coverage, and no duplicate manually selected Player because they span rows/tables. Do not add a current-Team-membership dependency: later membership, Player enablement, or name changes must not invalidate historical review.

## Confirmed API contract

| Operation | Method and endpoint | Request | Success | Errors/auth |
| --- | --- | --- | --- | --- |
| Finalize one review | `PUT /roster-scans/{scan_id}/manual-review` | Strict JSON object with `decisions` | `200 MatchRosterScanManualReviewResponse` | Assigned referee only; `400` for malformed/non-object JSON, `422` for Pydantic fields, `409` for a closed/expired review or valid decisions that violate review/roster invariants. |
| List review section | `GET /manual-reviews?status=pending&page=1` | Strict optional `status` (`pending`, `resolved`, `expired`, `all`) and positive `page` | `200 MatchRosterScanManualReviewListResponse` | Administrator: all reviews. Referee: only assigned-Match reviews. No image URL or bytes. |
| Read Match audit state | `GET /matches/{match_id}/manual-review-status` | Path only | `200 MatchManualReviewStatusResponse` with `requires_biometric_audit` and safe review counts | Administrator or assigned referee only. |

The existing `GET /roster-scans/{scan_id}` and nested scan-list response must expose a safe review summary and, once resolved, decision data. The review list must include only what is needed to identify the evidence source: Competition `id`/`name`, Match `id`/schedule/status, selected Team `id`/`name`, scan `id`/created time, unknown-face count, review status, and expiry. It must never return a protected-image URL, storage name, raw bytes, embeddings, or filesystem metadata.

`PUT /roster-scans/{scan_id}/manual-review` body shape:

```json
{
  "decisions": [
    {
      "face_index": 0,
      "outcome": "belongs_to_team",
      "player_id": 21
    },
    {
      "face_index": 2,
      "outcome": "does_not_belong_to_team"
    },
    {
      "face_index": 5,
      "outcome": "inconclusive"
    }
  ]
}
```

Unknown fields, a `player_id` on either non-membership outcome, an omitted `player_id` for `belongs_to_team`, or a partial decision list are rejected. Backend authorization and frozen-roster validation remain authoritative.

## Backend implementation plan

| Area | Existing/new path | Minimal change |
| --- | --- | --- |
| Models/migration | `app/models.py`, `migrations/versions/` | Add decision model; add resolution audit columns/checks to Specification 5's review model and relationships. |
| Schemas | `app/schemas/match_roster_scans.py` or a focused review schema module | Add strict body/path/query/list/detail/summary response contracts. |
| Services | New focused manual-review service plus `app/services/match_roster_scans.py` integration | Lock review/scan/Match, validate terminal decisions, derive flag, and perform image cleanup through Specification 5's storage service. |
| Routes | `app/routes/match_roster_scans.py` or a focused review route module | Register the three operations with explicit security/error responses; keep route functions HTTP-only. |
| Existing scan responses | Scan response serializer and Pydantic models | Add safe review/decision summaries without changing automated face outcomes. |
| Documentation | `documentation.md`, `docs/openapi.json`, `docs/erd.puml` | Document authorization, outcomes, expiration, audit-flag derivation, and explicitly excluded sanction/planilla behavior. |

When a review is resolved, clear its private scan-image reference and request the staged permanent deletion from Specification 5. If deletion finalization fails after database commit, leave only its recoverable deletion state for reconciliation; do not roll back an already-audited final conclusion or expose the image.

## Acceptance and verification

| Criterion | Implementation requirement | Verification |
| --- | --- | --- |
| HU11: audit flag clears after every unknown face is assigned or discarded | Derive `requires_biometric_audit` from pending reviews only; atomic full decisions transition the review to `resolved`. | Multiple-scan tests: one pending keeps flag true; all resolved makes it false; a later pending scan reactivates it. |
| HU12: referee associates an unknown face with a Team player | Only the assigned referee can finalize; `belongs_to_team` validates the frozen selected-Team roster and records immutable Player/name audit data. | Authorization, disabled-current-Player, wrong-Team/roster, duplicate-Player, unknown/recognized index, rollback, and historical-name tests. |
| HU12: non-player face can be ignored | `does_not_belong_to_team` requires no Player and is terminal without changing the automated `unknown` result. | Assert no Player data is stored, review resolves after complete form, image is deleted, and flag clears. |
| HU12: inconclusive option | `inconclusive` requires no Player, is terminal, remains historically visible, and closes the review when all decisions are complete. | Assert atomic terminal behavior and no sanction/Match-status change. |
| Privacy/expiry | Expired reviews cannot be written, have no retrievable image, remain history, and do not keep the flag active. | Exact `expires_at` boundary, lazy reconciliation, binary-image denial, and no storage metadata/biometric leakage tests. |

- Run `python -m unittest discover -s tests -v`.
- Run the disposable PostgreSQL migration/file tests described in `readme.md`, including rollback, FK/check, lock, and concurrent-finalization coverage.
- Run `python -m flask --app app export-openapi` and verify `docs/openapi.json` is deterministic.
- Inspect the final diff for a new dependency, real biometric image, secret, changed sanction semantics, Match-status mutation, or unrelated refactor.

## Documentation impact

- Update `documentation.md` with the three terminal manual outcomes, ownership, frozen-roster rule, expiry, safe read data, atomicity, and the fact that sanctions/planillas are excluded.
- Regenerate `docs/openapi.json`; do not maintain a manual parallel contract.
- Update `docs/erd.puml` and migration documentation in the same change.
- Update `readme.md` only if the already-existing scan-evidence reconciliation command gains an operator-visible manual-review cleanup behavior.

## Size and partition

- **Expected size:** medium.
- **Spec boundary:** this document starts after a private pending review exists. It handles only historical manual conclusions and derived review state; consent, image storage, protected binary retrieval, and expiry mechanics remain in Specification 5.

## Implementation stop conditions

Stop and report a blocker if implementation would require changing the approved recognition thresholds/libraries, the frozen Competition roster rule, the private local-storage model, the no-background-infrastructure constraint, sanction semantics, Match lifecycle, or frontend behavior beyond these backend contracts.
