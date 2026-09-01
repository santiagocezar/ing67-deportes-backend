from flask import current_app, jsonify
from flask_jwt_extended import get_jwt, get_jwt_identity, jwt_required
from flask_openapi3 import APIBlueprint, Tag
from sqlalchemy.exc import SQLAlchemyError

from ..errors import error_response
from ..schemas.auth import (
    LoginRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
    UserEnvelope,
    UserResponse,
)
from ..schemas.common import ErrorResponse
from ..services.auth import (
    RefreshTokenReuseError,
    SessionRevokedError,
    revoke_session,
    rotate_refresh_token,
    start_session,
)
from ..services.users import (
    DuplicateEmailError,
    UserValidationError,
    authenticate_user,
    create_user,
    get_user,
)


AUTH_TAG = Tag(
    name="Authentication",
    description="Registration, JWT sessions, rotation, and current-user access.",
)
ACCESS_SECURITY = [{"AccessTokenAuth": []}]
REFRESH_SECURITY = [{"RefreshTokenAuth": []}]
ANY_TOKEN_SECURITY = [
    {"AccessTokenAuth": []},
    {"RefreshTokenAuth": []},
]

auth_bp = APIBlueprint(
    "auth",
    __name__,
    url_prefix="/auth",
    abp_tags=[AUTH_TAG],
)


def _token_response(access_token: str, refresh_token: str):
    payload = TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        access_expires_in=900,
    )
    return jsonify(payload.model_dump(mode="json"))


@auth_bp.post(
    "/signup",
    summary="Register a referee account",
    description="Creates a public account with the referee role.",
    operation_id="authSignup",
    responses={
        201: SignupResponse,
        400: ErrorResponse,
        409: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
def signup(body: SignupRequest):
    try:
        user = create_user(body)
    except UserValidationError as error:
        return error_response("validation_error", str(error), 422)
    except DuplicateEmailError as error:
        return error_response("email_conflict", str(error), 409)
    except SQLAlchemyError:
        current_app.logger.error("Could not create user")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    payload = SignupResponse(
        message="User created successfully.",
        user=UserResponse.model_validate(user),
    )
    return jsonify(payload.model_dump(mode="json")), 201


@auth_bp.post(
    "/login",
    summary="Start an authenticated session",
    description=(
        "Validates credentials and returns a 15-minute access token plus a "
        "rotating refresh token."
    ),
    operation_id="authLogin",
    responses={
        200: TokenResponse,
        400: ErrorResponse,
        401: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
def login(body: LoginRequest):
    user = authenticate_user(body)
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
        current_app.logger.error("Could not start auth session")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    return _token_response(access_token, refresh_token), 200


@auth_bp.post(
    "/refresh",
    summary="Rotate a refresh token",
    description=(
        "Requires the current refresh token. It is invalidated and replaced "
        "with a new access/refresh pair; reuse revokes the whole session."
    ),
    operation_id="authRefresh",
    security=REFRESH_SECURITY,
    responses={
        200: TokenResponse,
        401: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
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
        current_app.logger.error("Could not rotate refresh token")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )

    return _token_response(access_token, refresh_token), 200


@auth_bp.delete(
    "/logout",
    summary="Revoke the current session",
    description="Accepts either token type and revokes its persistent session.",
    operation_id="authLogout",
    security=ANY_TOKEN_SECURITY,
    responses={
        204: None,
        401: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@jwt_required(verify_type=False)
def logout():
    try:
        revoke_session(get_jwt()["sid"])
    except SQLAlchemyError:
        current_app.logger.error("Could not revoke auth session")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    return "", 204


@auth_bp.get(
    "/me",
    summary="Get the authenticated user",
    description="Requires a valid access token.",
    operation_id="authMe",
    security=ACCESS_SECURITY,
    responses={
        200: UserEnvelope,
        401: ErrorResponse,
        404: ErrorResponse,
        422: ErrorResponse,
        503: ErrorResponse,
    },
)
@jwt_required()
def me():
    try:
        user = get_user(get_jwt_identity())
    except SQLAlchemyError:
        current_app.logger.error("Could not get authenticated user")
        return error_response(
            "service_unavailable",
            "The database is temporarily unavailable.",
            503,
        )
    if user is None:
        return error_response("user_not_found", "The user no longer exists.", 404)

    payload = UserEnvelope(user=UserResponse.model_validate(user))
    return jsonify(payload.model_dump(mode="json")), 200
