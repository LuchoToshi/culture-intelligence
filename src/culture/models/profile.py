"""Account profile: one row per Supabase Auth identity.

`id` is the Supabase `auth.users.id` UUID. There is no Python-level
`ForeignKey` to that table here: `auth.users` lives in Supabase's own schema,
outside this app's SQLAlchemy metadata, so an ORM `ForeignKey("auth.users.id")`
would break `Base.metadata.create_all()` in the unit-test suite (it resolves
FKs against registered metadata, not a live database). The real constraint is
added as raw DDL in the migration, which only ever runs against a real
(Supabase-backed) Postgres.

Registration creates a `pending` row, never access — the owner grants `member`
role through `/admin/approvals`. Exactly one `owner` row can exist (enforced
by a partial unique index on `role`, see the migration); it is never assigned
through the web app.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_SUSPENDED = "suspended"
STATUS_DISABLED = "disabled"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default=STATUS_PENDING, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(Text)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[str | None] = mapped_column(Text)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_by: Mapped[str | None] = mapped_column(Text)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_by: Mapped[str | None] = mapped_column(Text)
    # Owner-only context for a decision (e.g. why an account was rejected).
    # Never emailed to the account holder.
    last_status_reason: Mapped[str | None] = mapped_column(Text)
    legacy_magic_link: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(f"role IN ('{ROLE_OWNER}', '{ROLE_MEMBER}')", name="ck_profiles_role"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'suspended', 'disabled')",
            name="ck_profiles_status",
        ),
        Index("ix_profiles_status_created_at", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Profile {self.id} {self.email!r} role={self.role} status={self.status}>"
