from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from culture.database import Base, JSONField
from culture.models.content import ContentItem


class LifecycleStage(StrEnum):
    UNKNOWN = "unknown"
    EMERGING = "emerging"
    STRENGTHENING = "strengthening"
    MAINSTREAM = "mainstream"
    SATURATED = "saturated"
    DECLINING = "declining"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ContentAnalysis(Base):
    __tablename__ = "content_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE is safe here: analysis is derived data, unlike collected content.
    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )

    summary: Mapped[str | None] = mapped_column(Text)
    major_points: Mapped[list] = mapped_column(JSONField, default=list)

    people: Mapped[list] = mapped_column(JSONField, default=list)
    brands: Mapped[list] = mapped_column(JSONField, default=list)
    products: Mapped[list] = mapped_column(JSONField, default=list)
    designers: Mapped[list] = mapped_column(JSONField, default=list)
    artists: Mapped[list] = mapped_column(JSONField, default=list)
    musicians: Mapped[list] = mapped_column(JSONField, default=list)
    creators: Mapped[list] = mapped_column(JSONField, default=list)
    cities: Mapped[list] = mapped_column(JSONField, default=list)
    neighborhoods: Mapped[list] = mapped_column(JSONField, default=list)
    countries: Mapped[list] = mapped_column(JSONField, default=list)
    scenes: Mapped[list] = mapped_column(JSONField, default=list)
    subcultures: Mapped[list] = mapped_column(JSONField, default=list)
    sports: Mapped[list] = mapped_column(JSONField, default=list)
    garments: Mapped[list] = mapped_column(JSONField, default=list)
    footwear: Mapped[list] = mapped_column(JSONField, default=list)
    lifestyle_objects: Mapped[list] = mapped_column(JSONField, default=list)
    restaurants: Mapped[list] = mapped_column(JSONField, default=list)
    cafes: Mapped[list] = mapped_column(JSONField, default=list)
    clubs: Mapped[list] = mapped_column(JSONField, default=list)
    media_references: Mapped[list] = mapped_column(JSONField, default=list)
    historical_references: Mapped[list] = mapped_column(JSONField, default=list)
    topics: Mapped[list] = mapped_column(JSONField, default=list)
    tags: Mapped[list] = mapped_column(JSONField, default=list)
    consumer_archetypes: Mapped[list] = mapped_column(JSONField, default=list)
    possible_signals: Mapped[list] = mapped_column(JSONField, default=list)

    why_it_matters: Mapped[str | None] = mapped_column(Text)

    # Provisional 1-5 scores. Null means the item did not support a judgment.
    cultural_origin_score: Mapped[int | None] = mapped_column(SmallInteger)
    editorial_momentum_score: Mapped[int | None] = mapped_column(SmallInteger)
    urban_adoption_score: Mapped[int | None] = mapped_column(SmallInteger)
    meme_recognition_score: Mapped[int | None] = mapped_column(SmallInteger)
    creator_adoption_score: Mapped[int | None] = mapped_column(SmallInteger)
    commercial_evidence_score: Mapped[int | None] = mapped_column(SmallInteger)
    saturation_risk_score: Mapped[int | None] = mapped_column(SmallInteger)
    longevity_score: Mapped[int | None] = mapped_column(SmallInteger)

    lifecycle_stage: Mapped[str | None] = mapped_column(String(30))

    # What the source said vs. what our system inferred. Keep them apart.
    facts_json: Mapped[dict] = mapped_column(JSONField, default=dict)
    interpretations_json: Mapped[dict] = mapped_column(JSONField, default=dict)

    analysis_model: Mapped[str | None] = mapped_column(String(100))
    analysis_provider: Mapped[str | None] = mapped_column(String(50))
    analysis_version: Mapped[str | None] = mapped_column(String(50))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    content_item: Mapped[ContentItem] = relationship()

    def __repr__(self) -> str:
        return f"<ContentAnalysis {self.id} content_item_id={self.content_item_id}>"
