"""collection_requests table

"Collect now" in /admin/sources cannot run a collector in-function (Vercel
excludes collector deps and caps at 30s, see vercel.json); it queues a row
here instead. `culture ingest --queued` drains the queue and stamps
`fulfilled_at`.

Revision ID: 1c9d917396b8
Revises: ba99893057ef
Create Date: 2026-08-28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "1c9d917396b8"
down_revision: Union[str, Sequence[str], None] = "ba99893057ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("requested_by", sa.Text()),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_collection_requests_source_id", "collection_requests", ["source_id"])
    op.execute("ALTER TABLE collection_requests ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE collection_requests DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_collection_requests_source_id", table_name="collection_requests")
    op.drop_table("collection_requests")
