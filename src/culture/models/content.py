from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from culture.database import Base, JSONField
from culture.models.source import Source


class ContentType(StrEnum):
    ARTICLE = "article"
    VIDEO = "video"
    PODCAST = "podcast"
    # Manually submitted social post (Instagram/TikTok/...) — often image-led.
    POST = "post"


class ExtractionStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class TranscriptStatus(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_ATTEMPTED = "not_attempted"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class ProcessingStatus(StrEnum):
    NEW = "new"
    READY = "ready"
    # Deliberately excluded from analysis (e.g. deep back-catalog items); explicit, not silent.
    SKIPPED = "skipped"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ContentItem(Base):
    __tablename__ = "content_items"
    __table_args__ = (
        # DB-level dedup guarantees: same source + same platform id, or same
        # source + same normalized URL, cannot exist twice.
        UniqueConstraint("source_id", "external_id", name="uq_content_source_external_id"),
        UniqueConstraint("source_id", "url", name="uq_content_source_url"),
        Index("ix_content_items_content_hash", "content_hash"),
        Index("ix_content_items_published_at", "published_at"),
        Index("ix_content_items_processing_status", "processing_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT: collected history must never disappear because a source row was deleted.
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"))
    external_id: Mapped[str | None] = mapped_column(String(300))
    # url holds the normalized URL used for dedup; the original as-fetched URL
    # belongs in metadata_json.
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(30))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    description: Mapped[str | None] = mapped_column(Text)
    raw_text: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(20))
    extraction_status: Mapped[str] = mapped_column(
        String(30), default=ExtractionStatus.NOT_ATTEMPTED.value
    )
    transcript_status: Mapped[str] = mapped_column(
        String(30), default=TranscriptStatus.NOT_APPLICABLE.value
    )
    metadata_json: Mapped[dict] = mapped_column(JSONField, default=dict)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(String(30), default=ProcessingStatus.NEW.value)
    # When this item was last evaluated against the signal registry; null = pending.
    signals_processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    source: Mapped[Source] = relationship()

    def __repr__(self) -> str:
        return f"<ContentItem {self.id} source_id={self.source_id} {self.url!r}>"
