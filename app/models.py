from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import JSONB

from .extensions import db


USER_ROLE = "user"
REFEREE_USER_ROLE = "referee"
FEDERATION_DELEGATE_USER_ROLE = "federation_delegate"
ADMIN_USER_ROLE = "administrator"
DEFAULT_PUBLIC_ROLE = USER_ROLE
REQUESTABLE_ROLES = (
    REFEREE_USER_ROLE,
    FEDERATION_DELEGATE_USER_ROLE,
)
USER_ROLES = (
    USER_ROLE,
    REFEREE_USER_ROLE,
    FEDERATION_DELEGATE_USER_ROLE,
    ADMIN_USER_ROLE,
)


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
        CheckConstraint(
            "match_duration > 0",
            name="ck_sports_match_duration_positive",
        ),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(resolution_methods) = 'array' "
            "THEN jsonb_array_length(resolution_methods) > 0 ELSE FALSE END",
            name="ck_sports_resolution_methods_non_empty_array",
        ).ddl_if(dialect="postgresql"),
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
    match_duration = db.Column(db.Integer, nullable=False)
    resolution_methods = db.Column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
    )
    teams = db.relationship(
        "Team",
        back_populates="sport",
        passive_deletes=True,
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "max_players": self.max_players,
            "match_duration": self.match_duration,
            "resolution_methods": self.resolution_methods,
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
            name="ck_teams_valid_gender_category",
        ),
        CheckConstraint(
            "(is_enabled AND disabled_at IS NULL) OR "
            "(NOT is_enabled AND disabled_at IS NOT NULL)",
            name="ck_teams_enabled_timestamp_consistency",
        ),
        UniqueConstraint(
            "normalized_name",
            "sport_id",
            "gender_category",
            name="uq_teams_normalized_name_sport_gender",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False)
    sport_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "sports.id",
            name="fk_teams_sport_id_sports",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    gender_category = db.Column(db.String(10), nullable=False)
    is_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=true(),
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    disabled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    sport = db.relationship("Sport", back_populates="teams")

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "sport_id": self.sport_id,
            "sport": {
                "id": self.sport.id,
                "name": self.sport.name,
                "max_players": self.sport.max_players,
            },
            "gender_category": self.gender_category,
            "is_enabled": self.is_enabled,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "disabled_at": (
                self.disabled_at.isoformat() if self.disabled_at else None
            ),
            "current_players_quantity": 0,
            "is_eligible_for_competition": False,
        }

    def to_detail_dict(self) -> dict[str, Any]:
        return {**self.to_summary_dict(), "players": []}


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'referee', 'federation_delegate', 'administrator')",
            name="ck_users_valid_role",
        ),
        CheckConstraint(
            "requested_role IS NULL OR "
            "requested_role IN ('referee', 'federation_delegate')",
            name="ck_users_valid_requested_role",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    birthdate = db.Column(db.Date, nullable=False)
    role = db.Column(
        db.String(20),
        nullable=False,
        default=DEFAULT_PUBLIC_ROLE,
        server_default=DEFAULT_PUBLIC_ROLE,
        index=True,
    )
    requested_role = db.Column(db.String(30), nullable=True)
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=true(),
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

    def to_dict(self) -> dict[str, bool | int | str | date | datetime | None]:
        return {
            "id": self.id,
            "name": self.name,
            "birthdate": self.birthdate.isoformat(),
            "role": self.role,
            "requested_role": self.requested_role,
            "is_active": self.is_active,
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
