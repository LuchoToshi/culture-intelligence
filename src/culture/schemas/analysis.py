from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from culture.models.analysis import LifecycleStage

Score = int | None  # 1-5, or null when the item cannot support a judgment


def _all_fields_required(schema: dict[str, Any]) -> None:
    """Mark every property as required in the generated JSON schema.

    The Anthropic structured-outputs API rejects schemas with more than 24
    optional parameters; with Python-side defaults every field here would be
    optional (31 of them). Requiring everything also forces the model to emit
    explicit empty lists and explicit nulls instead of silently omitting
    fields. Python-side defaults are unaffected.
    """
    schema["required"] = sorted(schema.get("properties", {}))


class ItemScores(BaseModel):
    model_config = ConfigDict(json_schema_extra=_all_fields_required)

    cultural_origin: Score = Field(None, ge=1, le=5)
    editorial_momentum: Score = Field(None, ge=1, le=5)
    urban_adoption: Score = Field(None, ge=1, le=5)
    meme_recognition: Score = Field(None, ge=1, le=5)
    creator_adoption: Score = Field(None, ge=1, le=5)
    commercial_evidence: Score = Field(None, ge=1, le=5)
    saturation_risk: Score = Field(None, ge=1, le=5)
    longevity: Score = Field(None, ge=1, le=5)


class EntityType(StrEnum):
    """One value per entity column on ContentAnalysis."""

    PEOPLE = "people"
    BRANDS = "brands"
    PRODUCTS = "products"
    DESIGNERS = "designers"
    ARTISTS = "artists"
    MUSICIANS = "musicians"
    CREATORS = "creators"
    CITIES = "cities"
    NEIGHBORHOODS = "neighborhoods"
    COUNTRIES = "countries"
    SCENES = "scenes"
    SUBCULTURES = "subcultures"
    SPORTS = "sports"
    GARMENTS = "garments"
    FOOTWEAR = "footwear"
    LIFESTYLE_OBJECTS = "lifestyle_objects"
    RESTAURANTS = "restaurants"
    CAFES = "cafes"
    CLUBS = "clubs"
    MEDIA_REFERENCES = "media_references"
    HISTORICAL_REFERENCES = "historical_references"


class ExtractedEntity(BaseModel):
    model_config = ConfigDict(json_schema_extra=_all_fields_required)

    type: EntityType
    name: str


class ItemAnalysisResponse(BaseModel):
    """Structured output the model must return for one content item.

    Entities travel as one typed array rather than 21 parallel list fields:
    Anthropic's constrained-decoding grammar rejects schemas that wide
    ("compiled grammar is too large"). The analyzer fans entities back out
    into the per-type ContentAnalysis columns, which are unchanged.
    """

    model_config = ConfigDict(json_schema_extra=_all_fields_required)

    summary: str
    major_points: list[str] = Field(default_factory=list)

    entities: list[ExtractedEntity] = Field(default_factory=list)

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

    def entity_names(self, entity_type: EntityType) -> list[str]:
        """Names of one entity type, order preserved, exact duplicates dropped."""
        seen: set[str] = set()
        names: list[str] = []
        for entity in self.entities:
            if entity.type == entity_type and entity.name not in seen:
                seen.add(entity.name)
                names.append(entity.name)
        return names
