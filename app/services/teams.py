import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Sport, Team, team_players
from ..schemas.teams import (
    TeamCreateRequest,
    TeamListQuery,
    TeamUpdateRequest,
)
from .database import constraint_name
from .sports import SportNotFoundError


TEAM_PAGE_SIZE = 25
TEAM_NAME_CONSTRAINT = "uq_teams_normalized_name_sport_gender"
TEAM_SPORT_FOREIGN_KEY = "fk_teams_sport_id_sports"


class TeamValidationError(ValueError):
    """Raised when a Team name does not satisfy its domain rules."""


class DuplicateTeamNameError(ValueError):
    """Raised when a Team identity already exists."""


class TeamNotFoundError(LookupError):
    """Raised when a Team id does not exist."""


class TeamDisabledError(RuntimeError):
    """Raised when an operation requires an enabled Team."""


@dataclass(frozen=True)
class TeamPage:
    teams: list[Team]
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


def normalize_team_name(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise TeamValidationError("name must be a string.")

    display_name = " ".join(value.split())
    if not display_name:
        raise TeamValidationError("name is required.")
    if len(display_name) > 100:
        raise TeamValidationError(
            "name must contain at most 100 characters."
        )
    return display_name, _comparison_name(display_name)


def normalize_team_search(value: str | None) -> str | None:
    if value is None:
        return None
    compact_search = " ".join(value.split())
    if not compact_search:
        return None
    return _comparison_name(compact_search)


def _duplicate_team_id(
    normalized_name: str,
    sport_id: int,
    gender_category: str,
    *,
    excluded_team_id: int | None = None,
) -> int | None:
    statement = db.select(Team.id).where(
        Team.normalized_name == normalized_name,
        Team.sport_id == sport_id,
        Team.gender_category == gender_category,
    )
    if excluded_team_id is not None:
        statement = statement.where(Team.id != excluded_team_id)
    return db.session.execute(statement).scalar_one_or_none()


def get_team(team_id: int, *, for_update: bool = False) -> Team:
    statement = (
        db.select(Team)
        .options(joinedload(Team.sport))
        .where(Team.id == team_id)
    )
    if for_update:
        statement = statement.with_for_update(of=Team)
    team = db.session.execute(statement).scalar_one_or_none()
    if team is None:
        raise TeamNotFoundError("The Team does not exist.")
    return team


def create_team(data: TeamCreateRequest) -> Team:
    sport = db.session.get(Sport, data.sport_id)
    if sport is None:
        raise SportNotFoundError("The sport does not exist.")

    display_name, normalized_name = normalize_team_name(data.name)
    if _duplicate_team_id(
        normalized_name,
        sport.id,
        data.gender_category,
    ) is not None:
        raise DuplicateTeamNameError(
            "A Team with that name already exists for this Sport and gender."
        )

    team = Team(
        name=display_name,
        normalized_name=normalized_name,
        sport=sport,
        gender_category=data.gender_category,
        is_enabled=True,
        disabled_at=None,
    )
    db.session.add(team)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        violated_constraint = constraint_name(error)
        if violated_constraint == TEAM_NAME_CONSTRAINT:
            raise DuplicateTeamNameError(
                "A Team with that name already exists for this Sport and gender."
            ) from error
        if violated_constraint == TEAM_SPORT_FOREIGN_KEY:
            raise SportNotFoundError("The sport does not exist.") from error
        raise
    except Exception:
        db.session.rollback()
        raise

    return team


def update_team_name(team_id: int, data: TeamUpdateRequest) -> Team:
    team = get_team(team_id)
    if not team.is_enabled:
        raise TeamDisabledError(
            "A disabled Team cannot be renamed. Enable it first."
        )

    display_name, normalized_name = normalize_team_name(data.name)
    if _duplicate_team_id(
        normalized_name,
        team.sport_id,
        team.gender_category,
        excluded_team_id=team.id,
    ) is not None:
        raise DuplicateTeamNameError(
            "A Team with that name already exists for this Sport and gender."
        )

    team.name = display_name
    team.normalized_name = normalized_name
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if constraint_name(error) == TEAM_NAME_CONSTRAINT:
            raise DuplicateTeamNameError(
                "A Team with that name already exists for this Sport and gender."
            ) from error
        raise
    except Exception:
        db.session.rollback()
        raise
    return team


def set_team_enabled(team_id: int, *, enabled: bool) -> Team:
    try:
        team = get_team(team_id, for_update=True)
        if team.is_enabled == enabled:
            db.session.rollback()
            return team

        if not enabled:
            db.session.execute(
                db.delete(team_players).where(
                    team_players.c.team_id == team.id
                )
            )
        team.is_enabled = enabled
        team.disabled_at = None if enabled else datetime.now(timezone.utc)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return team


def list_teams(query: TeamListQuery) -> TeamPage:
    if query.sport_id is not None:
        sport = db.session.get(Sport, query.sport_id)
        if sport is None:
            raise SportNotFoundError("The sport does not exist.")

    filters = []
    normalized_search = normalize_team_search(query.search)
    if normalized_search is not None:
        filters.append(
            Team.normalized_name.contains(
                normalized_search,
                autoescape=True,
            )
        )
    if query.sport_id is not None:
        filters.append(Team.sport_id == query.sport_id)
    if query.gender_category is not None:
        filters.append(Team.gender_category == query.gender_category)
    if query.status == "enabled":
        filters.append(Team.is_enabled.is_(True))
    elif query.status == "disabled":
        filters.append(Team.is_enabled.is_(False))

    count_statement = db.select(func.count()).select_from(Team)
    teams_statement = db.select(Team).options(joinedload(Team.sport))
    if filters:
        count_statement = count_statement.where(*filters)
        teams_statement = teams_statement.where(*filters)

    if query.sort == "created_at_desc":
        teams_statement = teams_statement.order_by(
            Team.created_at.desc(),
            Team.id.desc(),
        )
    else:
        teams_statement = teams_statement.order_by(
            Team.normalized_name.asc(),
            Team.id.asc(),
        )

    total_items = db.session.execute(count_statement).scalar_one()
    teams_statement = teams_statement.offset(
        (query.page - 1) * TEAM_PAGE_SIZE
    ).limit(TEAM_PAGE_SIZE)
    teams = list(db.session.execute(teams_statement).scalars())
    total_pages = (
        (total_items + TEAM_PAGE_SIZE - 1) // TEAM_PAGE_SIZE
        if total_items
        else 0
    )
    return TeamPage(
        teams=teams,
        page=query.page,
        per_page=TEAM_PAGE_SIZE,
        total_items=total_items,
        total_pages=total_pages,
    )
