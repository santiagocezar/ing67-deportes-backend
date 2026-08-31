from ..extensions import db
from ..models import (
    REFEREE_USER_ROLE,
    REQUESTABLE_ROLES,
    USER_ROLE,
    USER_ROLES,
    User,
)


class AccountManagementValidationError(ValueError):
    """Raised when an account-management input is invalid."""


class UserNotFoundError(LookupError):
    """Raised when a target user does not exist."""


class UserNotPendingError(RuntimeError):
    """Raised when an operation requires a pending account."""


class ActiveUserDeleteForbiddenError(RuntimeError):
    """Raised when a referee deletion would remove historical identity."""


class AccountStateConflictError(RuntimeError):
    """Raised when enablement already has the requested state."""


def list_users(
    *,
    role: str | None = None,
    requested_role: str | None = None,
) -> list[User]:
    if role is not None and role not in USER_ROLES:
        raise AccountManagementValidationError("role is invalid.")
    if requested_role is not None and requested_role not in REQUESTABLE_ROLES:
        raise AccountManagementValidationError("requested_role is invalid.")

    statement = db.select(User).order_by(User.id)
    if role is not None:
        statement = statement.where(User.role == role)
    if requested_role is not None:
        statement = statement.where(User.requested_role == requested_role)
    return list(db.session.execute(statement).scalars())


def _locked_user(user_id: int) -> User:
    user = db.session.execute(
        db.select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        db.session.rollback()
        raise UserNotFoundError("The user does not exist.")
    return user


def approve_user(user_id: int) -> User:
    user = _locked_user(user_id)
    if user.role != USER_ROLE or user.requested_role not in REQUESTABLE_ROLES:
        db.session.rollback()
        raise UserNotPendingError("The user is not pending approval.")

    user.role = user.requested_role
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return user


def delete_user(user_id: int) -> None:
    user = _locked_user(user_id)
    if user.role == REFEREE_USER_ROLE:
        db.session.rollback()
        raise ActiveUserDeleteForbiddenError(
            "Referee accounts cannot be deleted. Disable the account instead."
        )

    db.session.delete(user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def set_user_active(user_id: int, *, is_active: bool) -> User:
    user = _locked_user(user_id)
    if user.role == USER_ROLE:
        db.session.rollback()
        raise UserNotPendingError(
            "Pending accounts must be approved or deleted."
        )
    if user.is_active is is_active:
        db.session.rollback()
        state = "enabled" if is_active else "disabled"
        raise AccountStateConflictError(f"The account is already {state}.")

    user.is_active = is_active
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return user
