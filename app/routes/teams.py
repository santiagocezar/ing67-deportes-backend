from flask import current_app, jsonify
from flask_openapi3 import APIBlueprint, Tag, validate_request
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response, json_object_required
from ..schemas.common import ErrorResponse
from ..schemas.teams import (
    PaginationResponse,
    TeamCreateRequest,
    TeamEnvelope,
    TeamListQuery,
    TeamListResponse,
    TeamPath,
    TeamResponse,
    TeamUpdateRequest,
)
from ..services.sports import SportNotFoundError
from ..services.teams import (
    DuplicateTeamNameError,
    TeamDisabledError,
    TeamNotFoundError,
    TeamValidationError,
    create_team,
    get_team,
    list_teams,
    set_team_enabled,
    update_team_name,
)
from .authorization import ACCESS_SECURITY, administrator_required


TEAMS_TAG = Tag(
    name="Teams",
    description="Administrator-only Team catalogue management.",
)

teams_bp = APIBlueprint(
    "teams",
    __name__,
    url_prefix="/teams",
    abp_tags=[TEAMS_TAG],
)


def _team_response(team) -> TeamResponse:
    return TeamResponse.model_validate(team)


def _database_unavailable(operation: str):
    current_app.logger.error("Could not %s Team", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


@teams_bp.post(
    "",
    summary="Create a Team",
    description=(
        "Creates an enabled Team. Its identity is unique by normalized name, "
        "Sport, and gender category."
    ),
    operation_id="teamsCreate",
    security=ACCESS_SECURITY,
    responses={
        201: TeamEnvelope,
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
def post_team(body: TeamCreateRequest):
    try:
        team = create_team(body)
    except TeamValidationError as error:
        return error_response("validation_error", str(error), 422)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except DuplicateTeamNameError as error:
        return error_response("team_name_conflict", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("create")

    payload = TeamEnvelope(team=_team_response(team))
    return jsonify(payload.model_dump(mode="json")), 201


@teams_bp.get(
    "",
    summary="List Teams",
    description=(
        "Returns a filtered and paginated Team catalogue. Enabled Teams are "
        "returned by default and each page contains at most 25 records."
    ),
    operation_id="teamsList",
    security=ACCESS_SECURITY,
    responses={
        200: TeamListResponse,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_teams(query: TeamListQuery):
    try:
        result = list_teams(query)
    except SportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("list")

    payload = TeamListResponse(
        teams=[_team_response(team) for team in result.teams],
        pagination=PaginationResponse(
            page=result.page,
            per_page=result.per_page,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
    return jsonify(payload.model_dump(mode="json")), 200


@teams_bp.get(
    "/<int:team_id>",
    summary="Get a Team",
    description="Returns one enabled or disabled Team by identifier.",
    operation_id="teamsGet",
    security=ACCESS_SECURITY,
    responses={
        200: TeamEnvelope,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def get_team_by_id(path: TeamPath):
    try:
        team = get_team(path.team_id)
    except TeamNotFoundError as error:
        return error_response("team_not_found", str(error), 404)
    except SQLAlchemyError:
        return _database_unavailable("get")

    payload = TeamEnvelope(team=_team_response(team))
    return jsonify(payload.model_dump(mode="json")), 200


@teams_bp.put(
    "/<int:team_id>",
    summary="Rename a Team",
    description=(
        "Changes only the name of an enabled Team. Sport and gender are "
        "immutable."
    ),
    operation_id="teamsUpdate",
    security=ACCESS_SECURITY,
    responses={
        200: TeamEnvelope,
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
def put_team(path: TeamPath, body: TeamUpdateRequest):
    try:
        team = update_team_name(path.team_id, body)
    except TeamValidationError as error:
        return error_response("validation_error", str(error), 422)
    except TeamNotFoundError as error:
        return error_response("team_not_found", str(error), 404)
    except TeamDisabledError as error:
        return error_response("team_disabled", str(error), 409)
    except DuplicateTeamNameError as error:
        return error_response("team_name_conflict", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("update")

    payload = TeamEnvelope(team=_team_response(team))
    return jsonify(payload.model_dump(mode="json")), 200


def _set_team_state(team_id: int, *, enabled: bool):
    try:
        team = set_team_enabled(team_id, enabled=enabled)
    except TeamNotFoundError as error:
        return error_response("team_not_found", str(error), 404)
    except SQLAlchemyError:
        operation = "enable" if enabled else "disable"
        return _database_unavailable(operation)

    payload = TeamEnvelope(team=_team_response(team))
    return jsonify(payload.model_dump(mode="json")), 200


@teams_bp.patch(
    "/<int:team_id>/disable",
    summary="Disable a Team",
    description=(
        "Disables a Team without deleting it. Repeated requests are idempotent."
    ),
    operation_id="teamsDisable",
    security=ACCESS_SECURITY,
    responses={
        200: TeamEnvelope,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def disable_team(path: TeamPath):
    return _set_team_state(path.team_id, enabled=False)


@teams_bp.patch(
    "/<int:team_id>/enable",
    summary="Enable a Team",
    description=(
        "Enables a Team again. Repeated requests are idempotent."
    ),
    operation_id="teamsEnable",
    security=ACCESS_SECURITY,
    responses={
        200: TeamEnvelope,
        401: ErrorResponse,
        403: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@administrator_required
@validate_request()
def enable_team(path: TeamPath):
    return _set_team_state(path.team_id, enabled=True)
