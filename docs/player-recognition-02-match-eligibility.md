# HU09 — Specification 2: Historical Match Eligibility

## Delivery boundary

- Status: approved for implementation after Specification 1.
- Target size: 400–900 changed source/test lines.
- Suggested commit: `feat(scans): resolve sanction eligibility for target matches`
- Does not detect faces, accept image uploads, persist scans, or add frontend work.

## Goal

Resolve sanctions as of the scanned Match, not as of the request time. HU09 permits scans up to two hours after a Match ends: a sanction may already have been consumed by that Match when the request is made, while it was applicable before that Match began.

## Rules

1. Reuse existing global-sanction, frozen-Team, overlap-group, queue, and strict time-boundary rules. Do not add a Competition-specific sanction model.
2. A source Match never makes its newly created sanction ineligible for itself.
3. Determine applicable sanctions immediately before the target Match starts; never use only current `remaining_matches` or `status`.
4. Return all applicable sanctions in deterministic queue order, with title, reason, and remaining-count values applicable at that point.
5. The resolver is read-only. It must not create consumption rows, mutate sanction state, or make a scan request alter later sanctions.
6. A historical roster Player may be disabled now; historical eligibility remains meaningful. Recognition candidates remain limited by Specification 3.

## Service design

Add a small service function such as `get_player_match_eligibility(player_id, match_id)`. It loads the target Match schedule and Player, reuses shared pure grouping/qualification helpers from the sanction service where that reduces duplication, and evaluates only data earlier than the target Match. Preserve deterministic ordering and bounded SQL; avoid a query per sanction or frozen Team.

Refactoring shared pure helpers is allowed only when it preserves existing sanction API behavior and tests. Do not alter consumption semantics, database schema, or public sanction response contracts.

## Required tests

- a sanction effective after the target Match is not returned;
- the source Match is not blocked by its source sanction;
- a completed target Match that subsequently consumed a sanction is still historically reported as sanctioned;
- strict `starts_at` boundaries and queued-sanction activation boundaries;
- overlapping groups, frozen Teams, served/annulled lifecycle, and disabled Players;
- multiple relevant sanctions preserve stable order and remaining-count snapshots;
- repeated resolver calls perform no writes and produce equal results;
- query count remains bounded for multiple sanctions/Teams.

## Completion

The service is a tested internal dependency for Specification 3. Existing sanction creation, consumption, reads, edit, annulment, and enrollment-guard tests remain green.
