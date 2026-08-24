import hashlib
import re


def content_hash(title: str | None, text: str | None) -> str | None:
    """Deterministic hash of extracted content, tolerant of whitespace noise.

    Returns None when there is no body text — a title alone is not enough to
    call two items identical.
    """
    if not text or not text.strip():
        return None
    normalized = re.sub(r"\s+", " ", f"{title or ''}\n{text}").strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
