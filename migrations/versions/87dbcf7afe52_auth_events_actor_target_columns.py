"""auth_events: actor, target, request_id columns

Additive columns for the account/admin audit vocabulary (owner_provisioned,
account_approved, source_created, settings_changed, ...): who performed an
action on someone else's behalf, and what it targeted. Existing self-service
events (login, logout, registration) leave these null. Append-only and
RLS-protected the same way as every other new table here: no client-role
policy exists, so only the app's privileged connection can read or write it.

Revision ID: 87dbcf7afe52
Revises: f8a21b02712b
Create Date: 2026-08-28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "87dbcf7afe52"
down_revision: Union[str, Sequence[str], None] = "f8a21b02712b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("auth_events", sa.Column("actor", sa.Text(), nullable=True))
    op.add_column("auth_events", sa.Column("target_type", sa.Text(), nullable=True))
    op.add_column("auth_events", sa.Column("target_id", sa.Text(), nullable=True))
    op.add_column("auth_events", sa.Column("request_id", sa.Text(), nullable=True))
    op.execute("ALTER TABLE auth_events ENABLE ROW LEVEL SECURITY")
    # No policies: deny-by-default for anon/authenticated, matching every
    # other RLS-protected table in this migration set.


def downgrade() -> None:
    op.execute("ALTER TABLE auth_events DISABLE ROW LEVEL SECURITY")
    op.drop_column("auth_events", "request_id")
    op.drop_column("auth_events", "target_id")
    op.drop_column("auth_events", "target_type")
    op.drop_column("auth_events", "actor")
