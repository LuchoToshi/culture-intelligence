"""app_settings table

Single-row, owner-editable registration controls (open/closed, invite-only,
allowed domains, an optional pre-approval list). `id = 1` is enforced by a
CHECK constraint rather than application code, and seeded here so the row
always exists (the admin settings screen always has something to read and
update, never a missing-row branch).

Revision ID: bd42a54e85df
Revises: 87dbcf7afe52
Create Date: 2026-08-28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "bd42a54e85df"
down_revision: Union[str, Sequence[str], None] = "87dbcf7afe52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("registration_open", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("invite_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_domains", sa.Text(), nullable=False, server_default=""),
        sa.Column("allowlist_emails", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Text()),
        sa.CheckConstraint("id = 1", name="ck_app_settings_singleton"),
    )
    op.execute("INSERT INTO app_settings (id) VALUES (1)")
    op.execute("ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE app_settings DISABLE ROW LEVEL SECURITY")
    op.drop_table("app_settings")
