from functools import wraps

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, jwt_required
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response
from ..models import ADMIN_USER_ROLE
from ..services.sports import (
    DuplicateSportNameError,
    SportNotFoundError,
    SportValidationError,
    create_sport,
    delete_sport,
    get_sport,
    list_sports,
    update_sport_name,
)


sports_bp = Blueprint("sports", __name__, url_prefix="/sports")

def administrator_required(function):
    """Require a valid access token whose role is administrator."""

    @wraps(function)
    @jwt_required()
    def wrapper(*args, **kwargs):
        if get_jwt().get("role") != ADMIN_USER_ROLE:
            return error_response(
                "administrator_required",
                "Administrator permissions are required.",
                403,
            )
        return function(*args, **kwargs)

    return wrapper


def _json_body() -> dict | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _database_unavailable(operation: str):
    current_app.logger.exception("Could not %s sport", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


@sports_bp.get("")
@administrator_required
def get_sports():
    try:
        sports = list_sports()
    except SQLAlchemyError:
        return _database_unavailable("list")
    return jsonify(sports=[sport.to_dict() for sport in sports]), 200


@sports_bp.post("")
@administrator_required
def post_sport():
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    try:
        sport = create_sport(data)
    except SportValidationError as error:
        return error_response("validation_error", str(error), 422)
    except DuplicateSportNameError as error:
        return error_response("sport_name_conflict", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("create")

    return jsonify(sport=sport.to_dict()), 201


@sports_bp.get("/<int:sport_id>")
@administrator_required
def get_sport_by_id(sport_id: int):
    try:
        sport = get_sport(sport_id)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")
    return jsonify(sport=sport.to_dict()), 200


@sports_bp.put("/<int:sport_id>")
@administrator_required
def put_sport(sport_id: int):
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )
    if "max_players" in data:
        return error_response(
            "immutable_field",
            "max_players cannot be modified after sport creation.",
            422,
        )
    if set(data) - {"name"}:
        return error_response(
            "validation_error",
            "Only name can be modified.",
            422,
        )

    try:
        sport = update_sport_name(sport_id, data.get("name"))
    except SportValidationError as error:
        return error_response("validation_error", str(error), 422)
    except DuplicateSportNameError as error:
        return error_response("sport_name_conflict", str(error), 409)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("update")

    return jsonify(sport=sport.to_dict()), 200


@sports_bp.delete("/<int:sport_id>")
@administrator_required
def remove_sport(sport_id: int):
    try:
        delete_sport(sport_id)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("delete")
    return "", 204
