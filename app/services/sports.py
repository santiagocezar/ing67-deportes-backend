import unicodedata
from typing import Any, Mapping

from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Sport


class SportValidationError(ValueError):
    """Raised when sport input does not satisfy validation rules."""


class DuplicateSportNameError(ValueError):
    """Raised when a normalized sport name already exists."""


class SportNotFoundError(LookupError):
    """Raised when a sport id does not exist."""


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
    if not 1 <= value <= 20:
        raise SportValidationError(
            "max_players must be between 1 and 20."
        )
    return value


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


def create_sport(data: Mapping[str, Any]) -> Sport:
    display_name, normalized_name = _normalize_name(data.get("name"))
    max_players = _validate_max_players(data.get("max_players"))

    duplicate_id = db.session.execute(
        db.select(Sport.id).where(Sport.normalized_name == normalized_name)
    ).scalar_one_or_none()
    if duplicate_id is not None:
        raise DuplicateSportNameError("A sport with that name already exists.")

    sport = Sport(
        name=display_name,
        normalized_name=normalized_name,
        max_players=max_players,
    )
    db.session.add(sport)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise DuplicateSportNameError(
            "A sport with that name already exists."
        ) from error
    except Exception:
        db.session.rollback()
        raise

    return sport


def update_sport_name(sport_id: int, name: Any) -> Sport:
    sport = get_sport(sport_id)
    display_name, normalized_name = _normalize_name(name)

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
        raise DuplicateSportNameError(
            "A sport with that name already exists."
        ) from error
    except Exception:
        db.session.rollback()
        raise

    return sport


def delete_sport(sport_id: int) -> None:
    sport = get_sport(sport_id)
    db.session.delete(sport)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
