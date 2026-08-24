from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from culture.models.content import ContentItem
from culture.models.source import Source
from culture.schemas.source import SeedSource

# Fields owned by the seed file. Operational fields (last_checked_at, ...) are
# never touched by a seed import.
_SEED_FIELDS = (
    "platform",
    "source_type",
    "url",
    "feed_url",
    "external_identifier",
    "region",
    "country",
    "city",
    "language",
    "categories",
    "intelligence_layers",
    "tier",
    "active",
    "collection_method",
)


class SourceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_name(self, name: str) -> Source | None:
        return self.session.scalar(select(Source).where(Source.name == name))

    def list_all(self) -> list[Source]:
        return list(self.session.scalars(select(Source).order_by(Source.name)))

    def list_with_latest_item(self) -> list[tuple[Source, datetime | None]]:
        latest = (
            select(
                ContentItem.source_id,
                func.max(ContentItem.published_at).label("latest_item"),
            )
            .group_by(ContentItem.source_id)
            .subquery()
        )
        rows = self.session.execute(
            select(Source, latest.c.latest_item)
            .outerjoin(latest, Source.id == latest.c.source_id)
            .order_by(Source.name)
        )
        return [(row[0], row[1]) for row in rows]

    def upsert_seed(self, seed: SeedSource) -> tuple[Source, str]:
        """Insert or update a source from a seed record.

        Returns the source and one of: "created", "updated", "unchanged".
        """
        values = seed.model_dump(mode="json")
        values["collection_notes"] = values.pop("notes")

        source = self.get_by_name(seed.name)
        if source is None:
            source = Source(name=seed.name, **{f: values[f] for f in _SEED_FIELDS})
            source.collection_notes = values["collection_notes"]
            self.session.add(source)
            self.session.flush()
            return source, "created"

        changed = False
        for field in (*_SEED_FIELDS, "collection_notes"):
            if getattr(source, field) != values[field]:
                setattr(source, field, values[field])
                changed = True
        if changed:
            self.session.flush()
            return source, "updated"
        return source, "unchanged"
