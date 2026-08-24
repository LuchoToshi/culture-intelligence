from datetime import datetime

from pydantic import BaseModel, Field

from culture.models.content import ContentType


class RawContentItem(BaseModel):
    """Normalized intermediate item every collector produces.

    The ingestion pipeline only ever sees this shape, never platform-specific
    feed entries.
    """

    external_id: str | None = None
    url: str
    title: str | None = None
    author: str | None = None
    description: str | None = None
    published_at: datetime | None = None
    content_type: ContentType
    metadata: dict = Field(default_factory=dict)
