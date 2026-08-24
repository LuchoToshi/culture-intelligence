from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.models.content import ContentItem


class ContentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_duplicate(
        self, source_id: int, external_id: str | None, urls: list[str]
    ) -> ContentItem | None:
        """Find an existing item this (source, external_id, urls) would duplicate."""
        if external_id:
            existing = self.session.scalar(
                select(ContentItem).where(
                    ContentItem.source_id == source_id,
                    ContentItem.external_id == external_id,
                )
            )
            if existing:
                return existing
        candidates = [u for u in urls if u]
        if not candidates:
            return None
        return self.session.scalar(
            select(ContentItem)
            .where(
                ContentItem.source_id == source_id,
                ContentItem.url.in_(candidates)
                | ContentItem.canonical_url.in_(candidates),
            )
            .limit(1)
        )

    def add(self, item: ContentItem) -> ContentItem:
        self.session.add(item)
        self.session.flush()
        return item
