"""Owner-editable runtime registration controls.

Single-row table: `id` is always `1`, enforced by a CHECK constraint rather
than a singleton pattern in code, so a stray insert fails loudly instead of
silently creating a second row that some code paths read and others don't.
`allowed_domains` / `allowlist_emails` are comma-joined text, matching the
existing `Settings.allowed_emails` env-var convention (`config.py`) rather
than a Postgres array type, so the same parsing helper and dialect-portable
column type work in both the SQLite test suite and production.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    registration_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    invite_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allowed_domains: Mapped[str] = mapped_column(Text, default="", nullable=False)
    allowlist_emails: Mapped[str] = mapped_column(Text, default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (CheckConstraint("id = 1", name="ck_app_settings_singleton"),)

    @property
    def allowed_domain_set(self) -> set[str]:
        return {d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()}

    @property
    def allowlist_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowlist_emails.split(",") if e.strip()}
