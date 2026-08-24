from typing import Protocol

from culture.models.source import Source
from culture.schemas.collector import RawContentItem


class CollectorError(Exception):
    """A source-level collection failure (bad feed, network exhaustion, ...)."""


class Collector(Protocol):
    def fetch(self, source: Source) -> list[RawContentItem]:
        """Return all currently visible items for a source.

        Raises CollectorError when the source as a whole cannot be read.
        """
        ...
