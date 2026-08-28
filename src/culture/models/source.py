from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base, JSONField


class Platform(StrEnum):
    WEB = "web"
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    X = "x"
    SUBSTACK = "substack"
    REDDIT = "reddit"
    PODCAST = "podcast"
    NEWSLETTER = "newsletter"
    OTHER = "other"


class SourceTier(StrEnum):
    CANDIDATE = "candidate"
    WATCH = "watch"
    CORE = "core"
    DORMANT = "dormant"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    # Stored as plain strings (values from Platform / SourceTier) so the
    # vocabulary can grow without a migration.
    platform: Mapped[str] = mapped_column(String(50))
    source_type: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text)
    feed_url: Mapped[str | None] = mapped_column(Text)
    external_identifier: Mapped[str | None] = mapped_column(String(300))
    region: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    city: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(20))
    categories: Mapped[list] = mapped_column(JSONField, default=list)
    intelligence_layers: Mapped[list] = mapped_column(JSONField, default=list)
    tier: Mapped[str] = mapped_column(String(20), default=SourceTier.CANDIDATE.value)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    collection_method: Mapped[str | None] = mapped_column(String(50))
    collection_notes: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Automated-discovery bookkeeping (mention counts, citing sources, dates).
    # Only populated on candidates the discovery service created or tracks.
    discovery_json: Mapped[dict] = mapped_column(JSONField, default=dict)
    # When a human last reviewed this source — used by the weekly review queue
    # for platforms with no automated collector (Instagram, TikTok).
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Canonical form of the account/feed identity (lowercased host, stripped
    # tracking params/trailing slash, bare @handle, resolved channel id for
    # YouTube) — see culture.utils.identifiers. Lets "@Handle" and
    # "instagram.com/handle/" collide as the same duplicate source.
    normalized_identifier: Mapped[str | None] = mapped_column(String(300))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(String(300))
    cadence: Mapped[str] = mapped_column(String(20), default="weekly")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "cadence IN ('hourly', 'daily', 'weekly')", name="ck_sources_cadence"
        ),
        Index(
            "ix_sources_normalized_identifier", "normalized_identifier", unique=True
        ),
    )

    def __repr__(self) -> str:
        return f"<Source {self.id} {self.name!r} platform={self.platform} tier={self.tier}>"
