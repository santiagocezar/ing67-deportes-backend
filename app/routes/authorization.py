from functools import wraps

from flask_jwt_extended import get_jwt, jwt_required

from ..errors import error_response
from ..models import ADMIN_USER_ROLE, USER_ROLES


ACCESS_SECURITY = [{"AccessTokenAuth": []}]


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


def authenticated_user_required(function):
    """Require an access token for a supported application role."""

    @wraps(function)
    @jwt_required()
    def wrapper(*args, **kwargs):
        if get_jwt().get("role") not in USER_ROLES:
            return error_response(
                "application_role_required",
                "A supported application role is required.",
                403,
            )
        return function(*args, **kwargs)

    return wrapper
