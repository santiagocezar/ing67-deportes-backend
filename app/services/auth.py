from datetime import datetime, timezone
from uuid import uuid4

from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    decode_token,
)

from ..extensions import db
from ..models import AuthSession, User


class SessionRevokedError(RuntimeError):
    """Raised when a token belongs to an inactive session."""


class RefreshTokenReuseError(RuntimeError):
    """Raised when an already rotated refresh token is presented."""


def _utc_from_timestamp(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _token_pair(user: User, session_id: str) -> tuple[str, str, str, datetime]:
    claims = {"sid": session_id, "role": user.role}
    access_token = create_access_token(
        identity=str(user.id),
        additional_claims=claims,
    )
    refresh_token = create_refresh_token(
        identity=str(user.id),
        additional_claims={"sid": session_id},
    )
    refresh_claims = decode_token(refresh_token)
    return (
        access_token,
        refresh_token,
        refresh_claims["jti"],
        _utc_from_timestamp(refresh_claims["exp"]),
    )


def start_session(user: User) -> tuple[str, str]:
    session_id = str(uuid4())
    access_token, refresh_token, refresh_jti, expires_at = _token_pair(
        user,
        session_id,
    )
    auth_session = AuthSession(
        id=session_id,
        user_id=user.id,
        current_refresh_jti=refresh_jti,
        expires_at=expires_at,
    )
    db.session.add(auth_session)

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return access_token, refresh_token


def rotate_refresh_token(
    session_id: str,
    presented_jti: str,
) -> tuple[str, str]:
    auth_session = db.session.execute(
        db.select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
    ).scalar_one_or_none()

    if auth_session is None or auth_session.revoked_at is not None:
        db.session.rollback()
        raise SessionRevokedError("The session is no longer active.")

    if auth_session.current_refresh_jti != presented_jti:
        auth_session.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
        raise RefreshTokenReuseError(
            "Refresh token reuse was detected. Sign in again."
        )

    access_token, refresh_token, refresh_jti, expires_at = _token_pair(
        auth_session.user,
        auth_session.id,
    )
    auth_session.current_refresh_jti = refresh_jti
    auth_session.expires_at = expires_at

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return access_token, refresh_token


def revoke_session(session_id: str) -> None:
    auth_session = db.session.get(AuthSession, session_id)
    if auth_session is None or auth_session.revoked_at is not None:
        return

    auth_session.revoked_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def is_token_revoked(jwt_payload: dict) -> bool:
    session_id = jwt_payload.get("sid")
    if not session_id:
        return True

    auth_session = db.session.get(AuthSession, session_id)
    return auth_session is None or auth_session.revoked_at is not None
