from dataclasses import dataclass
from datetime import datetime, timezone
import unicodedata
from typing import Any, Mapping

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Sport, Team


TEAM_PAGE_SIZE = 25
TEAM_GENDER_CATEGORIES = ("male", "female")
TEAM_STATUSES = ("enabled", "disabled", "all")
TEAM_SORT_FIELDS = ("name", "created_at")
TEAM_SORT_ORDERS = ("asc", "desc")

_TEAM_CREATE_FIELDS = {"name", "sport_id", "gender_category"}
_TEAM_UPDATE_FIELDS = {"name"}
_TEAM_QUERY_FIELDS = {
    "page",
    "search",
    "sport_id",
    "gender_category",
    "status",
    "sort",
    "order",
}


class TeamValidationError(ValueError):
    """Raised when Team input does not satisfy validation rules."""


class DuplicateTeamNameError(ValueError):
    """Raised when a normalized Team name conflicts in its scope."""


class TeamNotFoundError(LookupError):
    """Raised when a Team id does not exist."""


class TeamSportNotFoundError(LookupError):
    """Raised when Team creation references a missing Sport."""


class TeamDisabledError(ValueError):
    """Raised when attempting to edit a disabled Team."""


class TeamAlreadyEnabledError(ValueError):
    """Raised when enabling an already enabled Team."""


class TeamAlreadyDisabledError(ValueError):
    """Raised when disabling an already disabled Team."""


@dataclass(frozen=True)
class TeamPage:
    teams: list[Team]
    page: int
    total_items: int
    total_pages: int
    page_size: int = TEAM_PAGE_SIZE


def _normalized_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def normalize_team_name(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise TeamValidationError("name must be a string.")

    display_name = " ".join(value.split())
    if not display_name:
        raise TeamValidationError("name is required.")
    if len(display_name) > 100:
        raise TeamValidationError("name must contain at most 100 characters.")

    return display_name, _normalized_text(display_name)


def _reject_unexpected_fields(
    data: Mapping[str, Any],
    allowed_fields: set[str],
    *,
    label: str = "fields",
) -> None:
    unexpected_fields = sorted(
        str(field) for field in set(data) - allowed_fields
    )
    if unexpected_fields:
        raise TeamValidationError(
            f"Unexpected {label}: {', '.join(unexpected_fields)}."
        )


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TeamValidationError(f"{field} must be a positive integer.")
    if value <= 0:
        raise TeamValidationError(f"{field} must be a positive integer.")
    return value


def _query_positive_integer(value: Any, field: str) -> int:
    if not isinstance(value, str) or not value.isdigit():
        raise TeamValidationError(f"{field} must be a positive integer.")
    parsed_value = int(value)
    if parsed_value <= 0:
        raise TeamValidationError(f"{field} must be a positive integer.")
    return parsed_value


def _validate_gender_category(value: Any) -> str:
    if value not in TEAM_GENDER_CATEGORIES:
        raise TeamValidationError(
            "gender_category must be male or female."
        )
    return value


def _find_duplicate_team_id(
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


def _team_statement():
    return db.select(Team).options(joinedload(Team.sport))


def create_team(data: Mapping[str, Any]) -> Team:
    _reject_unexpected_fields(data, _TEAM_CREATE_FIELDS)
    display_name, normalized_name = normalize_team_name(data.get("name"))
    sport_id = _positive_integer(data.get("sport_id"), "sport_id")
    gender_category = _validate_gender_category(data.get("gender_category"))

    sport = db.session.get(Sport, sport_id)
    if sport is None:
        raise TeamSportNotFoundError("The sport does not exist.")
    if _find_duplicate_team_id(
        normalized_name,
        sport_id,
        gender_category,
    ) is not None:
        raise DuplicateTeamNameError(
            "A Team with that name already exists for the Sport and gender."
        )

    team = Team(
        name=display_name,
        normalized_name=normalized_name,
        sport=sport,
        gender_category=gender_category,
    )
    db.session.add(team)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if db.session.get(Sport, sport_id) is None:
            raise TeamSportNotFoundError("The sport does not exist.") from error
        raise DuplicateTeamNameError(
            "A Team with that name already exists for the Sport and gender."
        ) from error
    except Exception:
        db.session.rollback()
        raise

    return team


def get_team(team_id: int) -> Team:
    team = db.session.execute(
        _team_statement().where(Team.id == team_id)
    ).scalar_one_or_none()
    if team is None:
        raise TeamNotFoundError("The Team does not exist.")
    return team


def update_team_name(team_id: int, data: Mapping[str, Any]) -> Team:
    team = get_team(team_id)
    if not team.is_enabled:
        raise TeamDisabledError("A disabled Team cannot be edited.")

    _reject_unexpected_fields(data, _TEAM_UPDATE_FIELDS)
    display_name, normalized_name = normalize_team_name(data.get("name"))
    if _find_duplicate_team_id(
        normalized_name,
        team.sport_id,
        team.gender_category,
        excluded_team_id=team.id,
    ) is not None:
        raise DuplicateTeamNameError(
            "A Team with that name already exists for the Sport and gender."
        )

    team.name = display_name
    team.normalized_name = normalized_name
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise DuplicateTeamNameError(
            "A Team with that name already exists for the Sport and gender."
        ) from error
    except Exception:
        db.session.rollback()
        raise
    return team


def set_team_enabled(team_id: int, *, is_enabled: bool) -> Team:
    team = db.session.execute(
        _team_statement()
        .where(Team.id == team_id)
        .with_for_update()
    ).scalar_one_or_none()
    if team is None:
        raise TeamNotFoundError("The Team does not exist.")
    if is_enabled and team.is_enabled:
        raise TeamAlreadyEnabledError("The Team is already enabled.")
    if not is_enabled and not team.is_enabled:
        raise TeamAlreadyDisabledError("The Team is already disabled.")

    team.is_enabled = is_enabled
    team.disabled_at = None if is_enabled else datetime.now(timezone.utc)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return team


def list_teams(query: Mapping[str, Any]) -> TeamPage:
    _reject_unexpected_fields(query, _TEAM_QUERY_FIELDS, label="query parameters")

    page = _query_positive_integer(query.get("page", "1"), "page")
    status = query.get("status", "enabled")
    if status not in TEAM_STATUSES:
        raise TeamValidationError(
            "status must be enabled, disabled, or all."
        )
    sort = query.get("sort", "name")
    if sort not in TEAM_SORT_FIELDS:
        raise TeamValidationError("sort must be name or created_at.")
    order = query.get("order", "asc")
    if order not in TEAM_SORT_ORDERS:
        raise TeamValidationError("order must be asc or desc.")

    conditions = []
    search = query.get("search")
    if search is not None:
        if not isinstance(search, str):
            raise TeamValidationError("search must be a string.")
        compact_search = " ".join(search.split())
        if compact_search:
            conditions.append(
                Team.normalized_name.contains(
                    _normalized_text(compact_search),
                    autoescape=True,
                )
            )

    sport_id_value = query.get("sport_id")
    if sport_id_value is not None:
        conditions.append(
            Team.sport_id
            == _query_positive_integer(sport_id_value, "sport_id")
        )

    gender_category = query.get("gender_category")
    if gender_category is not None:
        conditions.append(
            Team.gender_category == _validate_gender_category(gender_category)
        )

    if status == "enabled":
        conditions.append(Team.is_enabled.is_(True))
    elif status == "disabled":
        conditions.append(Team.is_enabled.is_(False))

    total_items = db.session.execute(
        db.select(func.count(Team.id)).where(*conditions)
    ).scalar_one()
    total_pages = (
        (total_items + TEAM_PAGE_SIZE - 1) // TEAM_PAGE_SIZE
        if total_items
        else 0
    )

    sort_column = Team.normalized_name if sort == "name" else Team.created_at
    ordering = sort_column.asc if order == "asc" else sort_column.desc
    id_ordering = Team.id.asc if order == "asc" else Team.id.desc
    statement = (
        _team_statement()
        .where(*conditions)
        .order_by(ordering(), id_ordering())
        .limit(TEAM_PAGE_SIZE)
        .offset((page - 1) * TEAM_PAGE_SIZE)
    )
    teams = list(db.session.execute(statement).scalars().unique())

    return TeamPage(
        teams=teams,
        page=page,
        total_items=total_items,
        total_pages=total_pages,
    )
