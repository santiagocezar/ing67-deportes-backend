from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response
from ..services.auth import (
    RefreshTokenReuseError,
    SessionRevokedError,
    revoke_session,
    rotate_refresh_token,
    start_session,
)
from ..services.users import (
    DuplicateEmailError,
    InvalidRequestedRoleError,
    UserValidationError,
    authenticate_user,
    create_user,
    get_user,
)


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _json_body() -> dict | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


@auth_bp.post("/signup")
def signup():
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    try:
        user = create_user(data)
    except InvalidRequestedRoleError as error:
        return error_response("invalid_requested_role", str(error), 422)
    except UserValidationError as error:
        return error_response("validation_error", str(error), 422)
    except DuplicateEmailError as error:
        return error_response("email_conflict", str(error), 409)
    except SQLAlchemyError:
        current_app.logger.exception("Could not create user")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    return jsonify(message="User created successfully.", user=user.to_dict()), 201


@auth_bp.post("/login")
def login():
    data = _json_body()
    if data is None:
        return error_response(
            "invalid_request",
            "A JSON request body is required.",
            400,
        )

    email = data.get("email")
    password = data.get("password")
    if not isinstance(email, str) or not isinstance(password, str):
        return error_response(
            "validation_error",
            "Email and password are required.",
            422,
        )

    try:
        user = authenticate_user(email, password)
    except SQLAlchemyError:
        current_app.logger.exception("Could not authenticate user")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    if user is None:
        return error_response(
            "invalid_credentials",
            "Invalid email or password.",
            401,
        )

    if not current_app.config.get("JWT_SECRET_KEY"):
        current_app.logger.error("JWT_SECRET_KEY is not configured")
        return error_response(
            "authentication_unavailable",
            "Authentication is not configured.",
            503,
        )

    try:
        access_token, refresh_token = start_session(user)
    except SQLAlchemyError:
        current_app.logger.exception("Could not start auth session")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    return jsonify(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        access_expires_in=900,
    ), 200


@auth_bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    claims = get_jwt()
    try:
        access_token, refresh_token = rotate_refresh_token(
            claims["sid"],
            claims["jti"],
        )
    except RefreshTokenReuseError as error:
        return error_response("refresh_token_reused", str(error), 401)
    except SessionRevokedError as error:
        return error_response("session_revoked", str(error), 401)
    except SQLAlchemyError:
        current_app.logger.exception("Could not rotate refresh token")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    return jsonify(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        access_expires_in=900,
    ), 200


@auth_bp.delete("/logout")
@jwt_required(verify_type=False)
def logout():
    try:
        revoke_session(get_jwt()["sid"])
    except SQLAlchemyError:
        current_app.logger.exception("Could not revoke auth session")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    return "", 204


@auth_bp.get("/me")
@jwt_required()
def me():
    try:
        user = get_user(get_jwt_identity())
    except SQLAlchemyError:
        current_app.logger.exception("Could not load current user")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    if user is None:
        return error_response("user_not_found", "The user no longer exists.", 404)
    return jsonify(user=user.to_dict()), 200
