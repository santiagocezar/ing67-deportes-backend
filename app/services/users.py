from datetime import date
from typing import Any, Mapping

from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import DEFAULT_USER_ROLE, USER_ROLES, User


class UserValidationError(ValueError):
    """Raised when user input does not satisfy validation rules."""


class DuplicateEmailError(ValueError):
    """Raised when an email address is already registered."""


def _required_string(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise UserValidationError(f"{field} is required.")
    return value.strip()


def _parse_birthdate(value: str, today: date | None = None) -> date:
    try:
        birthdate = date.fromisoformat(value)
    except ValueError as error:
        raise UserValidationError(
            "birthdate must be a valid date in YYYY-MM-DD format."
        ) from error

    reference_date = today or date.today()
    age = reference_date.year - birthdate.year - (
        (reference_date.month, reference_date.day)
        < (birthdate.month, birthdate.day)
    )
    if age < 18:
        raise UserValidationError("The user must be at least 18 years old.")
    if age > 100:
        raise UserValidationError("The user cannot be older than 100 years.")
    return birthdate


def create_user(
    data: Mapping[str, Any],
    *,
    role: str = DEFAULT_USER_ROLE,
) -> User:
    if role not in USER_ROLES:
        raise UserValidationError("role is invalid.")

    name = _required_string(data, "name")
    birthdate = _parse_birthdate(_required_string(data, "birthdate"))
    email = _required_string(data, "email").lower()
    password = _required_string(data, "password")

    if "@" not in email:
        raise UserValidationError("email is invalid.")
    if len(password) < 8:
        raise UserValidationError("password must contain at least 8 characters.")

    existing_user_id = db.session.execute(
        db.select(User.id).where(User.email == email)
    ).scalar_one_or_none()
    if existing_user_id is not None:
        raise DuplicateEmailError("An account with that email already exists.")

    user = User(
        name=name,
        birthdate=birthdate,
        email=email,
        password_hash=generate_password_hash(password),
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


def authenticate_user(email: str, password: str) -> User | None:
    normalized_email = email.strip().lower()
    user = db.session.execute(
        db.select(User).where(User.email == normalized_email)
    ).scalar_one_or_none()

    if user is None or not check_password_hash(user.password_hash, password):
        return None
    return user


def get_user(user_id: str) -> User | None:
    try:
        numeric_user_id = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, numeric_user_id)
