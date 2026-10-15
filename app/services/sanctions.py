from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (
    ADMIN_USER_ROLE,
    DEFAULT_USER_ROLE,
    CompetitionRosterPlayer,
    Match,
    Player,
    PlayerSanction,
    PlayerSanctionTeam,
    Team,
    team_players,
)
from ..schemas.sanctions import PlayerSanctionCreateRequest
from .competition_state import utc_now
from .matches import reconcile_matches


class SanctionServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


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


def get_sanction(sanction_id: int) -> PlayerSanction:
    return db.session.execute(
        db.select(PlayerSanction)
        .options(*_sanction_options())
        .where(PlayerSanction.id == sanction_id)
    ).scalar_one()


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
