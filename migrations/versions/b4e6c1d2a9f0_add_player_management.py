"""add Player management

Revision ID: b4e6c1d2a9f0
Revises: a8c4e12f6b90
Create Date: 2026-09-03

"""
from alembic import op
import sqlalchemy as sa


revision = "b4e6c1d2a9f0"
down_revision = "a8c4e12f6b90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("gender", sa.String(length=10), nullable=False),
        sa.Column("sport_id", sa.Integer(), nullable=False),
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
            name="ck_players_name_not_blank",
        ),
        sa.CheckConstraint(
            "gender IN ('male', 'female')",
            name="ck_players_gender",
        ),
        sa.CheckConstraint(
            "(is_enabled = TRUE AND disabled_at IS NULL) OR "
            "(is_enabled = FALSE AND disabled_at IS NOT NULL)",
            name="ck_players_enabled_disabled_at",
        ),
        sa.ForeignKeyConstraint(
            ["sport_id"],
            ["sports.id"],
            name="fk_players_sport_id_sports",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_players_sport_id", "players", ["sport_id"])

    op.create_table(
        "team_players",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_team_players_team_id_teams",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_team_players_player_id_players",
        ),
        sa.PrimaryKeyConstraint("team_id", "player_id"),
    )
    op.create_index(
        "ix_team_players_player_id",
        "team_players",
        ["player_id"],
    )


def downgrade():
    op.drop_index(
        "ix_team_players_player_id",
        table_name="team_players",
    )
    op.drop_table("team_players")
    op.drop_index("ix_players_sport_id", table_name="players")
    op.drop_table("players")
