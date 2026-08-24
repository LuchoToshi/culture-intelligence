from pydantic import BaseModel, Field

from culture.models.analysis import LifecycleStage

Score = int | None  # 1-5, or null when the item cannot support a judgment


class ItemScores(BaseModel):
    cultural_origin: Score = Field(None, ge=1, le=5)
    editorial_momentum: Score = Field(None, ge=1, le=5)
    urban_adoption: Score = Field(None, ge=1, le=5)
    meme_recognition: Score = Field(None, ge=1, le=5)
    creator_adoption: Score = Field(None, ge=1, le=5)
    commercial_evidence: Score = Field(None, ge=1, le=5)
    saturation_risk: Score = Field(None, ge=1, le=5)
    longevity: Score = Field(None, ge=1, le=5)


class ItemAnalysisResponse(BaseModel):
    """Structured output the model must return for one content item."""

    summary: str
    major_points: list[str] = Field(default_factory=list)

    people: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    designers: list[str] = Field(default_factory=list)
    artists: list[str] = Field(default_factory=list)
    musicians: list[str] = Field(default_factory=list)
    creators: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    neighborhoods: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list)
    subcultures: list[str] = Field(default_factory=list)
    sports: list[str] = Field(default_factory=list)
    garments: list[str] = Field(default_factory=list)
    footwear: list[str] = Field(default_factory=list)
    lifestyle_objects: list[str] = Field(default_factory=list)
    restaurants: list[str] = Field(default_factory=list)
    cafes: list[str] = Field(default_factory=list)
    clubs: list[str] = Field(default_factory=list)
    media_references: list[str] = Field(default_factory=list)
    historical_references: list[str] = Field(default_factory=list)

    topics: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    consumer_archetypes: list[str] = Field(default_factory=list)
    possible_signals: list[str] = Field(default_factory=list)

    why_it_matters: str | None = None

    # What the source itself says vs. what we infer from it.
    facts: list[str] = Field(default_factory=list)
    interpretations: list[str] = Field(default_factory=list)

    scores: ItemScores = Field(default_factory=ItemScores)
    lifecycle_stage: LifecycleStage | None = None
