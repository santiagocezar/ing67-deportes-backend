"""Add Team management.

Revision ID: d4f2a7c91b30
Revises: a6c8f4d2190e
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa


revision = "d4f2a7c91b30"
down_revision = "a6c8f4d2190e"
branch_labels = None
depends_on = None


def upgrade():
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
            server_default=sa.true(),
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
            name="ck_teams_valid_gender_category",
        ),
        sa.CheckConstraint(
            "(is_enabled AND disabled_at IS NULL) OR "
            "(NOT is_enabled AND disabled_at IS NOT NULL)",
            name="ck_teams_enabled_timestamp_consistency",
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


def downgrade():
    op.drop_table("teams")
