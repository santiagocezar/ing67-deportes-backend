"""add player sanction Match consumptions

Revision ID: f2a3b4c5d6e7
Revises: e1f4c8a2b6d0
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa


revision = "f2a3b4c5d6e7"
down_revision = "e1f4c8a2b6d0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "player_sanction_match_consumptions",
        sa.Column("sanction_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sanction_id", "player_id"],
            ["player_sanctions.id", "player_sanctions.player_id"],
            name="fk_player_sanction_match_consumptions_sanction_player",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_player_sanction_match_consumptions_match_id_matches",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("sanction_id", "match_id"),
        sa.UniqueConstraint(
            "player_id",
            "match_id",
            name="uq_player_sanction_match_consumptions_player_match",
        ),
    )
    op.create_index(
        "ix_player_sanction_match_consumptions_match_id",
        "player_sanction_match_consumptions",
        ["match_id"],
    )


def downgrade():
    op.drop_index(
        "ix_player_sanction_match_consumptions_match_id",
        table_name="player_sanction_match_consumptions",
    )
    op.drop_table("player_sanction_match_consumptions")
