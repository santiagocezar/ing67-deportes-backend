from functools import wraps

from flask_jwt_extended import get_jwt, jwt_required

from ..errors import error_response
from ..models import ADMIN_USER_ROLE


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
