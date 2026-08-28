"""Add rotating auth sessions and birthdate date type.

Revision ID: 78feb1bb58cd
Revises:
Create Date: 2026-08-27
"""

from alembic import op
import sqlalchemy as sa


revision = "78feb1bb58cd"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("current_refresh_jti", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_refresh_jti"),
    )
    op.create_index(
        "ix_auth_sessions_user_id",
        "auth_sessions",
        ["user_id"],
        unique=False,
    )
    op.alter_column(
        "users",
        "birthdate",
        existing_type=sa.String(length=50),
        type_=sa.Date(),
        existing_nullable=False,
        postgresql_using="birthdate::date",
    )


def downgrade():
    op.alter_column(
        "users",
        "birthdate",
        existing_type=sa.Date(),
        type_=sa.String(length=50),
        existing_nullable=False,
        postgresql_using="birthdate::text",
    )
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
