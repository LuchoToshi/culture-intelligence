"""email_log table

Delivery ledger for transactional email. `dedupe_key` (template + target +
state-change id) makes retried account-mutation emails idempotent. Backs the
owner-only Outbox view in /admin.

Revision ID: 848708701be7
Revises: 1c9d917396b8
Create Date: 2026-08-28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "848708701be7"
down_revision: Union[str, Sequence[str], None] = "1c9d917396b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("trigger_event", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("dedupe_key", sa.Text(), nullable=False, unique=True),
        sa.CheckConstraint("status IN ('queued', 'sent', 'failed')", name="ck_email_log_status"),
    )
    op.create_index("ix_email_log_status", "email_log", ["status"])
    op.execute("ALTER TABLE email_log ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE email_log DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_email_log_status", table_name="email_log")
    op.drop_table("email_log")
