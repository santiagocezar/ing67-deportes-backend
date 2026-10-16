"""Historical sanction eligibility for a Player in one scheduled Match."""

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from ..extensions import db
from ..models import Match, Player, PlayerSanction
from .sanctions import SanctionServiceError, _overlapping_match_groups


@dataclass(frozen=True)
class ApplicableSanction:
    """Immutable sanction values that were applicable before a target Match."""

    sanction_id: int
    title: str
    reason: str
    remaining_matches: int


@dataclass(frozen=True)
class PlayerMatchEligibility:
    """Historical eligibility result used while producing one roster scan."""

    player_id: int
    match_id: int
    applicable_sanctions: tuple[ApplicableSanction, ...]


def _is_annulled_before(
    sanction: PlayerSanction, target_starts_at: datetime
) -> bool:
    """Whether the sanction had already been annulled at the target boundary."""
    if sanction.status != "annulled":
        return False
    return (
        sanction.annulled_at is None
        or sanction.annulled_at <= target_starts_at
    )


def _frozen_team_ids(sanction: PlayerSanction) -> set[int]:
    return {
        entry.team_id
        for entry in sanction.frozen_team_entries
    }


def _is_qualifying_match(
    match: Match,
    sanction: PlayerSanction,
    frozen_team_ids: set[int],
    activation_boundary: datetime,
) -> bool:
    """Apply the sanction service's strict candidate-Match boundaries."""
    return (
        match.id != sanction.source_match_id
        and match.starts_at is not None
        and match.ends_at is not None
        and match.starts_at > activation_boundary
        and (
            match.team_1_id in frozen_team_ids
            or match.team_2_id in frozen_team_ids
        )
    )


def _completed_groups_before_target(
    sanction: PlayerSanction,
    candidate_matches: Iterable[Match],
    frozen_team_ids: set[int],
    activation_boundary: datetime,
    target_starts_at: datetime,
    consumed_match_ids: set[int],
) -> list[tuple[datetime, Match]]:
    """Return unconsumed qualifying groups completed at the target boundary."""
    qualifying_matches = [
        match
        for match in candidate_matches
        if _is_qualifying_match(
            match, sanction, frozen_team_ids, activation_boundary
        )
    ]
    completed_groups: list[tuple[datetime, Match]] = []
    for group in _overlapping_match_groups(qualifying_matches):
        canonical_match = group[0]
        completed_at = max(match.ends_at for match in group)
        if (
            completed_at <= target_starts_at
            and canonical_match.id not in consumed_match_ids
        ):
            completed_groups.append((completed_at, canonical_match))
    return sorted(completed_groups, key=lambda item: (item[0], item[1].id))


def _resolve_applicable_sanctions(
    target_match: Match,
    sanctions: Sequence[PlayerSanction],
    candidate_matches: Iterable[Match],
) -> tuple[ApplicableSanction, ...]:
    """Reconstruct the global sanction queue immediately before a Match starts."""
    if target_match.starts_at is None:
        raise SanctionServiceError(
            "match_not_scheduled",
            "The target Match must have a schedule.",
        )

    consumed_match_ids: set[int] = set()
    preceding_served_at: datetime | None = None
    target_starts_at = target_match.starts_at

    for sanction in sanctions:
        if _is_annulled_before(sanction, target_starts_at):
            # Annulment stops future eligibility but does not release Match
            # groups that this sanction had already consumed. The sanction
            # service reconciles before annulling, so preserve that queue
            # position when evaluating later sanctions.
            frozen_team_ids = _frozen_team_ids(sanction)
            annulled_at = sanction.annulled_at or target_starts_at
            completed_groups = _completed_groups_before_target(
                sanction,
                candidate_matches,
                frozen_team_ids,
                sanction.effective_at,
                min(annulled_at, target_starts_at),
                consumed_match_ids,
            )
            consumed_match_ids.update(
                match.id
                for _, match in completed_groups[: sanction.suspension_matches]
            )
            continue

        activation_boundary = sanction.effective_at
        if (
            preceding_served_at is not None
            and preceding_served_at > activation_boundary
        ):
            activation_boundary = preceding_served_at

        # Queued sanctions cannot become effective until the preceding one is
        # served, and a candidate must start strictly after its boundary.
        if target_starts_at <= activation_boundary:
            break

        frozen_team_ids = _frozen_team_ids(sanction)
        completed_groups = _completed_groups_before_target(
            sanction,
            candidate_matches,
            frozen_team_ids,
            activation_boundary,
            target_starts_at,
            consumed_match_ids,
        )
        served_groups = completed_groups[: sanction.suspension_matches]
        if len(served_groups) == sanction.suspension_matches:
            consumed_match_ids.update(
                match.id for _, match in served_groups
            )
            preceding_served_at = served_groups[-1][0]
            continue

        if _is_qualifying_match(
            target_match,
            sanction,
            frozen_team_ids,
            activation_boundary,
        ):
            return (
                ApplicableSanction(
                    sanction_id=sanction.id,
                    title=sanction.title,
                    reason=sanction.reason,
                    remaining_matches=(
                        sanction.suspension_matches - len(served_groups)
                    ),
                ),
            )

        # This is the active queued sanction, even if the scanned Team is not
        # frozen for it. Later sanctions cannot apply yet.
        break

    return ()


def get_player_match_eligibility(
    player_id: int, match_id: int
) -> PlayerMatchEligibility:
    """Resolve sanctions as they stood immediately before a target Match.

    The resolver deliberately does not reconcile, lock, write, or commit. It
    derives historical remaining counts from the immutable Match schedule and
    frozen-Team snapshots instead of using a sanction's current lifecycle.
    """
    with db.session.no_autoflush:
        target_match = db.session.execute(
            db.select(Match).where(Match.id == match_id)
        ).scalar_one_or_none()
        if target_match is None:
            raise SanctionServiceError(
                "match_not_found", "The Match does not exist.", 404
            )
        if target_match.starts_at is None or target_match.ends_at is None:
            raise SanctionServiceError(
                "match_not_scheduled",
                "The target Match must have a schedule.",
            )

        player = db.session.execute(
            db.select(Player).where(Player.id == player_id)
        ).scalar_one_or_none()
        if player is None:
            raise SanctionServiceError(
                "player_not_found", "The Player does not exist.", 404
            )

        sanctions = list(
            db.session.execute(
                db.select(PlayerSanction)
                .options(selectinload(PlayerSanction.frozen_team_entries))
                .where(
                    PlayerSanction.player_id == player.id,
                    PlayerSanction.created_at <= target_match.starts_at,
                )
                .order_by(PlayerSanction.created_at, PlayerSanction.id)
            ).scalars()
        )
        frozen_team_ids = sorted(
            {
                entry.team_id
                for sanction in sanctions
                for entry in sanction.frozen_team_entries
            }
        )
        candidate_matches: list[Match] = []
        if frozen_team_ids:
            candidate_matches = list(
                db.session.execute(
                    db.select(Match)
                    .where(
                        Match.starts_at.is_not(None),
                        Match.ends_at.is_not(None),
                        Match.starts_at <= target_match.starts_at,
                        or_(
                            Match.team_1_id.in_(frozen_team_ids),
                            Match.team_2_id.in_(frozen_team_ids),
                        ),
                    )
                    .order_by(Match.starts_at, Match.ends_at, Match.id)
                ).scalars()
            )

    return PlayerMatchEligibility(
        player_id=player.id,
        match_id=target_match.id,
        applicable_sanctions=_resolve_applicable_sanctions(
            target_match,
            sanctions,
            candidate_matches,
        ),
    )
