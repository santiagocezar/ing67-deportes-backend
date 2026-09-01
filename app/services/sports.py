import unicodedata
from typing import Any

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Sport, Team
from ..schemas.sports import SportCreateRequest, SportUpdateRequest
from .database import constraint_name


class SportValidationError(ValueError):
    """Raised when sport input does not satisfy validation rules."""


class DuplicateSportNameError(ValueError):
    """Raised when a normalized sport name already exists."""


class SportNotFoundError(LookupError):
    """Raised when a sport id does not exist."""


class SportInUseError(RuntimeError):
    """Raised when a Team still references a Sport."""


SPORT_NAME_CONSTRAINTS = {
    "uq_sports_name",
    "uq_sports_normalized_name",
}
TEAM_SPORT_FOREIGN_KEY = "fk_teams_sport_id_sports"


def _normalize_name(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        raise SportValidationError("name must be a string.")

    compact_name = " ".join(value.split())
    if not compact_name:
        raise SportValidationError("name is required.")

    display_name = compact_name.capitalize()
    if len(display_name) > 100:
        raise SportValidationError("name must contain at most 100 characters.")

    decomposed_name = unicodedata.normalize("NFKD", display_name.casefold())
    normalized_name = "".join(
        character
        for character in decomposed_name
        if not unicodedata.combining(character)
    )
    return display_name, normalized_name


def _validate_max_players(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SportValidationError("max_players must be an integer.")
    if value <= 0:
        raise SportValidationError("max_players must be greater than zero.")
    return value


def _validate_capacities(
    max_players: Any,
    max_players_in_game: Any,
) -> tuple[int, int]:
    total_capacity = _validate_max_players(max_players)
    if isinstance(max_players_in_game, bool) or not isinstance(
        max_players_in_game,
        int,
    ):
        raise SportValidationError(
            "max_players_in_game must be an integer."
        )
    if max_players_in_game <= 0:
        raise SportValidationError(
            "max_players_in_game must be greater than zero."
        )
    if max_players_in_game > total_capacity:
        raise SportValidationError(
            "max_players_in_game cannot exceed max_players."
        )
    return total_capacity, max_players_in_game


def list_sports() -> list[Sport]:
    return list(
        db.session.execute(
            db.select(Sport).order_by(Sport.id)
        ).scalars()
    )


def get_sport(sport_id: int) -> Sport:
    sport = db.session.get(Sport, sport_id)
    if sport is None:
        raise SportNotFoundError("The sport does not exist.")
    return sport


def create_sport(data: SportCreateRequest) -> Sport:
    display_name, normalized_name = _normalize_name(data.name)
    max_players, max_players_in_game = _validate_capacities(
        data.max_players,
        data.max_players_in_game,
    )

    duplicate_id = db.session.execute(
        db.select(Sport.id).where(Sport.normalized_name == normalized_name)
    ).scalar_one_or_none()
    if duplicate_id is not None:
        raise DuplicateSportNameError("A sport with that name already exists.")

    sport = Sport(
        name=display_name,
        normalized_name=normalized_name,
        max_players=max_players,
        max_players_in_game=max_players_in_game,
    )
    db.session.add(sport)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if constraint_name(error) in SPORT_NAME_CONSTRAINTS:
            raise DuplicateSportNameError(
                "A sport with that name already exists."
            ) from error
        raise
    except Exception:
        db.session.rollback()
        raise

    return sport


def update_sport_name(
    sport_id: int,
    data: SportUpdateRequest,
) -> Sport:
    sport = get_sport(sport_id)
    display_name, normalized_name = _normalize_name(data.name)

    duplicate_id = db.session.execute(
        db.select(Sport.id).where(
            Sport.normalized_name == normalized_name,
            Sport.id != sport.id,
        )
    ).scalar_one_or_none()
    if duplicate_id is not None:
        raise DuplicateSportNameError("A sport with that name already exists.")

    sport.name = display_name
    sport.normalized_name = normalized_name

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if constraint_name(error) in SPORT_NAME_CONSTRAINTS:
            raise DuplicateSportNameError(
                "A sport with that name already exists."
            ) from error
        raise
    except Exception:
        db.session.rollback()
        raise

    return sport


def delete_sport(sport_id: int) -> None:
    sport = get_sport(sport_id)
    referenced_team_id = db.session.execute(
        db.select(Team.id).where(Team.sport_id == sport_id).limit(1)
    ).scalar_one_or_none()
    if referenced_team_id is not None:
        raise SportInUseError(
            "The sport cannot be deleted while Teams reference it."
        )

    db.session.delete(sport)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if constraint_name(error) == TEAM_SPORT_FOREIGN_KEY:
            raise SportInUseError(
                "The sport cannot be deleted while Teams reference it."
            ) from error
        raise
    except Exception:
        db.session.rollback()
        raise
