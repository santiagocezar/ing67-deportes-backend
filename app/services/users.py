from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import DEFAULT_USER_ROLE, USER_ROLES, User
from ..schemas.auth import LoginRequest, SignupRequest


class UserValidationError(ValueError):
    """Raised when user input does not satisfy validation rules."""


class DuplicateEmailError(ValueError):
    """Raised when an email address is already registered."""


def create_user(
    data: SignupRequest,
    *,
    role: str = DEFAULT_USER_ROLE,
) -> User:
    if role not in USER_ROLES:
        raise UserValidationError("role is invalid.")

    existing_user_id = db.session.execute(
        db.select(User.id).where(User.email == data.email)
    ).scalar_one_or_none()
    if existing_user_id is not None:
        raise DuplicateEmailError("An account with that email already exists.")

    user = User(
        name=data.name,
        birthdate=data.birthdate,
        email=data.email,
        password_hash=generate_password_hash(data.password),
        role=role,
    )
    db.session.add(user)

    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        raise DuplicateEmailError(
            "An account with that email already exists."
        ) from error
    except Exception:
        db.session.rollback()
        raise

    return user


def authenticate_user(credentials: LoginRequest) -> User | None:
    normalized_email = credentials.email.strip().lower()
    user = db.session.execute(
        db.select(User).where(User.email == normalized_email)
    ).scalar_one_or_none()

    if user is None or not check_password_hash(
        user.password_hash,
        credentials.password,
    ):
        return None
    return user


def get_user(user_id: str) -> User | None:
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, numeric_user_id)
