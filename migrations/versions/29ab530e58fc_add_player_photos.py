"""add Player photos

Revision ID: 29ab530e58fc
Revises: c7d8e9f0a1b2
Create Date: 2026-09-26

"""
from alembic import op
import sqlalchemy as sa


revision = "29ab530e58fc"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "player_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png')",
            name="ck_player_photos_content_type",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_player_photos_player_id_players",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_name", name="uq_player_photos_file_name"),
    )
    op.create_index(
        "ix_player_photos_player_id",
        "player_photos",
        ["player_id"],
    )


def downgrade():
    op.drop_index("ix_player_photos_player_id", table_name="player_photos")
    op.drop_table("player_photos")
