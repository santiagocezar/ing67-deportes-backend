import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import Player, Sport, Team, team_players
from ..schemas.players import (
    PlayerCreateRequest,
    PlayerListQuery,
    PlayerUpdateRequest,
)
from .database import constraint_name
from .sports import SportNotFoundError
from .teams import TeamDisabledError, TeamNotFoundError


PLAYER_PAGE_SIZE = 25
PLAYER_SPORT_FOREIGN_KEY = "fk_players_sport_id_sports"
TEAM_PLAYER_TEAM_FOREIGN_KEY = "fk_team_players_team_id_teams"
TEAM_PLAYER_PLAYER_FOREIGN_KEY = "fk_team_players_player_id_players"
MAX_PLAYER_TEAMS = 3


class PlayerValidationError(ValueError):
    """Raised when Player input does not satisfy its domain rules."""


class PlayerNotFoundError(LookupError):
    """Raised when a Player id does not exist."""


class PlayerDisabledError(RuntimeError):
    """Raised when an operation requires an enabled Player."""


class TeamSportMismatchError(ValueError):
    """Raised when a Team and Player belong to different Sports."""


class TeamGenderMismatchError(ValueError):
    """Raised when a Team and Player use different gender categories."""


class TeamCapacityReachedError(RuntimeError):
    """Raised when a Team has reached its Player capacity."""


@dataclass(frozen=True)
class PlayerPage:
    players: list[Player]
    page: int
    per_page: int
    total_items: int
    total_pages: int


def _comparison_name(value: str) -> str:
    decomposed_name = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in decomposed_name
        if not unicodedata.combining(character)
    )


def normalize_player_name(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise PlayerValidationError("name must be a string.")

    display_name = " ".join(value.split())
    if not display_name:
        raise PlayerValidationError("name is required.")
    if len(display_name) > 100:
        raise PlayerValidationError(
            "name must contain at most 100 characters."
        )
    return display_name, _comparison_name(display_name)


def normalize_player_search(value: str | None) -> str | None:
    if value is None:
        return None
    compact_search = " ".join(value.split())
    return _comparison_name(compact_search) if compact_search else None


def _validated_team_ids(team_ids: list[int]) -> list[int]:
    if any(
        isinstance(team_id, bool)
        or not isinstance(team_id, int)
        or team_id <= 0
        for team_id in team_ids
    ):
        raise PlayerValidationError("team_ids must contain positive integers.")
    if len(team_ids) > MAX_PLAYER_TEAMS:
        raise PlayerValidationError(
            f"A Player may belong to at most {MAX_PLAYER_TEAMS} Teams."
        )
    if len(set(team_ids)) != len(team_ids):
        raise PlayerValidationError("team_ids must not contain duplicates.")
    return sorted(team_ids)


def _lock_player(player_id: int) -> Player:
    player = db.session.execute(
        db.select(Player)
        .where(Player.id == player_id)
        .with_for_update()
    ).scalar_one_or_none()
    if player is None:
        raise PlayerNotFoundError("The Player does not exist.")
    return player


def _lock_teams(team_ids: list[int]) -> list[Team]:
    if not team_ids:
        return []

    teams = list(
        db.session.execute(
            db.select(Team)
            .where(Team.id.in_(team_ids))
            .order_by(Team.id)
            .with_for_update()
        ).scalars()
    )
    if [team.id for team in teams] != team_ids:
        raise TeamNotFoundError("The Team does not exist.")
    return teams


def _validate_team_assignments(
    teams: list[Team],
    *,
    sport: Sport,
    gender: str,
    new_team_ids: set[int],
) -> None:
    for team in teams:
        if not team.is_enabled:
            raise TeamDisabledError(
                "A disabled Team cannot receive Players. Enable it first."
            )
        if team.sport_id != sport.id:
            raise TeamSportMismatchError(
                "The Team and Player must belong to the same Sport."
            )
        if team.gender_category != gender:
            raise TeamGenderMismatchError(
                "The Team and Player must use the same gender category."
            )

    if not new_team_ids:
        return
    counts = dict(
        db.session.execute(
            db.select(
                team_players.c.team_id,
                func.count(team_players.c.player_id),
            )
            .where(team_players.c.team_id.in_(new_team_ids))
            .group_by(team_players.c.team_id)
        ).all()
    )
    if any(
        counts.get(team_id, 0) >= sport.max_players
        for team_id in new_team_ids
    ):
        raise TeamCapacityReachedError(
            "The Team has reached its Player capacity."
        )


def _raise_known_integrity_error(error: IntegrityError) -> None:
    violated_constraint = constraint_name(error)
    if violated_constraint == PLAYER_SPORT_FOREIGN_KEY:
        raise SportNotFoundError("The sport does not exist.") from error
    if violated_constraint == TEAM_PLAYER_TEAM_FOREIGN_KEY:
        raise TeamNotFoundError("The Team does not exist.") from error
    if violated_constraint == TEAM_PLAYER_PLAYER_FOREIGN_KEY:
        raise PlayerNotFoundError("The Player does not exist.") from error


def get_player(player_id: int) -> Player:
    player = db.session.execute(
        db.select(Player)
        .options(
            joinedload(Player.sport),
            selectinload(Player.teams),
        )
        .where(Player.id == player_id)
    ).scalar_one_or_none()
    if player is None:
        raise PlayerNotFoundError("The Player does not exist.")
    return player


def create_player(data: PlayerCreateRequest) -> Player:
    display_name, normalized_name = normalize_player_name(data.name)
    requested_ids = _validated_team_ids(list(data.team_ids))

    try:
        sport = db.session.get(Sport, data.sport_id)
        if sport is None:
            raise SportNotFoundError("The sport does not exist.")

        teams = _lock_teams(requested_ids)
        _validate_team_assignments(
            teams,
            sport=sport,
            gender=data.gender,
            new_team_ids=set(requested_ids),
        )
        player = Player(
            name=display_name,
            normalized_name=normalized_name,
            gender=data.gender,
            sport=sport,
            is_enabled=True,
            disabled_at=None,
            teams=teams,
        )
        db.session.add(player)
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        _raise_known_integrity_error(error)
        raise
    except Exception:
        db.session.rollback()
        raise
    return player


def update_player(player_id: int, data: PlayerUpdateRequest) -> Player:
    display_name, normalized_name = normalize_player_name(data.name)
    requested_ids = _validated_team_ids(list(data.team_ids))

    try:
        player = _lock_player(player_id)
        if not player.is_enabled:
            raise PlayerDisabledError(
                "A disabled Player cannot be updated. Enable it first."
            )

        current_ids = set(
            db.session.execute(
                db.select(team_players.c.team_id).where(
                    team_players.c.player_id == player.id
                )
            ).scalars()
        )
        locked_teams = _lock_teams(
            sorted(current_ids | set(requested_ids))
        )
        teams_by_id = {team.id: team for team in locked_teams}
        teams = [teams_by_id[team_id] for team_id in requested_ids]
        _validate_team_assignments(
            teams,
            sport=player.sport,
            gender=player.gender,
            new_team_ids=set(requested_ids) - current_ids,
        )
        player.name = display_name
        player.normalized_name = normalized_name
        player.teams = teams
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        _raise_known_integrity_error(error)
        raise
    except Exception:
        db.session.rollback()
        raise
    return player


def set_player_enabled(player_id: int, *, enabled: bool) -> Player:
    try:
        player = _lock_player(player_id)
        if player.is_enabled == enabled:
            db.session.rollback()
            return player

        if not enabled:
            player.teams = []
        player.is_enabled = enabled
        player.disabled_at = None if enabled else datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return player


def list_players(query: PlayerListQuery) -> PlayerPage:
    if (
        query.sport_id is not None
        and db.session.get(Sport, query.sport_id) is None
    ):
        raise SportNotFoundError("The sport does not exist.")
    if (
        query.team_id is not None
        and db.session.get(Team, query.team_id) is None
    ):
        raise TeamNotFoundError("The Team does not exist.")

    filters = []
    normalized_search = normalize_player_search(query.search)
    if normalized_search is not None:
        filters.append(
            Player.normalized_name.contains(normalized_search, autoescape=True)
        )
    if query.sport_id is not None:
        filters.append(Player.sport_id == query.sport_id)
    if query.gender is not None:
        filters.append(Player.gender == query.gender)
    if query.team_id is not None:
        filters.append(Player.teams.any(Team.id == query.team_id))
    if query.status == "enabled":
        filters.append(Player.is_enabled.is_(True))
    elif query.status == "disabled":
        filters.append(Player.is_enabled.is_(False))

    count_statement = db.select(func.count()).select_from(Player)
    players_statement = db.select(Player).options(
        joinedload(Player.sport),
        selectinload(Player.teams),
    )
    if filters:
        count_statement = count_statement.where(*filters)
        players_statement = players_statement.where(*filters)

    if query.sort == "created_at_desc":
        players_statement = players_statement.order_by(
            Player.created_at.desc(),
            Player.id.desc(),
        )
    else:
        players_statement = players_statement.order_by(
            Player.normalized_name.asc(),
            Player.id.asc(),
        )

    total_items = db.session.execute(count_statement).scalar_one()
    players_statement = players_statement.offset(
        (query.page - 1) * PLAYER_PAGE_SIZE
    ).limit(PLAYER_PAGE_SIZE)
    players = list(db.session.execute(players_statement).scalars())
    total_pages = (
        (total_items + PLAYER_PAGE_SIZE - 1) // PLAYER_PAGE_SIZE
        if total_items
        else 0
    )
    return PlayerPage(
        players=players,
        page=query.page,
        per_page=PLAYER_PAGE_SIZE,
        total_items=total_items,
        total_pages=total_pages,
    )
