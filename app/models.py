from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    func,
    text,
)

from .extensions import db


ADMIN_USER_ROLE = "administrator"
DEFAULT_USER_ROLE = "referee"
USER_ROLES = (ADMIN_USER_ROLE, DEFAULT_USER_ROLE)


team_players = db.Table(
    "team_players",
    db.Column(
        "team_id",
        db.Integer,
        db.ForeignKey(
            "teams.id",
            name="fk_team_players_team_id_teams",
        ),
        primary_key=True,
    ),
    db.Column(
        "player_id",
        db.Integer,
        db.ForeignKey(
            "players.id",
            name="fk_team_players_player_id_players",
        ),
        primary_key=True,
    ),
    Index("ix_team_players_player_id", "player_id"),
)


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
    players = db.relationship(
        "Player",
        back_populates="sport",
        passive_deletes=True,
    )
    competitions = db.relationship(
        "Competition",
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
    players = db.relationship(
        "Player",
        secondary=team_players,
        back_populates="teams",
        order_by="Player.id",
    )
    competition_entries = db.relationship(
        "CompetitionTeam",
        back_populates="team",
        passive_deletes=True,
    )
    matches_as_team_1 = db.relationship(
        "Match",
        foreign_keys="Match.team_1_id",
        back_populates="team_1",
        passive_deletes=True,
    )
    matches_as_team_2 = db.relationship(
        "Match",
        foreign_keys="Match.team_2_id",
        back_populates="team_2",
        passive_deletes=True,
    )


class Player(db.Model):
    __tablename__ = "players"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(name)) > 0",
            name="ck_players_name_not_blank",
        ),
        CheckConstraint(
            "("
            "is_enabled = TRUE AND disabled_at IS NULL"
            ") OR ("
            "is_enabled = FALSE AND disabled_at IS NOT NULL"
            ")",
            name="ck_players_enabled_disabled_at",
        ),
        CheckConstraint(
            "gender IN ('male', 'female')",
            name="ck_players_gender",
        ),
        Index("ix_players_sport_id", "sport_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    sport_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "sports.id",
            ondelete="RESTRICT",
            name="fk_players_sport_id_sports",
        ),
        nullable=False,
    )
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

    sport = db.relationship("Sport", back_populates="players")
    teams = db.relationship(
        "Team",
        secondary=team_players,
        back_populates="players",
        order_by="Team.id",
    )
    competition_roster_entries = db.relationship(
        "CompetitionRosterPlayer",
        back_populates="player",
        passive_deletes=True,
    )


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
    competition_referee_entries = db.relationship(
        "CompetitionReferee",
        back_populates="referee",
        passive_deletes=True,
    )
    refereed_matches = db.relationship(
        "Match",
        back_populates="referee",
        passive_deletes=True,
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


class Competition(db.Model):
    __tablename__ = "competitions"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(name)) > 0",
            name="ck_competitions_name_not_blank",
        ),
        CheckConstraint(
            "gender IN ('male', 'female')",
            name="ck_competitions_gender",
        ),
        CheckConstraint(
            "starts_at < ends_at",
            name="ck_competitions_date_range",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'in_progress', 'finished', 'discarded')",
            name="ck_competitions_status",
        ),
        CheckConstraint(
            "(is_enabled = TRUE AND disabled_at IS NULL AND "
            "status <> 'discarded') OR "
            "(is_enabled = FALSE AND disabled_at IS NOT NULL AND "
            "status = 'discarded')",
            name="ck_competitions_enabled_status",
        ),
        Index("ix_competitions_sport_id", "sport_id"),
        Index("ix_competitions_starts_at", "starts_at"),
        Index("ix_competitions_status", "status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    normalized_name = db.Column(db.String(100), nullable=False)
    sport_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "sports.id",
            ondelete="RESTRICT",
            name="fk_competitions_sport_id_sports",
        ),
        nullable=False,
    )
    gender = db.Column(db.String(10), nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=False)
    status = db.Column(db.String(20), nullable=False)
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

    sport = db.relationship("Sport", back_populates="competitions")
    team_entries = db.relationship(
        "CompetitionTeam",
        back_populates="competition",
        cascade="all, delete-orphan",
        order_by="CompetitionTeam.team_id",
    )
    referee_entries = db.relationship(
        "CompetitionReferee",
        back_populates="competition",
        cascade="all, delete-orphan",
        order_by="CompetitionReferee.referee_id",
    )
    matches = db.relationship(
        "Match",
        back_populates="competition",
        order_by=lambda: (Match.round_number, Match.id),
    )

    @property
    def team_count(self) -> int:
        return len(self.team_entries)


class CompetitionTeam(db.Model):
    __tablename__ = "competition_teams"

    competition_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "competitions.id",
            ondelete="CASCADE",
            name="fk_competition_teams_competition_id_competitions",
        ),
        primary_key=True,
    )
    team_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "teams.id",
            ondelete="RESTRICT",
            name="fk_competition_teams_team_id_teams",
        ),
        primary_key=True,
    )

    competition = db.relationship("Competition", back_populates="team_entries")
    team = db.relationship("Team", back_populates="competition_entries")
    roster_entries = db.relationship(
        "CompetitionRosterPlayer",
        back_populates="competition_team",
        cascade="all, delete-orphan",
        order_by="CompetitionRosterPlayer.player_id",
    )


class CompetitionRosterPlayer(db.Model):
    __tablename__ = "competition_roster_players"
    __table_args__ = (
        ForeignKeyConstraint(
            ["competition_id", "team_id"],
            ["competition_teams.competition_id", "competition_teams.team_id"],
            name=(
                "fk_competition_roster_players_competition_team"
            ),
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "competition_id",
            "player_id",
            name="uq_competition_roster_players_competition_player",
        ),
        Index(
            "ix_competition_roster_players_player_id",
            "player_id",
        ),
    )

    competition_id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "players.id",
            ondelete="RESTRICT",
            name="fk_competition_roster_players_player_id_players",
        ),
        primary_key=True,
    )

    competition_team = db.relationship(
        "CompetitionTeam",
        back_populates="roster_entries",
    )
    player = db.relationship(
        "Player",
        back_populates="competition_roster_entries",
    )


class CompetitionReferee(db.Model):
    __tablename__ = "competition_referees"
    __table_args__ = (
        Index("ix_competition_referees_referee_id", "referee_id"),
    )

    competition_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "competitions.id",
            ondelete="CASCADE",
            name="fk_competition_referees_competition_id_competitions",
        ),
        primary_key=True,
    )
    referee_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_competition_referees_referee_id_users",
        ),
        primary_key=True,
    )

    competition = db.relationship(
        "Competition",
        back_populates="referee_entries",
    )
    referee = db.relationship(
        "User",
        back_populates="competition_referee_entries",
    )


class Match(db.Model):
    __tablename__ = "matches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["competition_id", "team_1_id"],
            ["competition_teams.competition_id", "competition_teams.team_id"],
            name="fk_matches_competition_team_1",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["competition_id", "team_2_id"],
            ["competition_teams.competition_id", "competition_teams.team_id"],
            name="fk_matches_competition_team_2",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["competition_id", "referee_id"],
            [
                "competition_referees.competition_id",
                "competition_referees.referee_id",
            ],
            name="fk_matches_competition_referee",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "round_number BETWEEN 1 AND 3",
            name="ck_matches_round_number",
        ),
        CheckConstraint(
            "team_1_id < team_2_id",
            name="ck_matches_team_order",
        ),
        CheckConstraint(
            "(starts_at IS NULL AND ends_at IS NULL) OR "
            "(starts_at IS NOT NULL AND ends_at IS NOT NULL "
            "AND starts_at < ends_at)",
            name="ck_matches_schedule",
        ),
        CheckConstraint(
            "status IN ('incomplete', 'scheduled', 'in_progress', 'finished')",
            name="ck_matches_status",
        ),
        CheckConstraint(
            "(status = 'incomplete' AND starts_at IS NULL AND ends_at IS NULL) "
            "OR (status <> 'incomplete' AND starts_at IS NOT NULL "
            "AND ends_at IS NOT NULL)",
            name="ck_matches_status_schedule",
        ),
        UniqueConstraint(
            "competition_id",
            "team_1_id",
            "team_2_id",
            name="uq_matches_competition_team_pair",
        ),
        UniqueConstraint(
            "competition_id",
            "round_number",
            "referee_id",
            name="uq_matches_competition_round_referee",
        ),
        Index(
            "ix_matches_competition_round",
            "competition_id",
            "round_number",
        ),
        Index("ix_matches_team_1_schedule", "team_1_id", "starts_at"),
        Index("ix_matches_team_2_schedule", "team_2_id", "starts_at"),
        Index("ix_matches_referee_schedule", "referee_id", "starts_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    competition_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "competitions.id",
            ondelete="RESTRICT",
            name="fk_matches_competition_id_competitions",
        ),
        nullable=False,
    )
    team_1_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "teams.id",
            ondelete="RESTRICT",
            name="fk_matches_team_1_id_teams",
        ),
        nullable=False,
    )
    team_2_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "teams.id",
            ondelete="RESTRICT",
            name="fk_matches_team_2_id_teams",
        ),
        nullable=False,
    )
    referee_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            name="fk_matches_referee_id_users",
        ),
        nullable=False,
    )
    round_number = db.Column(db.Integer, nullable=False)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=True)
    ends_at = db.Column(db.DateTime(timezone=True), nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default="incomplete",
        server_default="incomplete",
    )

    competition = db.relationship("Competition", back_populates="matches")
    team_1 = db.relationship(
        "Team",
        foreign_keys=[team_1_id],
        back_populates="matches_as_team_1",
    )
    team_2 = db.relationship(
        "Team",
        foreign_keys=[team_2_id],
        back_populates="matches_as_team_2",
    )
    referee = db.relationship("User", back_populates="refereed_matches")
