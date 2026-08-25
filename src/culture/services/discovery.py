"""Source discovery: mine collected evidence for new candidate sources.

Two signals, both computed from data the pipeline already stores — no LLM
calls, no network:

1. Entities: analyses extract `creators` and `media_references` — names that
   publish content. (People, brands and designers are deliberately excluded:
   they are signal entities, not monitorable sources.)
2. Outbound links: URLs in extracted text and descriptions (podcast show
   notes are especially rich) pointing at platform profiles or unknown
   publication domains.

A name or link becomes a candidate Source row only when cited by at least
MIN_CITING_SOURCES independent existing sources — one mention is noise,
independent repetition is attention. Candidates are created inactive at tier
`candidate`; nothing is collected from them until promotion (Phase B) or a
human activates them. Existing sources are never modified.

Re-running is idempotent: stats are recomputed from scratch each run and
rewritten onto discovery-owned candidates; counts never double.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.logging import get_logger
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.source import Source, SourceTier
from culture.utils.dates import ensure_utc, now_utc

log = get_logger("culture.discovery")

MIN_CITING_SOURCES = 2
# media_references mixes real outlets with films, events and cultural
# references; demand broader independent citation before trusting a name
# from that column as a source.
MIN_CITING_SOURCES_MEDIA_REF = 3

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.IGNORECASE)

# Profile-URL shapes that identify a creator account on a platform.
_PLATFORM_PROFILES = [
    ("instagram", re.compile(r"^https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]{2,30})/?$")),
    ("tiktok", re.compile(r"^https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9_.]{2,30})/?$")),
    ("youtube", re.compile(r"^https?://(?:www\.)?youtube\.com/(@[A-Za-z0-9_.-]{2,40})/?$")),
    ("x", re.compile(r"^https?://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]{2,20})/?$")),
    ("substack", re.compile(r"^https?://([a-z0-9-]{2,60})\.substack\.com(?:/.*)?$")),
]

# Domains that are infrastructure, retail, or content hosts — never sources.
_DOMAIN_BLOCKLIST = {
    "google.com", "apple.com", "amazon.com", "spotify.com", "open.spotify.com",
    "wikipedia.org", "en.wikipedia.org", "archive.org", "imgur.com", "bit.ly",
    "linktr.ee", "youtu.be", "youtube.com", "instagram.com", "tiktok.com",
    "twitter.com", "x.com", "facebook.com", "reddit.com", "substack.com",
    "patreon.com", "shopify.com", "vercel.app", "podcasts.apple.com",
    "music.apple.com", "soundcloud.com", "discord.gg", "discord.com",
    "mailchimp.com", "eventbrite.com", "paypal.com", "gmail.com",
    # URL shorteners — destination unknown, never a source identity.
    "amzn.to", "t.co", "ow.ly", "buff.ly", "tinyurl.com", "goo.gl", "shorturl.at",
    "geni.us", "smarturl.it", "lnk.to",
}

# Entity names that are platforms or generic media, not monitorable sources.
_ENTITY_BLOCKLIST = {
    "reddit", "tiktok", "instagram", "youtube", "twitter", "x", "facebook",
    "spotify", "netflix", "twitch", "discord", "tumblr", "pinterest", "substack",
}


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _domain(url: str) -> str | None:
    try:
        host = urlsplit(url).netloc.lower().split(":")[0]
    except ValueError:
        return None
    return host.removeprefix("www.") or None


@dataclass
class Candidate:
    name: str
    platform: str
    url: str | None
    via: str  # "entity" | "link"
    mention_count: int = 0
    item_ids: set[int] = field(default_factory=set)
    citing_source_ids: set[int] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    def record(self, item: ContentItem, when: datetime | None) -> None:
        self.mention_count += 1
        self.item_ids.add(item.id)
        self.citing_source_ids.add(item.source_id)
        if when:
            if self.first_seen is None or when < self.first_seen:
                self.first_seen = when
            if self.last_seen is None or when > self.last_seen:
                self.last_seen = when


@dataclass
class DiscoveryStats:
    created: list[str] = field(default_factory=list)
    updated: int = 0
    below_threshold: int = 0
    skipped_known: int = 0


def _known_keys(session: Session) -> tuple[set[str], set[str]]:
    """Normalized names and domains of human-curated sources.

    Discovery-owned candidates are excluded on purpose: they must keep
    matching themselves on re-runs so their stats get refreshed instead of
    being skipped as "already known".
    """
    names: set[str] = set()
    domains: set[str] = set()
    for source in session.scalars(select(Source)):
        if source.discovery_json.get("via"):
            continue
        names.add(_normalize_name(source.name))
        for url in (source.url, source.feed_url):
            if url and (dom := _domain(url)):
                domains.add(dom)
    return names, domains


def _classify_link(url: str) -> tuple[str, str, str | None] | None:
    """Return (candidate_key_name, platform, profile_url) for a source-shaped link."""
    url = url.rstrip(".,;:!?")
    for platform, pattern in _PLATFORM_PROFILES:
        match = pattern.match(url)
        if match:
            handle = match.group(1).lstrip("@")
            if platform == "substack":
                return handle, "substack", f"https://{handle}.substack.com"
            return handle, platform, url.split("?")[0]
    dom = _domain(url)
    if dom is None or dom in _DOMAIN_BLOCKLIST or "." not in dom:
        return None
    return dom, "web", f"https://{dom}"


def _collect_candidates(session: Session) -> dict[str, Candidate]:
    items = {i.id: i for i in session.scalars(select(ContentItem))}
    candidates: dict[str, Candidate] = {}

    def bump(key: str, template: Candidate, item: ContentItem) -> None:
        candidate = candidates.setdefault(key, template)
        candidate.record(item, ensure_utc(item.published_at or item.discovered_at))

    # 1. Entity mentions from analyses.
    for analysis in session.scalars(select(ContentAnalysis)):
        item = items.get(analysis.content_item_id)
        if item is None:
            continue
        entity_pools = [
            (analysis.creators or [], "entity"),
            (analysis.media_references or [], "media_ref"),
        ]
        for pool, via in entity_pools:
            for name in pool:
                name = name.strip()
                if len(name) < 3 or _normalize_name(name) in _ENTITY_BLOCKLIST:
                    continue
                key = "entity:" + _normalize_name(name)
                bump(key, Candidate(name=name, platform="other", url=None, via=via), item)

    # 2. Outbound links in text and descriptions.
    for item in items.values():
        own_domain = _domain(item.url) if item.url else None
        text = " ".join(filter(None, [item.raw_text, item.description]))
        for url in _URL_RE.findall(text):
            classified = _classify_link(url)
            if classified is None:
                continue
            name, platform, profile_url = classified
            if platform == "web" and name == own_domain:
                continue  # self-reference
            key = f"link:{platform}:{name.lower()}"
            bump(key, Candidate(name=name, platform=platform, url=profile_url, via="link"), item)

    return candidates


def run_discovery(
    session: Session, min_citing_sources: int = MIN_CITING_SOURCES
) -> DiscoveryStats:
    stats = DiscoveryStats()
    known_names, known_domains = _known_keys(session)
    now = now_utc().isoformat()
    # Prefetch once — per-candidate lookups are an N+1 disaster over a
    # network database.
    all_sources = list(session.scalars(select(Source)))
    by_name = {s.name: s for s in all_sources}
    name_by_id = {s.id: s.name for s in all_sources}

    for candidate in _collect_candidates(session).values():
        if candidate.via == "link" and candidate.platform == "web":
            known = candidate.name in known_domains
        else:
            known = _normalize_name(candidate.name) in known_names
        existing = by_name.get(candidate.name)
        human_owned = existing is not None and not existing.discovery_json.get("via")
        if known or human_owned:
            stats.skipped_known += 1
            continue
        required = (
            MIN_CITING_SOURCES_MEDIA_REF
            if candidate.via == "media_ref"
            else min_citing_sources
        )
        if len(candidate.citing_source_ids) < required:
            stats.below_threshold += 1
            continue

        citing_names = [
            name_by_id[sid] for sid in candidate.citing_source_ids if sid in name_by_id
        ]
        discovery = {
            "via": candidate.via,
            "mentions": candidate.mention_count,
            "items": len(candidate.item_ids),
            "citing_sources": sorted(citing_names)[:10],
            "first_seen": candidate.first_seen.isoformat() if candidate.first_seen else None,
            "last_seen": candidate.last_seen.isoformat() if candidate.last_seen else None,
            "updated_at": now,
        }
        if existing is not None:
            existing.discovery_json = discovery
            stats.updated += 1
            continue

        session.add(
            Source(
                name=candidate.name,
                platform=candidate.platform,
                source_type="discovered",
                url=candidate.url,
                tier=SourceTier.CANDIDATE.value,
                active=False,
                collection_notes=(
                    f"Discovered automatically ({candidate.via}): cited by "
                    f"{len(candidate.citing_source_ids)} independent sources across "
                    f"{len(candidate.item_ids)} items. Verify before activating — "
                    "URL/handle is inferred, not confirmed."
                ),
                discovery_json=discovery,
            )
        )
        stats.created.append(candidate.name)
        log.info(
            "discovered candidate: %s (%s, cited by %d sources)",
            candidate.name,
            candidate.platform,
            len(candidate.citing_source_ids),
        )

    session.commit()
    log.info(
        "discovery completed: %d new, %d updated, %d below threshold, %d already known",
        len(stats.created),
        stats.updated,
        stats.below_threshold,
        stats.skipped_known,
    )
    return stats
