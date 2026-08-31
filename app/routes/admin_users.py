from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from ..authorization import roles_required
from ..errors import error_response
from ..models import ADMIN_USER_ROLE
from ..services.account_management import (
    AccountManagementValidationError,
    AccountStateConflictError,
    ActiveUserDeleteForbiddenError,
    UserNotFoundError,
    UserNotPendingError,
    approve_user,
    delete_user,
    list_users,
    set_user_active,
)


users_bp = Blueprint("users", __name__, url_prefix="/users")


def _database_unavailable(operation: str):
    current_app.logger.exception("Could not %s user account", operation)
    return error_response(
        "service_unavailable",
        "The database is temporarily unavailable.",
        503,
    )


def _account_error(error: Exception):
    if isinstance(error, UserNotFoundError):
        return error_response("user_not_found", str(error), 404)
    if isinstance(error, UserNotPendingError):
        return error_response("user_not_pending", str(error), 409)
    if isinstance(error, ActiveUserDeleteForbiddenError):
        return error_response(
            "active_user_delete_forbidden",
            str(error),
            409,
        )
    if isinstance(error, AccountStateConflictError):
        return error_response("account_state_conflict", str(error), 409)
    raise error


@users_bp.get("")
@roles_required(ADMIN_USER_ROLE)
def get_users():
    try:
        users = list_users(
            role=request.args.get("role"),
            requested_role=request.args.get("requested_role"),
        )
    except AccountManagementValidationError as error:
        return error_response("validation_error", str(error), 422)
    except SQLAlchemyError:
        return _database_unavailable("list")
    return jsonify(users=[user.to_dict() for user in users]), 200


@users_bp.post("/<int:user_id>/approve")
@roles_required(ADMIN_USER_ROLE)
def post_user_approval(user_id: int):
    try:
        user = approve_user(user_id)
    except (UserNotFoundError, UserNotPendingError) as error:
        return _account_error(error)
    except SQLAlchemyError:
        return _database_unavailable("approve")
    return jsonify(user=user.to_dict()), 200


@users_bp.delete("/<int:user_id>")
@roles_required(ADMIN_USER_ROLE)
def remove_user(user_id: int):
    try:
        delete_user(user_id)
    except (UserNotFoundError, ActiveUserDeleteForbiddenError) as error:
        return _account_error(error)
    except SQLAlchemyError:
        return _database_unavailable("delete")
    return "", 204


def _change_account_state(user_id: int, *, is_active: bool):
    try:
        user = set_user_active(user_id, is_active=is_active)
    except (
        UserNotFoundError,
        UserNotPendingError,
        AccountStateConflictError,
    ) as error:
        return _account_error(error)
    except SQLAlchemyError:
        operation = "enable" if is_active else "disable"
        return _database_unavailable(operation)
    return jsonify(user=user.to_dict()), 200


@users_bp.post("/<int:user_id>/disable")
@roles_required(ADMIN_USER_ROLE)
def disable_user(user_id: int):
    return _change_account_state(user_id, is_active=False)


@users_bp.post("/<int:user_id>/enable")
@roles_required(ADMIN_USER_ROLE)
def enable_user(user_id: int):
    return _change_account_state(user_id, is_active=True)
