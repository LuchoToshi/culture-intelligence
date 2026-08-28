"""sources: normalized_identifier, archived_at/by, cadence

`normalized_identifier` backs duplicate detection in /admin/sources (an
Instagram handle and its profile URL should collide as the same source, see
culture.utils.identifiers) — unique but nullable, since existing rows have no
normalized form until backfilled or next edited. `archived_at`/`archived_by`
implement archive-over-delete: no hard delete exists in the admin UI.
`cadence` replaces overloading `collection_notes` for collection frequency.

Revision ID: ba99893057ef
Revises: bd42a54e85df
Create Date: 2026-08-28
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "ba99893057ef"
down_revision: Union[str, Sequence[str], None] = "bd42a54e85df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("normalized_identifier", sa.String(300), nullable=True))
    op.add_column("sources", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("sources", sa.Column("archived_by", sa.String(300), nullable=True))
    op.add_column(
        "sources",
        sa.Column("cadence", sa.String(20), nullable=False, server_default="weekly"),
    )
    op.create_check_constraint(
        "ck_sources_cadence", "sources", "cadence IN ('hourly', 'daily', 'weekly')"
    )
    op.create_index(
        "ix_sources_normalized_identifier", "sources", ["normalized_identifier"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_sources_normalized_identifier", table_name="sources")
    op.drop_constraint("ck_sources_cadence", "sources", type_="check")
    op.drop_column("sources", "cadence")
    op.drop_column("sources", "archived_by")
    op.drop_column("sources", "archived_at")
    op.drop_column("sources", "normalized_identifier")
