from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..schemas.common import ErrorResponse
from ..schemas.players import (
    PlayerCreateRequest,
    PlayerListQuery,
    PlayerListResponse,
    PlayerPaginationResponse,
    PlayerPath,
    PlayerResponse,
    PlayerUpdateRequest,
)
from ..services.players import (
    PlayerDisabledError,
    PlayerNotFoundError,
    PlayerValidationError,
    TeamCapacityReachedError,
    TeamGenderMismatchError,
    TeamSportMismatchError,
    create_player,
    get_player,
    list_players,
    set_player_enabled,
    update_player,
)
from ..services.sports import SportNotFoundError
from ..services.teams import TeamDisabledError, TeamNotFoundError
from .authorization import ACCESS_SECURITY, administrator_required


PLAYERS_TAG = Tag(
    name="Players",
    description="Administrator-only Player management.",
)
players_bp = APIBlueprint(
    "players",
    __name__,
    url_prefix="/players",
    abp_tags=[PLAYERS_TAG],
)


def _player_response(player) -> PlayerResponse:
    return PlayerResponse.model_validate(player)


def _database_unavailable(operation: str):
    current_app.logger.error("Could not %s Player", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


def _assignment_error(error: Exception):
    if isinstance(error, TeamNotFoundError):
        return error_response("team_not_found", str(error), 404)
    if isinstance(error, TeamDisabledError):
        return error_response("team_disabled", str(error), 409)
    if isinstance(error, TeamSportMismatchError):
        return error_response("team_sport_mismatch", str(error), 409)
    if isinstance(error, TeamGenderMismatchError):
        return error_response("team_gender_mismatch", str(error), 409)
    return error_response("team_capacity_reached", str(error), 409)


ASSIGNMENT_ERRORS = (
    TeamNotFoundError,
    TeamDisabledError,
    TeamSportMismatchError,
    TeamGenderMismatchError,
    TeamCapacityReachedError,
)


@players_bp.post(
    "",
    summary="Create a Player",
    description=(
        "Creates an enabled Player and up to three validated Team "
        "memberships atomically."
    ),
    operation_id="playersCreate",
    security=ACCESS_SECURITY,
    responses={
        201: PlayerResponse,
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
def post_player(body: PlayerCreateRequest):
    try:
        player = create_player(body)
    except PlayerValidationError as error:
        return error_response("validation_error", str(error), 422)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except ASSIGNMENT_ERRORS as error:
        return _assignment_error(error)
    except SQLAlchemyError:
        return _database_unavailable("create")

    payload = _player_response(player)
    return jsonify(payload.model_dump(mode="json")), 201


@players_bp.get(
    "",
    summary="List Players",
    description=(
        "Returns filtered, sorted, and paginated Players. Enabled Players "
        "are returned by default and pages contain at most 25 records."
    ),
    operation_id="playersList",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_players(query: PlayerListQuery):
    try:
        result = list_players(query)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except TeamNotFoundError as error:
        return error_response("team_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("list")

    payload = PlayerListResponse(
        players=[_player_response(player) for player in result.players],
        pagination=PlayerPaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200


@players_bp.get(
    "/<int:player_id>",
    summary="Get a Player",
    description="Returns one enabled or disabled Player by identifier.",
    operation_id="playersGet",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_player_by_id(path: PlayerPath):
    try:
        player = get_player(path.player_id)
    except PlayerNotFoundError as error:
        return error_response("player_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")

    payload = _player_response(player)
    return jsonify(payload.model_dump(mode="json")), 200


@players_bp.put(
    "/<int:player_id>",
    summary="Update a Player",
    description=(
        "Updates the name and atomically replaces all Team memberships of "
        "an enabled Player."
    ),
    operation_id="playersUpdate",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerResponse,
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
def put_player(path: PlayerPath, body: PlayerUpdateRequest):
    try:
        player = update_player(path.player_id, body)
    except PlayerValidationError as error:
        return error_response("validation_error", str(error), 422)
    except PlayerNotFoundError as error:
        return error_response("player_not_found", str(error), 404)
    except PlayerDisabledError as error:
        return error_response("player_disabled", str(error), 409)
    except ASSIGNMENT_ERRORS as error:
        return _assignment_error(error)
    except SQLAlchemyError:
        return _database_unavailable("update")

    payload = _player_response(player)
    return jsonify(payload.model_dump(mode="json")), 200


def _set_player_state(player_id: int, *, enabled: bool):
    try:
        player = set_player_enabled(player_id, enabled=enabled)
    except PlayerNotFoundError as error:
        return error_response("player_not_found", str(error), 404)
    except SQLAlchemyError:
        operation = "enable" if enabled else "disable"
        return _database_unavailable(operation)

    payload = _player_response(player)
    return jsonify(payload.model_dump(mode="json")), 200


@players_bp.patch(
    "/<int:player_id>/disable",
    summary="Disable a Player",
    description=(
        "Disables a Player and permanently removes all Team memberships. "
        "Repeated requests are idempotent."
    ),
    operation_id="playersDisable",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def disable_player(path: PlayerPath):
    return _set_player_state(path.player_id, enabled=False)


@players_bp.patch(
    "/<int:player_id>/enable",
    summary="Enable a Player",
    description=(
        "Enables a Player without restoring prior Team memberships. "
        "Repeated requests are idempotent."
    ),
    operation_id="playersEnable",
    security=ACCESS_SECURITY,
    responses={
        200: PlayerResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def enable_player(path: PlayerPath):
    return _set_player_state(path.player_id, enabled=True)
