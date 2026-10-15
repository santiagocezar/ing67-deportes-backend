"""add match-origin player sanctions

Revision ID: e1f4c8a2b6d0
Revises: 29ab530e58fc
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa


revision = "e1f4c8a2b6d0"
down_revision = "29ab530e58fc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "player_sanctions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source_match_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.String(length=250), nullable=False),
        sa.Column("suspension_matches", sa.Integer(), nullable=False),
        sa.Column("remaining_matches", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("annulled_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("annulled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("served_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(trim(title)) > 0",
            name="ck_player_sanctions_title_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(trim(reason)) > 0",
            name="ck_player_sanctions_reason_not_blank",
        ),
        sa.CheckConstraint(
            "suspension_matches BETWEEN 1 AND 10",
            name="ck_player_sanctions_suspension_matches",
        ),
        sa.CheckConstraint(
            "remaining_matches BETWEEN 0 AND suspension_matches",
            name="ck_player_sanctions_remaining_matches",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'served', 'annulled')",
            name="ck_player_sanctions_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND remaining_matches > 0 "
            "AND served_at IS NULL AND annulled_at IS NULL "
            "AND annulled_by_user_id IS NULL) OR "
            "(status = 'served' AND remaining_matches = 0 "
            "AND served_at IS NOT NULL AND annulled_at IS NULL "
            "AND annulled_by_user_id IS NULL) OR "
            "(status = 'annulled' AND annulled_at IS NOT NULL "
            "AND annulled_by_user_id IS NOT NULL AND served_at IS NULL)",
            name="ck_player_sanctions_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_player_sanctions_player_id_players",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_match_id"],
            ["matches.id"],
            name="fk_player_sanctions_source_match_id_matches",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_player_sanctions_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_player_sanctions_updated_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["annulled_by_user_id"],
            ["users.id"],
            name="fk_player_sanctions_annulled_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "player_id", name="uq_player_sanctions_id_player"
        ),
    )
    op.create_index(
        "ix_player_sanctions_player_status_created_at",
        "player_sanctions",
        ["player_id", "status", "created_at"],
    )
    op.create_table(
        "player_sanction_teams",
        sa.Column("sanction_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["sanction_id"],
            ["player_sanctions.id"],
            name="fk_player_sanction_teams_sanction_id_player_sanctions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_player_sanction_teams_team_id_teams",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("sanction_id", "team_id"),
    )


def downgrade():
    op.drop_table("player_sanction_teams")
    op.drop_index(
        "ix_player_sanctions_player_status_created_at",
        table_name="player_sanctions",
    )
    op.drop_table("player_sanctions")
