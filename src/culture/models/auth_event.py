"""Audit trail for authentication and access-control events.

Stdout logs on Vercel are ephemeral; an access decision needs a durable
record. One append-only table, written best-effort — an audit failure must
never block a login — and readable only from operational tooling. Rows may
contain email addresses (including from access requests), so this table is
internal data under the same policy as source identities.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base


class AuthEvent(Base):
    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # link_requested, access_requested, login_success, login_expired,
    # demo_login, demo_login_failed, logout, rate_limited, access_denied,
    # plus the account/security/sources/settings vocabulary added for the
    # Supabase-backed auth model (see culture.web.auth and culture.web.admin).
    event: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    # Who performed the action, when it wasn't the subject themself (an
    # owner approving/suspending another account). Null for self-service
    # events (login, logout, registration).
    actor: Mapped[str | None] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_auth_events_created_at", "created_at"),
        Index("ix_auth_events_event", "event"),
    )
