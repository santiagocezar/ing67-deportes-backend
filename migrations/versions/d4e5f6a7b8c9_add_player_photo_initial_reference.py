"""add Player photo initial reference

Revision ID: d4e5f6a7b8c9
Revises: a3c4e5f6a7b8
Create Date: 2026-11-07

"""

from alembic import op
import sqlalchemy as sa


revision = "d4e5f6a7b8c9"
down_revision = "a3c4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "player_photos",
        sa.Column(
            "is_initial_reference",
            sa.Boolean(),
            nullable=True,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        WITH ranked_photos AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY player_id
                       ORDER BY id
                   ) AS position
            FROM player_photos
        )
        UPDATE player_photos
        SET is_initial_reference = (ranked_photos.position = 1)
        FROM ranked_photos
        WHERE player_photos.id = ranked_photos.id
        """
    )
    op.alter_column(
        "player_photos",
        "is_initial_reference",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    op.create_index(
        "uq_player_photos_initial_reference",
        "player_photos",
        ["player_id"],
        unique=True,
        postgresql_where=sa.text("is_initial_reference = true"),
    )


def downgrade():
    op.drop_index(
        "uq_player_photos_initial_reference",
        table_name="player_photos",
    )
    op.drop_column("player_photos", "is_initial_reference")
