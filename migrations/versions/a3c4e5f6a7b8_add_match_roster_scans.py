"""add persistent Match roster scans

Revision ID: a3c4e5f6a7b8
Revises: f2a3b4c5d6e7
Create Date: 2026-10-15

"""
from alembic import op
import sqlalchemy as sa


revision = "a3c4e5f6a7b8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "match_roster_scans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("expected_players", sa.Integer(), nullable=False),
        sa.Column("detected_faces", sa.Integer(), nullable=False),
        sa.Column("unique_recognized_players", sa.Integer(), nullable=False),
        sa.Column("group_image_file_name", sa.String(length=64), nullable=True),
        sa.Column("group_image_content_type", sa.String(length=127), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expected_players >= 0",
            name="ck_match_roster_scans_expected_players_nonnegative",
        ),
        sa.CheckConstraint(
            "detected_faces >= 0",
            name="ck_match_roster_scans_detected_faces_nonnegative",
        ),
        sa.CheckConstraint(
            "unique_recognized_players >= 0",
            name=(
                "ck_match_roster_scans_unique_recognized_players_nonnegative"
            ),
        ),
        sa.CheckConstraint(
            "(group_image_file_name IS NULL AND "
            "group_image_content_type IS NULL) OR "
            "(group_image_file_name IS NOT NULL AND "
            "group_image_content_type IS NOT NULL)",
            name="ck_match_roster_scans_group_image_pair",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name="fk_match_roster_scans_match_id_matches",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_match_roster_scans_team_id_teams",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_match_roster_scans_created_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_image_file_name",
            name="uq_match_roster_scans_group_image_file_name",
        ),
    )
    op.create_index(
        "ix_match_roster_scans_match_id",
        "match_roster_scans",
        ["match_id"],
    )
    op.create_table(
        "match_roster_scan_faces",
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("face_index", sa.Integer(), nullable=False),
        sa.Column("top", sa.Integer(), nullable=False),
        sa.Column("right", sa.Integer(), nullable=False),
        sa.Column("bottom", sa.Integer(), nullable=False),
        sa.Column("left", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=True),
        sa.Column("player_name_snapshot", sa.String(length=100), nullable=True),
        sa.Column("match_percentage", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "face_index >= 0", name="ck_match_roster_scan_faces_index"
        ),
        sa.CheckConstraint("top >= 0", name="ck_match_roster_scan_faces_top"),
        sa.CheckConstraint(
            "right >= 0", name="ck_match_roster_scan_faces_right"
        ),
        sa.CheckConstraint(
            "bottom >= 0", name="ck_match_roster_scan_faces_bottom"
        ),
        sa.CheckConstraint(
            "left >= 0", name="ck_match_roster_scan_faces_left"
        ),
        sa.CheckConstraint(
            "right > left AND bottom > top",
            name="ck_match_roster_scan_faces_box_order",
        ),
        sa.CheckConstraint(
            "match_percentage IS NULL OR match_percentage BETWEEN 0 AND 100",
            name="ck_match_roster_scan_faces_percentage_range",
        ),
        sa.CheckConstraint(
            "status IN ('declared_eligible', 'declared_sanctioned', "
            "'team_member_undeclared', "
            "'team_member_undeclared_sanctioned', 'unknown')",
            name="ck_match_roster_scan_faces_status",
        ),
        sa.CheckConstraint(
            "(status = 'unknown' AND player_id IS NULL AND "
            "player_name_snapshot IS NULL AND match_percentage IS NULL) OR "
            "(status <> 'unknown' AND player_id IS NOT NULL AND "
            "match_percentage IS NOT NULL)",
            name="ck_match_roster_scan_faces_recognition_fields",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["match_roster_scans.id"],
            name="fk_match_roster_scan_faces_scan_id_scans",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["players.id"],
            name="fk_match_roster_scan_faces_player_id_players",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scan_id", "face_index"),
        sa.UniqueConstraint(
            "scan_id",
            "face_index",
            "player_id",
            name="uq_match_roster_scan_faces_identity",
        ),
    )
    op.create_index(
        "ix_match_roster_scan_faces_player_id",
        "match_roster_scan_faces",
        ["player_id"],
    )
    op.create_table(
        "match_roster_scan_face_sanctions",
        sa.Column("scan_id", sa.Integer(), nullable=False),
        sa.Column("face_index", sa.Integer(), nullable=False),
        sa.Column("sanction_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("title_snapshot", sa.String(length=30), nullable=False),
        sa.Column("reason_snapshot", sa.String(length=250), nullable=False),
        sa.Column("remaining_matches_snapshot", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "char_length(trim(title_snapshot)) > 0",
            name="ck_match_roster_scan_face_sanctions_title_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(trim(reason_snapshot)) > 0",
            name="ck_match_roster_scan_face_sanctions_reason_not_blank",
        ),
        sa.CheckConstraint(
            "remaining_matches_snapshot >= 0",
            name="ck_match_roster_scan_face_sanctions_remaining_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id", "face_index", "player_id"],
            [
                "match_roster_scan_faces.scan_id",
                "match_roster_scan_faces.face_index",
                "match_roster_scan_faces.player_id",
            ],
            name="fk_match_roster_scan_face_sanctions_face",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sanction_id", "player_id"],
            ["player_sanctions.id", "player_sanctions.player_id"],
            name="fk_match_roster_scan_face_sanctions_sanction_player",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("scan_id", "face_index", "sanction_id"),
    )


def downgrade():
    op.drop_table("match_roster_scan_face_sanctions")
    op.drop_index(
        "ix_match_roster_scan_faces_player_id",
        table_name="match_roster_scan_faces",
    )
    op.drop_table("match_roster_scan_faces")
    op.drop_index(
        "ix_match_roster_scans_match_id",
        table_name="match_roster_scans",
    )
    op.drop_table("match_roster_scans")
