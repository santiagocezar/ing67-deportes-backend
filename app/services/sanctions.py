from datetime import datetime

from dataclasses import dataclass

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (
    ADMIN_USER_ROLE,
    DEFAULT_USER_ROLE,
    CompetitionRosterPlayer,
    Match,
    Player,
    PlayerSanction,
    PlayerSanctionMatchConsumption,
    PlayerSanctionTeam,
    Team,
    team_players,
)
from ..schemas.sanctions import (
    PlayerSanctionCreateRequest,
    PlayerSanctionListQuery,
    PlayerSanctionUpdateRequest,
)
from .competition_state import utc_now
from .matches import reconcile_matches


class SanctionServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class PlayerSanctionListResult:
    sanctions: list[PlayerSanction]
    page: int
    per_page: int
    total_items: int
    total_pages: int


SANCTIONS_PER_PAGE = 25


def _lock_match(match_id: int) -> Match:
    match = db.session.execute(
        db.select(Match)
        .where(Match.id == match_id)
        .with_for_update(of=Match)
    ).scalar_one_or_none()
    if match is None:
        raise SanctionServiceError(
            "match_not_found", "The Match does not exist.", 404
        )
    return match


def _lock_player(player_id: int) -> Player:
    player = db.session.execute(
        db.select(Player)
        .where(Player.id == player_id)
        .with_for_update(of=Player)
    ).scalar_one_or_none()
    if player is None:
        raise SanctionServiceError(
            "player_not_found", "The Player does not exist.", 404
        )
    return player


def _source_roster_team_id(match: Match, player_id: int) -> int:
    roster_entries = list(
        db.session.execute(
            db.select(CompetitionRosterPlayer)
            .where(
                CompetitionRosterPlayer.competition_id == match.competition_id,
                CompetitionRosterPlayer.player_id == player_id,
                CompetitionRosterPlayer.team_id.in_(
                    (match.team_1_id, match.team_2_id)
                ),
            )
            .with_for_update(of=CompetitionRosterPlayer)
        ).scalars()
    )
    if len(roster_entries) != 1:
        raise SanctionServiceError(
            "player_not_in_match_roster",
            "The Player is not in the source Match roster.",
        )
    return roster_entries[0].team_id


def _locked_current_team_ids(player_id: int) -> list[int]:
    team_ids = sorted(
        db.session.execute(
            db.select(team_players.c.team_id)
            .where(team_players.c.player_id == player_id)
            .with_for_update(of=team_players)
        ).scalars()
    )
    if not team_ids:
        return []
    locked_teams = list(
        db.session.execute(
            db.select(Team)
            .where(Team.id.in_(team_ids))
            .order_by(Team.id)
            .with_for_update(of=Team)
        ).scalars()
    )
    return [team.id for team in locked_teams]


def _authorize_actor(match: Match, actor_user_id: int, actor_role: str) -> None:
    if actor_role == ADMIN_USER_ROLE:
        return
    if actor_role != DEFAULT_USER_ROLE:
        raise SanctionServiceError(
            "application_role_required",
            "A supported application role is required.",
            403,
        )
    if match.referee_id != actor_user_id:
        raise SanctionServiceError(
            "match_referee_required",
            "The assigned Match referee is required.",
            403,
        )


def _sanction_options():
    return (
        joinedload(PlayerSanction.player),
        joinedload(PlayerSanction.source_match).joinedload(Match.competition),
        joinedload(PlayerSanction.created_by),
        joinedload(PlayerSanction.updated_by),
        joinedload(PlayerSanction.annulled_by),
        selectinload(PlayerSanction.frozen_team_entries).joinedload(
            PlayerSanctionTeam.team
        ),
    )


def _overlapping_match_groups(matches: list[Match]) -> list[list[Match]]:
    """Build connected half-open interval groups from deterministically sorted Matches."""
    groups: list[list[Match]] = []
    group_end: datetime | None = None
    for match in sorted(
        matches, key=lambda item: (item.starts_at, item.ends_at, item.id)
    ):
        if group_end is None or match.starts_at >= group_end:
            groups.append([match])
            group_end = match.ends_at
            continue
        groups[-1].append(match)
        if match.ends_at > group_end:
            group_end = match.ends_at
    return groups


def _candidate_matches(
    sanction: PlayerSanction,
    activation_boundary: datetime,
) -> list[Match]:
    frozen_team_ids = [
        entry.team_id for entry in sanction.frozen_team_entries
    ]
    if not frozen_team_ids:
        return []
    return list(
        db.session.execute(
            db.select(Match)
            .where(
                Match.id != sanction.source_match_id,
                Match.starts_at.is_not(None),
                Match.ends_at.is_not(None),
                Match.starts_at > activation_boundary,
                or_(
                    Match.team_1_id.in_(frozen_team_ids),
                    Match.team_2_id.in_(frozen_team_ids),
                ),
            )
            .order_by(Match.starts_at, Match.ends_at, Match.id)
        ).scalars()
    )


def _reconcile_locked_player_sanctions(
    player: Player,
    sanctions: list[PlayerSanction],
    consumed_match_ids: set[int],
    now: datetime,
) -> bool:
    """Consume completed qualifying Match groups for one already locked Player."""
    has_active_sanction = False
    preceding_served_at: datetime | None = None

    for sanction in sanctions:
        if sanction.status == "annulled":
            continue
        if sanction.status == "served":
            preceding_served_at = sanction.served_at
            continue

        has_active_sanction = True
        activation_boundary = sanction.effective_at
        if (
            preceding_served_at is not None
            and preceding_served_at > activation_boundary
        ):
            activation_boundary = preceding_served_at

        candidates = _candidate_matches(sanction, activation_boundary)
        completed_groups = []
        for group in _overlapping_match_groups(candidates):
            canonical_match = group[0]
            completed_at = max(match.ends_at for match in group)
            if completed_at <= now and canonical_match.id not in consumed_match_ids:
                completed_groups.append((completed_at, canonical_match))
        completed_groups.sort(key=lambda item: (item[0], item[1].id))

        for completed_at, canonical_match in completed_groups[
            : sanction.remaining_matches
        ]:
            db.session.add(
                PlayerSanctionMatchConsumption(
                    sanction_id=sanction.id,
                    player_id=player.id,
                    match_id=canonical_match.id,
                )
            )
            consumed_match_ids.add(canonical_match.id)
            sanction.remaining_matches -= 1
            if sanction.remaining_matches == 0:
                sanction.status = "served"
                sanction.served_at = completed_at
                preceding_served_at = completed_at
                has_active_sanction = False
                break

        if sanction.status == "active":
            break

    return has_active_sanction


def reconcile_player_sanctions(
    player_id: int,
    now: datetime | None = None,
) -> bool:
    """Reconcile one Player without committing the caller's transaction."""
    return reconcile_player_sanctions_batch([player_id], now=now).get(
        player_id, False
    )


def reconcile_player_sanctions_batch(
    player_ids: list[int],
    now: datetime | None = None,
) -> dict[int, bool]:
    """Reconcile sorted unique Players while preserving caller transaction control."""
    identifiers = sorted(set(player_ids))
    if not identifiers:
        return {}
    current_time = utc_now() if now is None else now
    players = list(
        db.session.execute(
            db.select(Player)
            .where(Player.id.in_(identifiers))
            .order_by(Player.id)
            .with_for_update(of=Player)
        ).scalars()
    )
    players_by_id = {player.id: player for player in players}
    sanctions_by_player = {player_id: [] for player_id in identifiers}
    sanctions = list(
        db.session.execute(
            db.select(PlayerSanction)
            .options(selectinload(PlayerSanction.frozen_team_entries))
            .where(PlayerSanction.player_id.in_(identifiers))
            .order_by(
                PlayerSanction.player_id,
                PlayerSanction.created_at,
                PlayerSanction.id,
            )
            .with_for_update(of=PlayerSanction)
        ).scalars()
    )
    for sanction in sanctions:
        sanctions_by_player[sanction.player_id].append(sanction)

    consumed_matches_by_player = {player_id: set() for player_id in identifiers}
    for player_id, match_id in db.session.execute(
        db.select(
            PlayerSanctionMatchConsumption.player_id,
            PlayerSanctionMatchConsumption.match_id,
        ).where(PlayerSanctionMatchConsumption.player_id.in_(identifiers))
    ):
        consumed_matches_by_player[player_id].add(match_id)

    result = {}
    for player_id in identifiers:
        player = players_by_id.get(player_id)
        if player is None:
            raise SanctionServiceError(
                "player_not_found", "The Player does not exist.", 404
            )
        result[player_id] = _reconcile_locked_player_sanctions(
            player,
            sanctions_by_player[player_id],
            consumed_matches_by_player[player_id],
            current_time,
        )
    return result


def get_sanction(sanction_id: int) -> PlayerSanction:
    sanction = db.session.execute(
        db.select(PlayerSanction)
        .options(*_sanction_options())
        .where(PlayerSanction.id == sanction_id)
    ).scalar_one_or_none()
    if sanction is None:
        raise SanctionServiceError(
            "sanction_not_found", "The Player sanction does not exist.", 404
        )
    return sanction


def _sanction_reference(sanction_id: int) -> PlayerSanction:
    sanction = db.session.execute(
        db.select(PlayerSanction).where(PlayerSanction.id == sanction_id)
    ).scalar_one_or_none()
    if sanction is None:
        raise SanctionServiceError(
            "sanction_not_found", "The Player sanction does not exist.", 404
        )
    return sanction


def _locked_sanction(sanction_id: int) -> PlayerSanction:
    sanction = db.session.execute(
        db.select(PlayerSanction)
        .where(PlayerSanction.id == sanction_id)
        .with_for_update(of=PlayerSanction)
    ).scalar_one_or_none()
    if sanction is None:
        raise SanctionServiceError(
            "sanction_not_found", "The Player sanction does not exist.", 404
        )
    return sanction


def _reconcile_sanction(sanction_id: int) -> PlayerSanction:
    reference = _sanction_reference(sanction_id)
    reconcile_player_sanctions(reference.player_id)
    return _locked_sanction(sanction_id)


def list_player_sanctions(
    player_id: int, query: PlayerSanctionListQuery
) -> PlayerSanctionListResult:
    """Return a reconciled, bounded page of one Player's sanctions."""
    try:
        reconcile_player_sanctions(player_id)
        db.session.commit()
        filters = [PlayerSanction.player_id == player_id]
        if query.status is not None:
            filters.append(PlayerSanction.status == query.status)
        if query.created_from is not None:
            filters.append(PlayerSanction.created_at >= query.created_from)
        if query.created_to is not None:
            filters.append(PlayerSanction.created_at <= query.created_to)

        total_items = db.session.execute(
            db.select(func.count()).select_from(PlayerSanction).where(*filters)
        ).scalar_one()
        total_pages = (
            (total_items + SANCTIONS_PER_PAGE - 1) // SANCTIONS_PER_PAGE
            if total_items
            else 0
        )
        sanctions = list(
            db.session.execute(
                db.select(PlayerSanction)
                .options(*_sanction_options())
                .where(*filters)
                .order_by(PlayerSanction.created_at.desc(), PlayerSanction.id.desc())
                .limit(SANCTIONS_PER_PAGE)
                .offset((query.page - 1) * SANCTIONS_PER_PAGE)
            ).scalars()
        )
    except Exception:
        db.session.rollback()
        raise
    return PlayerSanctionListResult(
        sanctions=sanctions,
        page=query.page,
        per_page=SANCTIONS_PER_PAGE,
        total_items=total_items,
        total_pages=total_pages,
    )


def get_reconciled_sanction(sanction_id: int) -> PlayerSanction:
    """Return one sanction after applying completed Match consumptions."""
    try:
        sanction = _reconcile_sanction(sanction_id)
        resolved_sanction_id = sanction.id
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return get_sanction(resolved_sanction_id)


def _authorize_sanction_edit(
    sanction: PlayerSanction, actor_user_id: int, actor_role: str
) -> None:
    if actor_role == ADMIN_USER_ROLE:
        return
    if actor_role != DEFAULT_USER_ROLE:
        raise SanctionServiceError(
            "application_role_required",
            "A supported application role is required.",
            403,
        )
    if sanction.created_by_user_id != actor_user_id:
        raise SanctionServiceError(
            "sanction_owner_required",
            "Only the creating referee may edit this sanction.",
            403,
        )
    reconcile_matches(utc_now(), match_id=sanction.source_match_id)
    source_match = _lock_match(sanction.source_match_id)
    if source_match.status != "in_progress":
        raise SanctionServiceError(
            "sanction_edit_window_closed",
            "A referee may edit only while the source Match is in progress.",
        )


def update_sanction(
    sanction_id: int,
    data: PlayerSanctionUpdateRequest,
    *,
    actor_user_id: int,
    actor_role: str,
) -> PlayerSanction:
    """Edit permitted sanction fields without mutating audit history."""
    try:
        sanction = _reconcile_sanction(sanction_id)
        if sanction.status != "active":
            raise SanctionServiceError(
                "sanction_not_active",
                "Only an active sanction may be edited.",
            )
        _authorize_sanction_edit(sanction, actor_user_id, actor_role)
        consumption_count = db.session.execute(
            db.select(func.count())
            .select_from(PlayerSanctionMatchConsumption)
            .where(PlayerSanctionMatchConsumption.sanction_id == sanction.id)
        ).scalar_one()
        if (
            consumption_count
            and data.suspension_matches != sanction.suspension_matches
        ):
            raise SanctionServiceError(
                "sanction_duration_locked",
                "The suspension duration cannot change after consumption.",
            )
        sanction.title = data.title
        sanction.reason = data.reason
        if not consumption_count:
            sanction.suspension_matches = data.suspension_matches
            sanction.remaining_matches = data.suspension_matches
        sanction.updated_by_user_id = actor_user_id
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return get_sanction(sanction.id)


def annul_sanction(sanction_id: int, *, actor_user_id: int) -> PlayerSanction:
    """Annul an active sanction while retaining all audit records."""
    try:
        sanction = _reconcile_sanction(sanction_id)
        if sanction.status == "served":
            raise SanctionServiceError(
                "sanction_already_served",
                "A served sanction cannot be annulled.",
            )
        if sanction.status == "annulled":
            raise SanctionServiceError(
                "sanction_already_annulled",
                "This sanction has already been annulled.",
            )
        sanction.status = "annulled"
        sanction.annulled_at = utc_now()
        sanction.annulled_by_user_id = actor_user_id
        sanction.updated_by_user_id = actor_user_id
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return get_sanction(sanction.id)


def create_match_origin_sanction(
    match_id: int,
    player_id: int,
    data: PlayerSanctionCreateRequest,
    *,
    actor_user_id: int,
    actor_role: str,
) -> PlayerSanction:
    """Create a global sanction and its immutable Team snapshot atomically."""
    try:
        # This order is shared for all creation attempts to avoid lock inversions.
        match = _lock_match(match_id)
        player = _lock_player(player_id)
        reconcile_matches(utc_now(), match_id=match.id)
        if (
            match.status != "in_progress"
            or match.starts_at is None
            or match.ends_at is None
        ):
            raise SanctionServiceError(
                "match_not_in_progress",
                "The source Match must be in progress.",
            )

        reconcile_player_sanctions(player.id)
        source_team_id = _source_roster_team_id(match, player.id)
        _authorize_actor(match, actor_user_id, actor_role)
        frozen_team_ids = sorted(
            set(_locked_current_team_ids(player.id)) | {source_team_id}
        )
        sanction = PlayerSanction(
            player_id=player.id,
            source_match_id=match.id,
            title=data.title,
            reason=data.reason,
            suspension_matches=data.suspension_matches,
            remaining_matches=data.suspension_matches,
            status="active",
            effective_at=match.ends_at,
            created_by_user_id=actor_user_id,
            updated_by_user_id=actor_user_id,
            frozen_team_entries=[
                PlayerSanctionTeam(team_id=team_id)
                for team_id in frozen_team_ids
            ],
        )
        db.session.add(sanction)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return get_sanction(sanction.id)
