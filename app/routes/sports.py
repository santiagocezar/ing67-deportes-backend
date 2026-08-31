from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from ..authorization import roles_required
from ..errors import error_response
from ..models import ADMIN_USER_ROLE, FEDERATION_DELEGATE_USER_ROLE
from ..services.sports import (
    DuplicateSportNameError,
    SportNotFoundError,
    SportInUseError,
    SportValidationError,
    create_sport,
    delete_sport,
    get_sport,
    list_sports,
    update_sport_name,
)


sports_bp = Blueprint("sports", __name__, url_prefix="/sports")

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
@roles_required(ADMIN_USER_ROLE, FEDERATION_DELEGATE_USER_ROLE)
def get_sports():
    try:
        sports = list_sports()
    except SQLAlchemyError:
        return _database_unavailable("list")
    return jsonify(sports=[sport.to_dict() for sport in sports]), 200


@sports_bp.post("")
@roles_required(ADMIN_USER_ROLE)
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
@roles_required(ADMIN_USER_ROLE, FEDERATION_DELEGATE_USER_ROLE)
def get_sport_by_id(sport_id: int):
    try:
        sport = get_sport(sport_id)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")
    return jsonify(sport=sport.to_dict()), 200


@sports_bp.put("/<int:sport_id>")
@roles_required(ADMIN_USER_ROLE)
def put_sport(sport_id: int):
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )
    immutable_fields = {
        "max_players",
        "match_duration",
        "resolution_methods",
    }
    supplied_immutable_fields = sorted(immutable_fields.intersection(data))
    if supplied_immutable_fields:
        return error_response(
            "immutable_field",
            f"{', '.join(supplied_immutable_fields)} cannot be modified "
            "after sport creation.",
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
@roles_required(ADMIN_USER_ROLE)
def remove_sport(sport_id: int):
    try:
        delete_sport(sport_id)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SportInUseError as error:
        return error_response("sport_in_use", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("delete")
    return "", 204
