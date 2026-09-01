from functools import wraps

from flask import current_app, jsonify
from flask_jwt_extended import get_jwt, jwt_required
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..models import ADMIN_USER_ROLE
from ..schemas.common import ErrorResponse
from ..schemas.sports import (
    SportCreateRequest,
    SportEnvelope,
    SportListResponse,
    SportPath,
    SportResponse,
    SportUpdateRequest,
)
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


SPORTS_TAG = Tag(
    name="Sports",
    description="Administrator-only sport catalogue management.",
)
ACCESS_SECURITY = [{"AccessTokenAuth": []}]

sports_bp = APIBlueprint(
    "sports",
    __name__,
    url_prefix="/sports",
    abp_tags=[SPORTS_TAG],
)


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


def _database_unavailable(operation: str):
    current_app.logger.error("Could not %s sport", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


@sports_bp.get(
    "",
    summary="List sports",
    description="Returns every sport ordered by identifier.",
    operation_id="sportsList",
    security=ACCESS_SECURITY,
    responses={
        200: SportListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_sports():
    try:
        sports = list_sports()
    except SQLAlchemyError:
        return _database_unavailable("list")

    payload = SportListResponse(
        sports=[SportResponse.model_validate(sport) for sport in sports]
    )
    return jsonify(payload.model_dump(mode="json")), 200


@sports_bp.post(
    "",
    summary="Create a sport",
    description=(
        "Creates a uniquely normalized sport with a maximum of 1 to 20 "
        "players per team."
    ),
    operation_id="sportsCreate",
    security=ACCESS_SECURITY,
    responses={
        201: SportEnvelope,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@json_object_required
@validate_request()
def post_sport(body: SportCreateRequest):
    try:
        sport = create_sport(body)
    except SportValidationError as error:
        return error_response("validation_error", str(error), 422)
    except DuplicateSportNameError as error:
        return error_response("sport_name_conflict", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("create")

    payload = SportEnvelope(sport=SportResponse.model_validate(sport))
    return jsonify(payload.model_dump(mode="json")), 201


@sports_bp.get(
    "/<int:sport_id>",
    summary="Get a sport",
    description="Returns one sport by identifier.",
    operation_id="sportsGet",
    security=ACCESS_SECURITY,
    responses={
        200: SportEnvelope,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_sport_by_id(path: SportPath):
    try:
        sport = get_sport(path.sport_id)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")

    payload = SportEnvelope(sport=SportResponse.model_validate(sport))
    return jsonify(payload.model_dump(mode="json")), 200


@sports_bp.put(
    "/<int:sport_id>",
    summary="Rename a sport",
    description="Changes only the sport name; max_players is immutable.",
    operation_id="sportsUpdate",
    security=ACCESS_SECURITY,
    responses={
        200: SportEnvelope,
        400: ErrorResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@json_object_required
@validate_request()
def put_sport(path: SportPath, body: SportUpdateRequest):
    try:
        sport = update_sport_name(path.sport_id, body)
    except SportValidationError as error:
        return error_response("validation_error", str(error), 422)
    except DuplicateSportNameError as error:
        return error_response("sport_name_conflict", str(error), 409)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("update")

    payload = SportEnvelope(sport=SportResponse.model_validate(sport))
    return jsonify(payload.model_dump(mode="json")), 200


@sports_bp.delete(
    "/<int:sport_id>",
    summary="Delete a sport",
    description="Deletes one sport by identifier.",
    operation_id="sportsDelete",
    security=ACCESS_SECURITY,
    responses={
        204: None,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def remove_sport(path: SportPath):
    try:
        delete_sport(path.sport_id)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("delete")
    return "", 204
