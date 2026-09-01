import re
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


_SPORT_CREATE_FIELDS = {
    "name",
    "max_players",
    "match_duration",
    "resolution_methods",
}
_RESOLUTION_METHOD_FIELDS = {"code", "name"}
_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


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


def _validate_match_duration(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SportValidationError("match_duration must be an integer.")
    if value <= 0:
        raise SportValidationError("match_duration must be greater than zero.")
    return value


def _validate_resolution_methods(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise SportValidationError(
            "resolution_methods must be a non-empty array."
        )

    validated_methods: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()

    for index, method in enumerate(value):
        if not isinstance(method, dict):
            raise SportValidationError(
                f"resolution_methods[{index}] must be an object."
            )
        if set(method) != _RESOLUTION_METHOD_FIELDS:
            raise SportValidationError(
                f"resolution_methods[{index}] must contain only code and name."
            )

        code = method.get("code")
        name = method.get("name")
        if not isinstance(code, str) or not code.strip():
            raise SportValidationError(
                f"resolution_methods[{index}].code must be a non-blank string."
            )
        code = code.strip()
        if not _SNAKE_CASE_PATTERN.fullmatch(code):
            raise SportValidationError(
                f"resolution_methods[{index}].code must use snake_case."
            )
        if not isinstance(name, str) or not name.strip():
            raise SportValidationError(
                f"resolution_methods[{index}].name must be a non-blank string."
            )
        display_name = " ".join(name.split())
        normalized_name = display_name.casefold()

        if code in seen_codes:
            raise SportValidationError(
                "resolution method codes must be unique within a sport."
            )
        if normalized_name in seen_names:
            raise SportValidationError(
                "resolution method names must be unique within a sport."
            )

        seen_codes.add(code)
        seen_names.add(normalized_name)
        validated_methods.append({"code": code, "name": display_name})

    return validated_methods


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
    unexpected_fields = sorted(
        str(field) for field in set(data) - _SPORT_CREATE_FIELDS
    )
    if unexpected_fields:
        raise SportValidationError(
            f"Unexpected fields: {', '.join(unexpected_fields)}."
        )

    display_name, normalized_name = _normalize_name(data.get("name"))
    max_players = _validate_max_players(data.get("max_players"))
    match_duration = _validate_match_duration(data.get("match_duration"))
    resolution_methods = _validate_resolution_methods(
        data.get("resolution_methods")
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
        match_duration=match_duration,
        resolution_methods=resolution_methods,
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
