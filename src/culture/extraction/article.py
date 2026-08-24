import json
import re
from dataclasses import dataclass
from datetime import datetime

import trafilatura

from culture.models.content import ExtractionStatus
from culture.utils.dates import from_iso_date

# Below this many characters we treat an extraction as partial: probably a
# teaser, a paywall stub, or boilerplate rather than the article body.
PARTIAL_THRESHOLD = 400

_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']|'
    r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    re.IGNORECASE,
)


@dataclass
class ExtractionResult:
    status: ExtractionStatus
    text: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    canonical_url: str | None = None
    error: str | None = None


def find_canonical_url(html: str) -> str | None:
    match = _CANONICAL_RE.search(html)
    if match:
        return match.group(1) or match.group(2)
    return None


def extract_article(html: str, url: str) -> ExtractionResult:
    """Extract readable article text and metadata from HTML. Never raises."""
    canonical = find_canonical_url(html)
    try:
        raw = trafilatura.extract(html, url=url, output_format="json", with_metadata=True)
    except Exception as exc:  # trafilatura can choke on exotic markup
        return ExtractionResult(
            status=ExtractionStatus.FAILED, canonical_url=canonical, error=str(exc)
        )
    if not raw:
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            canonical_url=canonical,
            error="no extractable content",
        )

    data = json.loads(raw)
    text = (data.get("text") or "").strip() or None
    if text is None:
        status = ExtractionStatus.FAILED
    elif len(text) < PARTIAL_THRESHOLD:
        status = ExtractionStatus.PARTIAL
    else:
        status = ExtractionStatus.SUCCESS

    return ExtractionResult(
        status=status,
        text=text,
        title=data.get("title") or None,
        author=data.get("author") or None,
        published_at=from_iso_date(data.get("date")),
        canonical_url=canonical or data.get("source") or None,
        error=None if text else "no extractable content",
    )
