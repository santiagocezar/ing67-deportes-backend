"""add historical manual roster review decisions

Revision ID: c9d0e1f2a3b4
Revises: b6e7f8a9b0c1, d4e5f6a7b8c9
Create Date: 2026-11-08

"""

from alembic import op
import sqlalchemy as sa


revision = "c9d0e1f2a3b4"
down_revision = ("b6e7f8a9b0c1", "d4e5f6a7b8c9")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "match_roster_scan_manual_reviews",
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "match_roster_scan_manual_reviews",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_match_roster_scan_manual_reviews_resolved_by_user_id_users",
        "match_roster_scan_manual_reviews",
        "users",
        ["resolved_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_match_roster_scan_manual_reviews_resolution_audit",
        "match_roster_scan_manual_reviews",
        "(status = 'resolved' AND resolved_by_user_id IS NOT NULL "
        "AND resolved_at IS NOT NULL) OR "
        "(status IN ('pending', 'expired') "
        "AND resolved_by_user_id IS NULL AND resolved_at IS NULL)",
    )
    op.create_table(
        "match_roster_scan_manual_review_decisions",
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("face_index", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("player_name_snapshot", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('belongs_to_team', 'does_not_belong_to_team', "
            "'inconclusive')",
            name="ck_match_roster_scan_manual_review_decisions_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'belongs_to_team' AND player_id IS NOT NULL "
            "AND player_name_snapshot IS NOT NULL "
            "AND char_length(trim(player_name_snapshot)) > 0) OR "
            "(outcome IN ('does_not_belong_to_team', 'inconclusive') "
            "AND player_id IS NULL AND player_name_snapshot IS NULL)",
            name=(
                "ck_match_roster_scan_manual_review_decisions_player_fields"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["scan_id", "face_index"],
            [
                "match_roster_scan_faces.scan_id",
                "match_roster_scan_faces.face_index",
            ],
            name="fk_match_roster_scan_manual_review_decisions_face",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name=(
                "fk_match_roster_scan_manual_review_decisions_player_id_players"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scan_id", "face_index"),
    )


def downgrade():
    op.drop_table("match_roster_scan_manual_review_decisions")
    op.drop_constraint(
        "ck_match_roster_scan_manual_reviews_resolution_audit",
        "match_roster_scan_manual_reviews",
        type_="check",
    )
    op.drop_constraint(
        "fk_match_roster_scan_manual_reviews_resolved_by_user_id_users",
        "match_roster_scan_manual_reviews",
        type_="foreignkey",
    )
    op.drop_column("match_roster_scan_manual_reviews", "resolved_at")
    op.drop_column("match_roster_scan_manual_reviews", "resolved_by_user_id")
