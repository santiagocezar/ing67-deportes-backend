from functools import wraps
from typing import Callable

from flask import current_app, g
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import SQLAlchemyError

from .errors import error_response
from .models import USER_ROLE
from .services.users import get_user


def roles_required(*allowed_roles: str):
    """Authorize a business request using the current persisted user state."""

    def decorator(function: Callable):
        @wraps(function)
        @jwt_required()
        def wrapper(*args, **kwargs):
            try:
                user = get_user(get_jwt_identity())
            except SQLAlchemyError:
                current_app.logger.exception(
                    "Could not load the authenticated user"
                )
                return error_response(
                    "service_unavailable",
                    "The database is temporarily unavailable.",
                    503,
                )

            if user is None:
                return error_response(
                    "user_not_found",
                    "The authenticated user no longer exists.",
                    401,
                )
            if not user.is_active:
                return error_response(
                    "account_disabled",
                    "The account is disabled.",
                    403,
                )
            if user.role == USER_ROLE and USER_ROLE not in allowed_roles:
                return error_response(
                    "approval_required",
                    "Administrator approval is required.",
                    403,
                )
            if user.role not in allowed_roles:
                return error_response(
                    "role_forbidden",
                    "The current role cannot access this resource.",
                    403,
                )

            g.current_user = user
            return function(*args, **kwargs)

        return wrapper

    return decorator
