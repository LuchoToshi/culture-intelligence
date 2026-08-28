"""Delivery ledger for transactional email (verification, approval, reset...).

Written in the same transaction as the state change it announces, so a
mutation and its notification are never silently out of sync. `dedupe_key`
(template + target + state-change id) makes retried actions idempotent —
a second approve on an already-approved row cannot double-send. Delivery
failure is recorded here and retried later; it never rolls back the account
mutation that queued it (`status='queued'` -> 'sent' | 'failed').
"""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_email: Mapped[str] = mapped_column(Text, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_event: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('queued', 'sent', 'failed')", name="ck_email_log_status"),
        Index("ix_email_log_status", "status"),
    )
