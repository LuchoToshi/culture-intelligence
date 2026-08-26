"""auth_events audit table

Durable audit trail for authentication and access-control events: login
links requested, access requests from unknown emails, successful and expired
logins, demo logins, rate-limit hits, and role-based denials. Append-only;
written best-effort by culture.web.auth.record_event.

Revision ID: f4a1c9d27b31
Revises: e326d50dae29
Create Date: 2026-08-26
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a1c9d27b31"
down_revision: Union[str, Sequence[str], None] = "e326d50dae29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("ip", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])
    op.create_index("ix_auth_events_event", "auth_events", ["event"])


def downgrade() -> None:
    op.drop_index("ix_auth_events_event", table_name="auth_events")
    op.drop_index("ix_auth_events_created_at", table_name="auth_events")
    op.drop_table("auth_events")
