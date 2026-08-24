from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from culture.database import Base, JSONField
from culture.models.content import ContentItem


class SignalState(StrEnum):
    ACTIVE = "active"
    MERGED = "merged"
    RETIRED = "retired"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Signal(Base):
    """A persistent cultural signal accumulating evidence across sources and weeks.

    This is the core asset of the intelligence layer: items come and go weekly,
    signals persist and build history.
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(300), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default=SignalState.ACTIVE.value)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"))

    lifecycle_stage: Mapped[str] = mapped_column(String(30), default="unknown")
    first_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    cities: Mapped[list] = mapped_column(JSONField, default=list)
    countries: Mapped[list] = mapped_column(JSONField, default=list)
    categories: Mapped[list] = mapped_column(JSONField, default=list)

    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)

    # Aggregated from evidence-item analyses (avg of non-null scores).
    cultural_origin_score: Mapped[int | None] = mapped_column(SmallInteger)
    editorial_momentum_score: Mapped[int | None] = mapped_column(SmallInteger)
    urban_adoption_score: Mapped[int | None] = mapped_column(SmallInteger)
    meme_recognition_score: Mapped[int | None] = mapped_column(SmallInteger)
    creator_adoption_score: Mapped[int | None] = mapped_column(SmallInteger)
    commercial_evidence_score: Mapped[int | None] = mapped_column(SmallInteger)
    saturation_risk_score: Mapped[int | None] = mapped_column(SmallInteger)
    longevity_score: Mapped[int | None] = mapped_column(SmallInteger)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<Signal {self.id} {self.name!r} stage={self.lifecycle_stage} evidence={self.evidence_count}>"


class SignalEvidence(Base):
    __tablename__ = "signal_evidence"
    __table_args__ = (
        UniqueConstraint("signal_id", "content_item_id", name="uq_signal_evidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    signal: Mapped[Signal] = relationship()
    content_item: Mapped[ContentItem] = relationship()
