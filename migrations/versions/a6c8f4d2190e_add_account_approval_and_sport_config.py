"""Add account approval and sport match configuration.

Revision ID: a6c8f4d2190e
Revises: 3e22b5f59faa
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a6c8f4d2190e"
down_revision = "3e22b5f59faa"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_users_valid_role", "users", type_="check")
    op.add_column(
        "users",
        sa.Column("requested_role", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=20),
        server_default="user",
        existing_nullable=False,
    )
    op.create_index("ix_users_role", "users", ["role"], unique=False)
    op.create_check_constraint(
        "ck_users_valid_role",
        "users",
        "role IN ('user', 'referee', 'federation_delegate', 'administrator')",
    )
    op.create_check_constraint(
        "ck_users_valid_requested_role",
        "users",
        "requested_role IS NULL OR "
        "requested_role IN ('referee', 'federation_delegate')",
    )

    op.add_column(
        "sports",
        sa.Column("match_duration", sa.Integer(), nullable=True),
    )
    op.add_column(
        "sports",
        sa.Column(
            "resolution_methods",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE sports
            SET match_duration = 90,
                resolution_methods = CAST(:resolution_methods AS jsonb)
            WHERE normalized_name = 'futbol'
            """
        ),
        {
            "resolution_methods": (
                '[{"code":"penalty","name":"penales"},'
                '{"code":"overtime","name":"tiempo extra"}]'
            )
        },
    )
    connection.execute(
        sa.text(
            """
            UPDATE sports
            SET match_duration = 40,
                resolution_methods = CAST(:resolution_methods AS jsonb)
            WHERE normalized_name = 'basquet'
            """
        ),
        {
            "resolution_methods": (
                '[{"code":"overtime","name":"tiempo extra"}]'
            )
        },
    )

    incomplete_sports = connection.execute(
        sa.text(
            """
            SELECT normalized_name
            FROM sports
            WHERE match_duration IS NULL OR resolution_methods IS NULL
            ORDER BY normalized_name
            """
        )
    ).scalars().all()
    if incomplete_sports:
        names = ", ".join(incomplete_sports)
        raise RuntimeError(
            "Cannot migrate sports without approved match configuration: "
            f"{names}."
        )

    op.alter_column(
        "sports",
        "match_duration",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.alter_column(
        "sports",
        "resolution_methods",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_sports_match_duration_positive",
        "sports",
        "match_duration > 0",
    )
    op.create_check_constraint(
        "ck_sports_resolution_methods_non_empty_array",
        "sports",
        "CASE WHEN jsonb_typeof(resolution_methods) = 'array' "
        "THEN jsonb_array_length(resolution_methods) > 0 ELSE FALSE END",
    )


def downgrade():
    connection = op.get_bind()
    incompatible_roles = connection.execute(
        sa.text(
            """
            SELECT role
            FROM users
            WHERE role NOT IN ('administrator', 'referee')
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if incompatible_roles is not None:
        raise RuntimeError(
            "Cannot downgrade while user or federation_delegate accounts exist."
        )

    op.drop_constraint(
        "ck_sports_resolution_methods_non_empty_array",
        "sports",
        type_="check",
    )
    op.drop_constraint(
        "ck_sports_match_duration_positive",
        "sports",
        type_="check",
    )
    op.drop_column("sports", "resolution_methods")
    op.drop_column("sports", "match_duration")

    op.drop_constraint(
        "ck_users_valid_requested_role",
        "users",
        type_="check",
    )
    op.drop_constraint("ck_users_valid_role", "users", type_="check")
    op.drop_index("ix_users_role", table_name="users")
    op.alter_column(
        "users",
        "role",
        existing_type=sa.String(length=20),
        server_default="referee",
        existing_nullable=False,
    )
    op.drop_column("users", "is_active")
    op.drop_column("users", "requested_role")
    op.create_check_constraint(
        "ck_users_valid_role",
        "users",
        "role IN ('administrator', 'referee')",
    )
