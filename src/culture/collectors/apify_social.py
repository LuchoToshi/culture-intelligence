"""Instagram / TikTok collection via Apify managed actors.

Instagram and TikTok have no official API for monitoring arbitrary public
accounts. This collector uses Apify's maintained actors — no login, no stored
credentials — to pull recent public posts from the platform accounts already
in our registry. It is a deliberate, operator-authorized revision of the
original no-unofficial-scraping stance (see the run's decision record):
targeted (our ~20 accounts only), low-volume, storing derived intelligence
rather than republishing content.

The actor call goes through an injected `run_actor` callable so tests never
touch the network. The default runner hits Apify's REST run-sync endpoint
using APIFY_TOKEN.
"""

from collections.abc import Callable
from datetime import datetime

import httpx

from culture.collectors.base import CollectorError
from culture.logging import get_logger
from culture.models.content import ContentType
from culture.models.source import Source
from culture.schemas.collector import RawContentItem
from culture.utils.dates import from_iso_date

log = get_logger("culture.collectors.apify")

# actor -> (post-limit input key, extra fixed input)
INSTAGRAM_ACTOR = "apify/instagram-post-scraper"
TIKTOK_ACTOR = "clockworks/tiktok-scraper"

ActorRunner = Callable[[str, dict], list[dict]]


def _handle(source: Source) -> str | None:
    ident = (source.external_identifier or "").strip().lstrip("@")
    if ident:
        return ident
    url = (source.url or "").rstrip("/")
    if not url:
        return None
    tail = url.split("/")[-1]
    return tail.lstrip("@") or None


def default_runner(token: str, timeout: float = 300.0) -> ActorRunner:
    """Apify REST run-sync-get-dataset-items runner."""

    def run(actor: str, actor_input: dict) -> list[dict]:
        path = actor.replace("/", "~")
        url = f"https://api.apify.com/v2/acts/{path}/run-sync-get-dataset-items"
        try:
            response = httpx.post(
                url, params={"token": token}, json=actor_input, timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            raise CollectorError(f"Apify actor {actor} failed: {exc}") from exc

    return run


class ApifySocialCollector:
    """Collector for instagram/tiktok sources. Returns POST RawContentItems.

    Image bytes are NOT fetched here — the ingestion POST branch downloads the
    display image only after dedup confirms the post is new, so re-runs never
    re-download known posts.
    """

    def __init__(
        self,
        run_actor: ActorRunner,
        posts_per_account: int = 10,
        newer_than_days: int = 30,
    ) -> None:
        self.run_actor = run_actor
        self.posts_per_account = posts_per_account
        self.newer_than_days = newer_than_days

    def fetch(self, source: Source) -> list[RawContentItem]:
        handle = _handle(source)
        if handle is None:
            raise CollectorError(f"{source.name!r} has no resolvable account handle")
        if source.platform == "instagram":
            return self._fetch_instagram(handle)
        if source.platform == "tiktok":
            return self._fetch_tiktok(handle)
        raise CollectorError(f"ApifySocialCollector cannot handle platform {source.platform!r}")

    def _fetch_instagram(self, handle: str) -> list[RawContentItem]:
        rows = self.run_actor(
            INSTAGRAM_ACTOR,
            {
                "username": [handle],
                "resultsLimit": self.posts_per_account,
                "onlyPostsNewerThan": f"{self.newer_than_days} days",
                "dataDetailLevel": "basicData",
            },
        )
        items = []
        for row in rows:
            url = row.get("url")
            if not url:
                continue
            caption = (row.get("caption") or "").strip()
            items.append(
                RawContentItem(
                    external_id=row.get("shortCode") or None,
                    url=url,
                    title=caption.split("\n")[0][:120] or f"Instagram post by {handle}",
                    author=row.get("ownerUsername") or handle,
                    description=caption or None,
                    published_at=_ts(row.get("timestamp")),
                    content_type=ContentType.POST,
                    metadata={
                        "platform": "instagram",
                        "image_url": row.get("displayUrl"),
                        "media_kind": row.get("type"),
                        "likes": row.get("likesCount"),
                        "comments": row.get("commentsCount"),
                    },
                )
            )
        return items

    def _fetch_tiktok(self, handle: str) -> list[RawContentItem]:
        rows = self.run_actor(
            TIKTOK_ACTOR,
            {
                "profiles": [handle],
                "resultsPerPage": self.posts_per_account,
                "profileScrapeSections": ["videos"],
                "profileSorting": "latest",
                "excludePinnedPosts": True,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            },
        )
        items = []
        for row in rows:
            url = row.get("webVideoUrl") or row.get("postPage") or row.get("url")
            if not url:
                continue
            caption = (row.get("text") or "").strip()
            cover = row.get("videoMeta", {}).get("coverUrl") if isinstance(
                row.get("videoMeta"), dict
            ) else None
            items.append(
                RawContentItem(
                    external_id=str(row.get("id")) if row.get("id") else None,
                    url=url,
                    title=caption.split("\n")[0][:120] or f"TikTok by {handle}",
                    author=handle,
                    description=caption or None,
                    published_at=_ts(row.get("createTimeISO") or row.get("createTime")),
                    content_type=ContentType.POST,
                    metadata={
                        "platform": "tiktok",
                        "image_url": cover,
                        "likes": row.get("diggCount"),
                        "comments": row.get("commentCount"),
                        "views": row.get("playCount"),
                    },
                )
            )
        return items


def _ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        from datetime import UTC

        return datetime.fromtimestamp(value, tz=UTC)
    return from_iso_date(str(value))
