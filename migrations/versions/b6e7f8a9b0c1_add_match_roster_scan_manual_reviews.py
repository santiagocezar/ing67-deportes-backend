"""add consented Match roster scan manual reviews

Revision ID: b6e7f8a9b0c1
Revises: a3c4e5f6a7b8
Create Date: 2026-11-08

"""

from alembic import op
import sqlalchemy as sa


revision = "b6e7f8a9b0c1"
down_revision = "a3c4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_roster_scan_manual_reviews",
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'resolved', 'expired')",
            name="ck_match_roster_scan_manual_reviews_status",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["match_roster_scans.id"],
            name="fk_match_roster_scan_manual_reviews_scan_id_scans",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scan_id"),
    )

    # Earlier scans did not collect an explicit retention decision. Remove their
    # private references so a later reconciliation can delete the orphaned files.
    op.execute(
        "UPDATE match_roster_scans "
        "SET group_image_file_name = NULL, group_image_content_type = NULL "
        "WHERE group_image_file_name IS NOT NULL"
    )


def downgrade():
    op.drop_table("match_roster_scan_manual_reviews")
