"""add Team management and Sport capacities

Revision ID: a8c4e12f6b90
Revises: 3e22b5f59faa
Create Date: 2026-09-01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a8c4e12f6b90"
down_revision = "3e22b5f59faa"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "sports",
        sa.Column("max_players_in_game", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE sports SET max_players_in_game = max_players "
        "WHERE max_players_in_game IS NULL"
    )
    op.drop_constraint(
        "ck_sports_max_players_range",
        "sports",
        type_="check",
    )
    op.execute(
        "UPDATE sports SET max_players = 22, max_players_in_game = 11 "
        "WHERE normalized_name = 'futbol'"
    )
    op.execute(
        "UPDATE sports SET max_players = 15, max_players_in_game = 5 "
        "WHERE normalized_name = 'basquet'"
    )
    op.alter_column(
        "sports",
        "max_players_in_game",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_sports_max_players_positive",
        "sports",
        "max_players > 0",
    )
    op.create_check_constraint(
        "ck_sports_max_players_in_game_positive",
        "sports",
        "max_players_in_game > 0",
    )
    op.create_check_constraint(
        "ck_sports_capacity_order",
        "sports",
        "max_players_in_game <= max_players",
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("normalized_name", sa.String(length=100), nullable=False),
        sa.Column("sport_id", sa.Integer(), nullable=False),
        sa.Column("gender_category", sa.String(length=10), nullable=False),
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
            name="ck_teams_name_not_blank",
        ),
        sa.CheckConstraint(
            "gender_category IN ('male', 'female')",
            name="ck_teams_gender_category",
        ),
        sa.CheckConstraint(
            "(is_enabled = TRUE AND disabled_at IS NULL) OR "
            "(is_enabled = FALSE AND disabled_at IS NOT NULL)",
            name="ck_teams_enabled_disabled_at",
        ),
        sa.ForeignKeyConstraint(
            ["sport_id"],
            ["sports.id"],
            name="fk_teams_sport_id_sports",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "normalized_name",
            "sport_id",
            "gender_category",
            name="uq_teams_normalized_name_sport_gender",
        ),
    )
    op.create_index("ix_teams_sport_id", "teams", ["sport_id"])


def downgrade():
    op.drop_index("ix_teams_sport_id", table_name="teams")
    op.drop_table("teams")

    op.drop_constraint(
        "ck_sports_capacity_order",
        "sports",
        type_="check",
    )
    op.drop_constraint(
        "ck_sports_max_players_in_game_positive",
        "sports",
        type_="check",
    )
    op.drop_constraint(
        "ck_sports_max_players_positive",
        "sports",
        type_="check",
    )
    op.execute(
        "UPDATE sports SET max_players = LEAST(max_players, 20)"
    )
    op.execute(
        "UPDATE sports SET max_players = 11 "
        "WHERE normalized_name = 'futbol'"
    )
    op.execute(
        "UPDATE sports SET max_players = 5 "
        "WHERE normalized_name = 'basquet'"
    )
    op.drop_column("sports", "max_players_in_game")
    op.create_check_constraint(
        "ck_sports_max_players_range",
        "sports",
        "max_players BETWEEN 1 AND 20",
    )
