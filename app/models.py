from datetime import date, datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint, func, text

from .extensions import db


ADMIN_USER_ROLE = "administrator"
DEFAULT_USER_ROLE = "referee"
USER_ROLES = (ADMIN_USER_ROLE, DEFAULT_USER_ROLE)


class Sport(db.Model):
    __tablename__ = "sports"
    __table_args__ = (
        CheckConstraint(
            "max_players > 0",
            name="ck_sports_max_players_positive",
        ),
        CheckConstraint(
            "max_players_in_game > 0",
            name="ck_sports_max_players_in_game_positive",
        ),
        CheckConstraint(
            "max_players_in_game <= max_players",
            name="ck_sports_capacity_order",
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
    max_players_in_game = db.Column(db.Integer, nullable=False)

    teams = db.relationship(
        "Team",
        back_populates="sport",
        passive_deletes=True,
    )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "id": self.id,
            "name": self.name,
            "max_players": self.max_players,
            "max_players_in_game": self.max_players_in_game,
        }


class Team(db.Model):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(name)) > 0",
            name="ck_teams_name_not_blank",
        ),
        CheckConstraint(
            "gender_category IN ('male', 'female')",
            name="ck_teams_gender_category",
        ),
        CheckConstraint(
            "("
            "is_enabled = TRUE AND disabled_at IS NULL"
            ") OR ("
            "is_enabled = FALSE AND disabled_at IS NOT NULL"
            ")",
            name="ck_teams_enabled_disabled_at",
        ),
        UniqueConstraint(
            "normalized_name",
            "sport_id",
            "gender_category",
            name="uq_teams_normalized_name_sport_gender",
        ),
        Index("ix_teams_sport_id", "sport_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False)
    sport_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "sports.id",
            ondelete="RESTRICT",
            name="fk_teams_sport_id_sports",
        ),
        nullable=False,
    )
    gender_category = db.Column(db.String(10), nullable=False)
    is_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    disabled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    sport = db.relationship("Sport", back_populates="teams")


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
