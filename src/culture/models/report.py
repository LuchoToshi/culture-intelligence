from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from culture.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WeeklyReport(Base):
    """A generated weekly intelligence report, stored in the database (not
    just as a local file) so the deployed, stateless web app can read it —
    the pipeline that generates reports runs locally; the public/private
    web views run on Vercel with no access to the local filesystem.

    is_public gates the public homepage/brief page: reports default to
    private, since publishing report content to the open internet is an
    explicit operator decision, not something a generation run should do
    on its own.
    """

    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    iso_week: Mapped[str] = mapped_column(String(10), unique=True)  # e.g. "2026-W35"
    content_markdown: Mapped[str] = mapped_column(Text)
    sources_covered: Mapped[int] = mapped_column(default=0)
    items_covered: Mapped[int] = mapped_column(default=0)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<WeeklyReport {self.iso_week} public={self.is_public}>"
