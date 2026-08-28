"""Manual "collect now" requests from the sources admin screen.

The Vercel function excludes collector dependencies (feedparser, yt-dlp, ...)
from its bundle and caps at 30s (`vercel.json`), so the web app cannot run a
collector itself. It queues a request here instead; the daily pipeline
(`culture ingest --queued`) drains the queue and stamps `fulfilled_at`.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CollectionRequest(Base):
    __tablename__ = "collection_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False)
    requested_by: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
