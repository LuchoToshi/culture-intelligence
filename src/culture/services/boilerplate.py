"""Cross-document boilerplate detection.

Extracted article text often carries site furniture the extractor could not
distinguish from content: newsletter upsells, paywall sign-in prompts,
affiliate disclaimers. These repeat verbatim across a source's articles, so we
detect paragraphs that appear in a meaningful fraction of a source's items.

raw_text in the database is never modified — cleaning is applied at
consumption time (analysis, reporting), which also means detection quality
improves as the corpus grows.
"""

from collections import Counter
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.models.content import ContentItem

# A paragraph is boilerplate when it appears in >= MIN_ITEMS items AND
# >= MIN_FRACTION of the source's items with text. Exact match only.
MIN_ITEMS = 3
MIN_FRACTION = 0.25
# Very short repeated lines (names, section labels) are left alone.
MIN_PARAGRAPH_LENGTH = 30


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n") if p.strip()]


def find_boilerplate(bodies: Iterable[str]) -> set[str]:
    bodies = [b for b in bodies if b and b.strip()]
    if len(bodies) < MIN_ITEMS:
        return set()
    counts: Counter[str] = Counter()
    for body in bodies:
        # count each paragraph once per document
        for paragraph in set(split_paragraphs(body)):
            if len(paragraph) >= MIN_PARAGRAPH_LENGTH:
                counts[paragraph] += 1
    threshold = max(MIN_ITEMS, int(len(bodies) * MIN_FRACTION))
    return {p for p, n in counts.items() if n >= threshold}


def clean_text(text: str, boilerplate: set[str]) -> str:
    if not boilerplate:
        return text
    return "\n".join(p for p in split_paragraphs(text) if p not in boilerplate)


def boilerplate_for_source(session: Session, source_id: int) -> set[str]:
    bodies = session.scalars(
        select(ContentItem.raw_text).where(
            ContentItem.source_id == source_id, ContentItem.raw_text.is_not(None)
        )
    )
    return find_boilerplate(bodies)


def cleaned_text_for(session: Session, item: ContentItem) -> str | None:
    """The text analysis should consume: extracted text minus source boilerplate."""
    if item.raw_text is None:
        return None
    return clean_text(item.raw_text, boilerplate_for_source(session, item.source_id))
