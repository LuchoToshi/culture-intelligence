"""Manual multimodal intake for platforms with no automated collector.

The compliant path for Instagram/TikTok evidence: a human saves the post
(URL, caption, screenshot), the system stores it as a first-class content
item and the analyzer interprets the image with vision. Human-in-the-loop on
capture, automated on understanding.

Images live under media/items/ (gitignored, local to the pipeline machine).
The stored analysis is the durable intelligence; the image file is working
material with its sha256 recorded for integrity.
"""

import hashlib
import io
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.logging import get_logger
from culture.models.content import (
    ContentItem,
    ContentType,
    ExtractionStatus,
    ProcessingStatus,
    TranscriptStatus,
)
from culture.models.source import Source
from culture.repositories.content import ContentRepository
from culture.utils.urls import normalize_url

log = get_logger("culture.intake")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MEDIA_DIR = PROJECT_ROOT / "media" / "items"

IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# Vision cost scales with pixel count (~width*height/750 tokens), not file
# size. Instagram/TikTok images arrive at ~1080px — no meme's on-image text
# or clothing detail needs that; 768px on the long edge stays fully legible
# (cost-relevant test: "is it readable", not "is it high-resolution") while
# cutting image tokens by roughly half.
MAX_IMAGE_DIMENSION = 768
IMAGE_JPEG_QUALITY = 85


def downscale_image(data: bytes, media_type: str) -> tuple[bytes, str]:
    """Resize to MAX_IMAGE_DIMENSION on the long edge; re-encode as JPEG.

    Returns (possibly unchanged) bytes and the resulting media type. Images
    already at or under the target size pass through untouched — no point
    re-encoding (and possibly degrading) an already-small image.
    """
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:
        log.warning("could not decode image for downscaling, storing as-is: %s", exc)
        return data, media_type

    if max(image.size) <= MAX_IMAGE_DIMENSION:
        return data, media_type

    rgb_image = image if image.mode in ("RGB", "L") else image.convert("RGB")
    rgb_image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    rgb_image.save(buffer, format="JPEG", quality=IMAGE_JPEG_QUALITY)
    return buffer.getvalue(), "image/jpeg"


class IntakeError(Exception):
    """Actionable intake failure — message tells the operator what to do."""


def _handle_from_url(url: str) -> str | None:
    """Extract the account handle from a social post URL, if recognizable."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None
    if host == "instagram.com" and segments[0] not in ("p", "reel", "reels", "stories"):
        return segments[0].lower()
    if host == "instagram.com":
        return None  # bare post URL carries no handle
    if host == "tiktok.com" and segments[0].startswith("@"):
        return segments[0].lstrip("@").lower()
    return None


def infer_source(session: Session, url: str) -> Source | None:
    handle = _handle_from_url(url)
    if handle is None:
        return None
    for source in session.scalars(
        select(Source).where(Source.platform.in_(["instagram", "tiktok"]))
    ):
        source_handle = _handle_from_url(source.url or "") or (
            (source.external_identifier or "").lstrip("@").lower() or None
        )
        if source_handle == handle:
            return source
    return None


def store_image_bytes(data: bytes, media_type: str, stem: str) -> dict:
    """Downscale, then persist image bytes into the media dir. Returns an
    images-list entry.

    Shared by manual intake and the social collector so both produce the
    identical metadata shape the analyzer reads via load_item_images.
    """
    if media_type not in IMAGE_MEDIA_TYPES.values():
        raise IntakeError(f"Unsupported image media type {media_type!r}.")
    data, media_type = downscale_image(data, media_type)
    suffix = next(s for s, mt in IMAGE_MEDIA_TYPES.items() if mt == media_type)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    destination = MEDIA_DIR / f"{stem}{suffix}"
    destination.write_bytes(data)
    return {"path": str(destination), "media_type": media_type, "sha256": digest}


def _store_image(image_path: Path, item_id: int) -> tuple[str, str, str]:
    """Copy a local image into the media dir. Returns (stored_path, media_type, sha256)."""
    suffix = image_path.suffix.lower()
    media_type = IMAGE_MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise IntakeError(
            f"Unsupported image type {suffix!r}. Use one of: "
            + ", ".join(sorted(IMAGE_MEDIA_TYPES))
        )
    entry = store_image_bytes(image_path.read_bytes(), media_type, str(item_id))
    return entry["path"], entry["media_type"], entry["sha256"]


def load_item_images(item: ContentItem) -> list[tuple[str, bytes]]:
    """(media_type, bytes) for the analyzer; missing files are skipped loudly."""
    images = []
    for entry in item.metadata_json.get("images", []):
        path = Path(entry["path"])
        if not path.exists():
            log.warning("image file missing for item %d: %s", item.id, path)
            continue
        images.append((entry["media_type"], path.read_bytes()))
    return images


def add_post(
    session: Session,
    url: str,
    caption: str | None = None,
    image_path: Path | None = None,
    source_name: str | None = None,
    published=None,
) -> ContentItem:
    if not caption and image_path is None:
        raise IntakeError("Provide --caption, --image, or both — an empty post is no evidence.")

    if source_name:
        source = session.scalar(select(Source).where(Source.name == source_name))
        if source is None:
            raise IntakeError(
                f"No source named {source_name!r}. Check `culture sources`, or add it to "
                "seeds/sources.yaml first — posts always belong to a registered source."
            )
    else:
        source = infer_source(session, url)
        if source is None:
            raise IntakeError(
                "Could not match this URL to a registered account. "
                'Pass --source "<name>" explicitly (see `culture sources`).'
            )

    normalized = normalize_url(url)
    repo = ContentRepository(session)
    if repo.find_duplicate(source.id, None, [normalized]):
        raise IntakeError(f"Already stored: {normalized}")

    if image_path is not None and not image_path.exists():
        raise IntakeError(f"Image file not found: {image_path}")

    item = ContentItem(
        source_id=source.id,
        url=normalized,
        title=(caption or "").strip().split("\n")[0][:120] or f"Post by {source.name}",
        description=caption,
        content_type=ContentType.POST.value,
        published_at=published,
        extraction_status=ExtractionStatus.NOT_ATTEMPTED.value,
        transcript_status=TranscriptStatus.NOT_APPLICABLE.value,
        processing_status=ProcessingStatus.READY.value,
        metadata_json={"original_url": url, "manual_intake": True},
    )
    repo.add(item)
    session.flush()

    if image_path is not None:
        stored, media_type, digest = _store_image(image_path, item.id)
        item.metadata_json = {
            **item.metadata_json,
            "images": [{"path": stored, "media_type": media_type, "sha256": digest}],
        }

    session.commit()
    log.info("manual post stored: [%s] %s (image=%s)", source.name, normalized, bool(image_path))
    return item
