from pydantic import BaseModel, Field, field_validator

from culture.models.source import Platform, SourceTier


class SeedSource(BaseModel):
    """One record from seeds/sources.yaml, validated before it touches the database."""

    name: str = Field(min_length=1, max_length=200)
    platform: Platform
    source_type: str | None = None
    url: str | None = None
    feed_url: str | None = None
    external_identifier: str | None = None
    region: str | None = None
    country: str | None = None
    city: str | None = None
    language: str | None = None
    categories: list[str] = Field(default_factory=list)
    intelligence_layers: list[str] = Field(default_factory=list)
    tier: SourceTier = SourceTier.CANDIDATE
    active: bool = True
    collection_method: str | None = None
    notes: str | None = None

    @field_validator("url", "feed_url")
    @classmethod
    def url_must_be_http(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://")):
            raise ValueError(f"must be an http(s) URL, got {value!r}")
        return value
