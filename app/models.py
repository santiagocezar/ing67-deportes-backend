from datetime import date, datetime

from sqlalchemy import CheckConstraint, UniqueConstraint, func

from .extensions import db


ADMIN_USER_ROLE = "administrator"
DEFAULT_USER_ROLE = "referee"
USER_ROLES = (ADMIN_USER_ROLE, DEFAULT_USER_ROLE)


class Sport(db.Model):
    __tablename__ = "sports"
    __table_args__ = (
        CheckConstraint(
            "max_players BETWEEN 1 AND 20",
            name="ck_sports_max_players_range",
        ),
        CheckConstraint(
            "char_length(trim(name)) > 0",
            name="ck_sports_name_not_blank",
        ),
        UniqueConstraint("name", name="uq_sports_name"),
        UniqueConstraint(
            "normalized_name",
            name="uq_sports_normalized_name",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False)
    max_players = db.Column(db.Integer, nullable=False)

    def to_dict(self) -> dict[str, int | str]:
        return {
            "id": self.id,
            "name": self.name,
            "max_players": self.max_players,
        }


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('administrator', 'referee')",
            name="ck_users_valid_role",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    birthdate = db.Column(db.Date, nullable=False)
    role = db.Column(
        db.String(20),
        nullable=False,
        default=DEFAULT_USER_ROLE,
        server_default=DEFAULT_USER_ROLE,
    )
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    creation_date = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    auth_sessions = db.relationship(
        "AuthSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict[str, int | str | date | datetime | None]:
        return {
            "id": self.id,
            "name": self.name,
            "birthdate": self.birthdate.isoformat(),
            "role": self.role,
            "email": self.email,
            "creation_date": (
                self.creation_date.isoformat() if self.creation_date else None
            ),
        }


class AuthSession(db.Model):
    __tablename__ = "auth_sessions"

    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    current_refresh_jti = db.Column(db.String(36), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user = db.relationship("User", back_populates="auth_sessions")
