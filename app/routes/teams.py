from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from ..authorization import roles_required
from ..errors import error_response
from ..models import ADMIN_USER_ROLE, FEDERATION_DELEGATE_USER_ROLE
from ..services.teams import (
    DuplicateTeamNameError,
    TeamAlreadyDisabledError,
    TeamAlreadyEnabledError,
    TeamDisabledError,
    TeamNotFoundError,
    TeamSportNotFoundError,
    TeamValidationError,
    create_team,
    get_team,
    list_teams,
    set_team_enabled,
    update_team_name,
)


teams_bp = Blueprint("teams", __name__, url_prefix="/teams")
TEAM_MANAGER_ROLES = (ADMIN_USER_ROLE, FEDERATION_DELEGATE_USER_ROLE)


def _json_body() -> dict | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _database_unavailable(operation: str):
    current_app.logger.exception("Could not %s Team", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


def _unexpected_failure(operation: str):
    current_app.logger.exception("Unexpected error while trying to %s Team", operation)
    return error_response(
        "internal_error",
        "An unexpected server error occurred.",
        500,
    )


def _team_not_found(error: Exception):
    return error_response("team_not_found", str(error), 404)


@teams_bp.post("")
@roles_required(*TEAM_MANAGER_ROLES)
def post_team():
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    try:
        team = create_team(data)
    except TeamValidationError as error:
        return error_response("validation_error", str(error), 422)
    except TeamSportNotFoundError as error:
        return error_response("sport_not_found", str(error), 404)
    except DuplicateTeamNameError as error:
        return error_response("team_name_conflict", str(error), 409)
    except SQLAlchemyError:
        return _database_unavailable("create")
    except Exception:
        return _unexpected_failure("create")
    return jsonify(team=team.to_detail_dict()), 201


@teams_bp.get("")
@roles_required(*TEAM_MANAGER_ROLES)
def get_teams():
    try:
        page = list_teams(request.args)
    except TeamValidationError as error:
        return error_response("validation_error", str(error), 422)
    except SQLAlchemyError:
        return _database_unavailable("list")
    except Exception:
        return _unexpected_failure("list")

    return jsonify(
        teams=[team.to_summary_dict() for team in page.teams],
        pagination={
            "page": page.page,
            "page_size": page.page_size,
            "total_items": page.total_items,
            "total_pages": page.total_pages,
        },
    ), 200


@teams_bp.get("/<int:team_id>")
@roles_required(*TEAM_MANAGER_ROLES)
def get_team_by_id(team_id: int):
    try:
        team = get_team(team_id)
    except TeamNotFoundError as error:
        return _team_not_found(error)
    except SQLAlchemyError:
        return _database_unavailable("get")
    except Exception:
        return _unexpected_failure("get")
    return jsonify(team=team.to_detail_dict()), 200


@teams_bp.put("/<int:team_id>")
@roles_required(*TEAM_MANAGER_ROLES)
def put_team(team_id: int):
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    try:
        team = update_team_name(team_id, data)
    except TeamNotFoundError as error:
        return _team_not_found(error)
    except TeamDisabledError as error:
        return error_response("team_disabled", str(error), 409)
    except DuplicateTeamNameError as error:
        return error_response("team_name_conflict", str(error), 409)
    except TeamValidationError as error:
        return error_response("validation_error", str(error), 422)
    except SQLAlchemyError:
        return _database_unavailable("update")
    except Exception:
        return _unexpected_failure("update")
    return jsonify(team=team.to_detail_dict()), 200


def _change_team_state(team_id: int, *, is_enabled: bool):
    try:
        team = set_team_enabled(team_id, is_enabled=is_enabled)
    except TeamNotFoundError as error:
        return _team_not_found(error)
    except TeamAlreadyEnabledError as error:
        return error_response("team_already_enabled", str(error), 409)
    except TeamAlreadyDisabledError as error:
        return error_response("team_already_disabled", str(error), 409)
    except SQLAlchemyError:
        operation = "enable" if is_enabled else "disable"
        return _database_unavailable(operation)
    except Exception:
        operation = "enable" if is_enabled else "disable"
        return _unexpected_failure(operation)
    return jsonify(team=team.to_detail_dict()), 200


@teams_bp.patch("/<int:team_id>/disable")
@roles_required(*TEAM_MANAGER_ROLES)
def disable_team(team_id: int):
    return _change_team_state(team_id, is_enabled=False)


@teams_bp.patch("/<int:team_id>/enable")
@roles_required(*TEAM_MANAGER_ROLES)
def enable_team(team_id: int):
    return _change_team_state(team_id, is_enabled=True)
