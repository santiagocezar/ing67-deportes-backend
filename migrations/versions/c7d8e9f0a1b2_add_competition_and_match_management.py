"""add Competition and Match management

Revision ID: c7d8e9f0a1b2
Revises: b4e6c1d2a9f0
Create Date: 2026-09-15

"""
from alembic import op
import sqlalchemy as sa


revision = "c7d8e9f0a1b2"
down_revision = "b4e6c1d2a9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "competitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("sport_id", sa.Integer(), nullable=False),
        sa.Column("gender", sa.String(length=10), nullable=False),
        sa.Column(
            "starts_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(trim(name)) > 0",
            name="ck_competitions_name_not_blank",
        ),
        sa.CheckConstraint(
            "gender IN ('male', 'female')",
            name="ck_competitions_gender",
        ),
        sa.CheckConstraint(
            "starts_at < ends_at",
            name="ck_competitions_date_range",
        ),
        sa.CheckConstraint(
            "status IN "
            "('scheduled', 'in_progress', 'finished', 'discarded')",
            name="ck_competitions_status",
        ),
        sa.CheckConstraint(
            "(is_enabled = TRUE AND disabled_at IS NULL AND "
            "status <> 'discarded') OR "
            "(is_enabled = FALSE AND disabled_at IS NOT NULL AND "
            "status = 'discarded')",
            name="ck_competitions_enabled_status",
        ),
        sa.ForeignKeyConstraint(
            ["sport_id"],
            ["sports.id"],
            name="fk_competitions_sport_id_sports",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_competitions_sport_id", "competitions", ["sport_id"]
    )
    op.create_index(
        "ix_competitions_starts_at", "competitions", ["starts_at"]
    )
    op.create_index(
        "ix_competitions_status", "competitions", ["status"]
    )

    op.create_table(
        "competition_teams",
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["competition_id"],
            ["competitions.id"],
            name="fk_competition_teams_competition_id_competitions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_competition_teams_team_id_teams",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("competition_id", "team_id"),
    )

    op.create_table(
        "competition_roster_players",
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["competition_id", "team_id"],
            [
                "competition_teams.competition_id",
                "competition_teams.team_id",
            ],
            name="fk_competition_roster_players_competition_team",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_competition_roster_players_player_id_players",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("competition_id", "team_id", "player_id"),
        sa.UniqueConstraint(
            "competition_id",
            "player_id",
            name="uq_competition_roster_players_competition_player",
        ),
    )
    op.create_index(
        "ix_competition_roster_players_player_id",
        "competition_roster_players",
        ["player_id"],
    )

    op.create_table(
        "competition_referees",
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("referee_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["competition_id"],
            ["competitions.id"],
            name="fk_competition_referees_competition_id_competitions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["referee_id"],
            ["users.id"],
            name="fk_competition_referees_referee_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("competition_id", "referee_id"),
    )
    op.create_index(
        "ix_competition_referees_referee_id",
        "competition_referees",
        ["referee_id"],
    )

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competition_id", sa.Integer(), nullable=False),
        sa.Column("team_1_id", sa.Integer(), nullable=False),
        sa.Column("team_2_id", sa.Integer(), nullable=False),
        sa.Column("referee_id", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'incomplete'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "round_number BETWEEN 1 AND 3",
            name="ck_matches_round_number",
        ),
        sa.CheckConstraint(
            "team_1_id < team_2_id",
            name="ck_matches_team_order",
        ),
        sa.CheckConstraint(
            "(starts_at IS NULL AND ends_at IS NULL) OR "
            "(starts_at IS NOT NULL AND ends_at IS NOT NULL "
            "AND starts_at < ends_at)",
            name="ck_matches_schedule",
        ),
        sa.CheckConstraint(
            "status IN "
            "('incomplete', 'scheduled', 'in_progress', 'finished')",
            name="ck_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'incomplete' AND starts_at IS NULL "
            "AND ends_at IS NULL) OR "
            "(status <> 'incomplete' AND starts_at IS NOT NULL "
            "AND ends_at IS NOT NULL)",
            name="ck_matches_status_schedule",
        ),
        sa.ForeignKeyConstraint(
            ["competition_id"],
            ["competitions.id"],
            name="fk_matches_competition_id_competitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["team_1_id"],
            ["teams.id"],
            name="fk_matches_team_1_id_teams",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["team_2_id"],
            ["teams.id"],
            name="fk_matches_team_2_id_teams",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["referee_id"],
            ["users.id"],
            name="fk_matches_referee_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["competition_id", "team_1_id"],
            [
                "competition_teams.competition_id",
                "competition_teams.team_id",
            ],
            name="fk_matches_competition_team_1",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["competition_id", "team_2_id"],
            [
                "competition_teams.competition_id",
                "competition_teams.team_id",
            ],
            name="fk_matches_competition_team_2",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["competition_id", "referee_id"],
            [
                "competition_referees.competition_id",
                "competition_referees.referee_id",
            ],
            name="fk_matches_competition_referee",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "competition_id",
            "team_1_id",
            "team_2_id",
            name="uq_matches_competition_team_pair",
        ),
        sa.UniqueConstraint(
            "competition_id",
            "round_number",
            "referee_id",
            name="uq_matches_competition_round_referee",
        ),
    )
    op.create_index(
        "ix_matches_competition_round",
        "matches",
        ["competition_id", "round_number"],
    )
    op.create_index(
        "ix_matches_team_1_schedule",
        "matches",
        ["team_1_id", "starts_at"],
    )
    op.create_index(
        "ix_matches_team_2_schedule",
        "matches",
        ["team_2_id", "starts_at"],
    )
    op.create_index(
        "ix_matches_referee_schedule",
        "matches",
        ["referee_id", "starts_at"],
    )


def downgrade():
    op.drop_index("ix_matches_referee_schedule", table_name="matches")
    op.drop_index("ix_matches_team_2_schedule", table_name="matches")
    op.drop_index("ix_matches_team_1_schedule", table_name="matches")
    op.drop_index("ix_matches_competition_round", table_name="matches")
    op.drop_table("matches")
    op.drop_index(
        "ix_competition_referees_referee_id",
        table_name="competition_referees",
    )
    op.drop_table("competition_referees")
    op.drop_index(
        "ix_competition_roster_players_player_id",
        table_name="competition_roster_players",
    )
    op.drop_table("competition_roster_players")
    op.drop_table("competition_teams")
    op.drop_index("ix_competitions_status", table_name="competitions")
    op.drop_index("ix_competitions_starts_at", table_name="competitions")
    op.drop_index("ix_competitions_sport_id", table_name="competitions")
    op.drop_table("competitions")
