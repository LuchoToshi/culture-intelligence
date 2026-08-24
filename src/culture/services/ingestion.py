import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from culture.collectors.base import Collector, CollectorError
from culture.collectors.rss import RSSCollector
from culture.collectors.youtube import YouTubeCollector
from culture.extraction.article import ExtractionResult, extract_article
from culture.extraction.youtube import (
    TranscriptResult,
    VideoEnrichment,
    enrich_video,
    fetch_transcript,
)
from culture.logging import get_logger
from culture.models.content import (
    ContentItem,
    ContentType,
    ExtractionStatus,
    ProcessingStatus,
    TranscriptStatus,
)
from culture.models.source import Platform, Source
from culture.repositories.content import ContentRepository
from culture.schemas.collector import RawContentItem
from culture.utils.dates import now_utc
from culture.utils.hashing import content_hash
from culture.utils.http import create_client, get_with_retries
from culture.utils.urls import normalize_url

log = get_logger("culture.ingestion")

PageFetcher = Callable[[str], str]
VideoEnricher = Callable[[str, str], VideoEnrichment]

# Pause between article page fetches so a large backlog never hammers a site.
FETCH_DELAY_SECONDS = 0.5
# YouTube bans IPs that fetch transcripts in rapid bursts; space video work out.
VIDEO_DELAY_SECONDS = 4.0
# Stop a transcript retry pass after this many consecutive failures — the IP
# is almost certainly still blocked and hammering only prolongs the ban.
TRANSCRIPT_RETRY_BREAKER = 3


def _polite_fetcher(client: httpx.Client) -> PageFetcher:
    def fetch(url: str) -> str:
        time.sleep(FETCH_DELAY_SECONDS)
        return get_with_retries(client, url).text

    return fetch


def _polite_video_enricher(url: str, video_id: str) -> VideoEnrichment:
    time.sleep(VIDEO_DELAY_SECONDS)
    return enrich_video(url, video_id)


def _polite_transcript_fetcher(video_id: str) -> TranscriptResult:
    time.sleep(VIDEO_DELAY_SECONDS)
    return fetch_transcript(video_id)


@dataclass
class SourceIngestStats:
    source_name: str
    ok: bool = True
    error: str | None = None
    skipped_reason: str | None = None
    new_articles: int = 0
    new_videos: int = 0
    new_podcasts: int = 0
    duplicates: int = 0
    extracted: int = 0
    partial: int = 0
    extraction_failed: int = 0
    transcripts_available: int = 0
    transcripts_unavailable: int = 0
    transcripts_failed: int = 0
    transcripts_recovered: int = 0


@dataclass
class IngestionStats:
    sources: list[SourceIngestStats] = field(default_factory=list)

    def _sum(self, attr: str) -> int:
        return sum(getattr(s, attr) for s in self.sources)

    @property
    def checked(self) -> int:
        return len([s for s in self.sources if not s.skipped_reason])

    @property
    def successful(self) -> int:
        return len([s for s in self.sources if s.ok and not s.skipped_reason])

    @property
    def failed(self) -> list[SourceIngestStats]:
        return [s for s in self.sources if not s.ok]

    @property
    def skipped(self) -> list[SourceIngestStats]:
        return [s for s in self.sources if s.skipped_reason]

    def __getattr__(self, name: str) -> int:
        if name.startswith("total_"):
            return self._sum(name.removeprefix("total_"))
        raise AttributeError(name)


class IngestionService:
    """Runs collectors over sources and stores new content.

    Collectors and the page fetcher are injectable so tests never touch the
    network.
    """

    def __init__(
        self,
        session: Session,
        collectors: dict[str, Collector] | None = None,
        page_fetcher: PageFetcher | None = None,
        video_enricher: VideoEnricher | None = None,
        transcript_fetcher: Callable[[str], TranscriptResult] | None = None,
    ) -> None:
        self.session = session
        self.repo = ContentRepository(session)
        if collectors is None or page_fetcher is None:
            client = create_client()
            collectors = collectors or {
                Platform.WEB.value: RSSCollector(client),
                Platform.SUBSTACK.value: RSSCollector(client),
                Platform.NEWSLETTER.value: RSSCollector(client),
                Platform.PODCAST.value: RSSCollector(client, content_type=ContentType.PODCAST),
                Platform.YOUTUBE.value: YouTubeCollector(client),
            }
            page_fetcher = page_fetcher or _polite_fetcher(client)
        self.collectors = collectors
        self.page_fetcher = page_fetcher
        self.video_enricher = video_enricher or _polite_video_enricher
        self.transcript_fetcher = transcript_fetcher or _polite_transcript_fetcher

    def ingest(self, source_name: str | None = None) -> IngestionStats:
        query_sources = [
            s
            for s in self.session.query(Source).order_by(Source.name).all()
            if source_name is None or s.name == source_name
        ]
        if source_name is not None and not query_sources:
            raise ValueError(f"No source named {source_name!r}")

        stats = IngestionStats()
        for source in query_sources:
            if source_name is None and not source.active:
                continue
            stats.sources.append(self._ingest_source(source))
        return stats

    def _ingest_source(self, source: Source) -> SourceIngestStats:
        stats = SourceIngestStats(source_name=source.name)

        collector = self.collectors.get(source.platform)
        if collector is None:
            stats.skipped_reason = f"no collector for platform {source.platform!r}"
            return stats
        if not source.feed_url:
            stats.skipped_reason = "no verified feed configured"
            return stats

        log.info("checking source: %s", source.name)
        source.last_checked_at = now_utc()
        self.session.commit()

        try:
            raw_items = collector.fetch(source)
        except CollectorError as exc:
            stats.ok = False
            stats.error = str(exc)
            log.error("source check failed: %s: %s", source.name, exc)
            self.session.commit()
            return stats

        for raw in raw_items:
            try:
                self._store_item(source, raw, stats)
                self.session.commit()
            except IntegrityError:
                # Unique constraint beat us to it — a duplicate by definition.
                self.session.rollback()
                stats.duplicates += 1
            except Exception as exc:
                self.session.rollback()
                log.error("item failed for %s (%s): %s", source.name, raw.url, exc)

        if source.platform == Platform.YOUTUBE.value:
            self._retry_failed_transcripts(source, stats)

        source.last_successful_check_at = now_utc()
        self.session.commit()
        log.info(
            "source check completed: %s (%d new, %d duplicates)",
            source.name,
            stats.new_articles + stats.new_videos,
            stats.duplicates,
        )
        return stats

    def _retry_failed_transcripts(self, source: Source, stats: SourceIngestStats) -> None:
        """Re-attempt transcripts that failed on a previous run (e.g. IP block)."""
        failed_items = (
            self.session.query(ContentItem)
            .filter(
                ContentItem.source_id == source.id,
                ContentItem.transcript_status == TranscriptStatus.FAILED.value,
            )
            .order_by(ContentItem.published_at.desc())
            .all()
        )
        consecutive_failures = 0
        for item in failed_items:
            if not item.external_id:
                continue
            result = self.transcript_fetcher(item.external_id)
            if result.status == TranscriptStatus.AVAILABLE:
                consecutive_failures = 0
                item.transcript_status = result.status.value
                item.raw_text = result.text
                item.language = result.language
                item.content_hash = content_hash(item.title, item.raw_text)
                item.metadata_json = {
                    k: v for k, v in item.metadata_json.items() if k != "transcript_error"
                } | {"transcript_generated": result.is_generated}
                # Content changed substantially — analysis must run (again).
                item.processing_status = ProcessingStatus.READY.value
                stats.transcripts_recovered += 1
                log.info("transcript recovered: %s", item.url)
            elif result.status == TranscriptStatus.UNAVAILABLE:
                consecutive_failures = 0
                item.transcript_status = result.status.value
                stats.transcripts_unavailable += 1
                log.info("transcript confirmed unavailable: %s", item.url)
            else:
                consecutive_failures += 1
                item.metadata_json = {**item.metadata_json, "transcript_error": result.error}
                if consecutive_failures >= TRANSCRIPT_RETRY_BREAKER:
                    log.warning(
                        "transcript retries aborted for %s after %d consecutive failures "
                        "(likely still IP-blocked); %d items left for the next run",
                        source.name,
                        consecutive_failures,
                        len(failed_items) - failed_items.index(item) - 1,
                    )
                    break
            self.session.commit()

    def _store_item(self, source: Source, raw: RawContentItem, stats: SourceIngestStats) -> None:
        normalized = normalize_url(raw.url)
        if self.repo.find_duplicate(source.id, raw.external_id, [normalized]):
            stats.duplicates += 1
            log.debug("duplicate skipped: %s", normalized)
            return

        item = ContentItem(
            source_id=source.id,
            external_id=raw.external_id,
            url=normalized,
            title=raw.title,
            author=raw.author,
            description=raw.description,
            content_type=raw.content_type.value,
            published_at=raw.published_at,
            metadata_json={"original_url": raw.url, **raw.metadata},
        )

        if raw.content_type == ContentType.ARTICLE:
            extraction = self._extract(normalized)
            item.extraction_status = extraction.status.value
            item.transcript_status = TranscriptStatus.NOT_APPLICABLE.value
            if extraction.text:
                item.raw_text = extraction.text
            item.author = raw.author or extraction.author
            item.published_at = raw.published_at or extraction.published_at
            if extraction.canonical_url:
                canonical = normalize_url(extraction.canonical_url)
                # The canonical URL may reveal this is a duplicate under a
                # different feed URL.
                if self.repo.find_duplicate(source.id, None, [canonical]):
                    stats.duplicates += 1
                    log.debug("duplicate by canonical url skipped: %s", canonical)
                    return
                item.canonical_url = canonical
            if extraction.error:
                item.metadata_json = {**item.metadata_json, "extraction_error": extraction.error}
            if extraction.status == ExtractionStatus.SUCCESS:
                stats.extracted += 1
            elif extraction.status == ExtractionStatus.PARTIAL:
                stats.partial += 1
            else:
                stats.extraction_failed += 1
                log.warning("article extraction failed: %s", normalized)
            stats.new_articles += 1
        elif raw.content_type == ContentType.PODCAST:
            # Show notes travel in description; episode audio is not transcribed.
            item.extraction_status = ExtractionStatus.NOT_ATTEMPTED.value
            item.transcript_status = TranscriptStatus.NOT_ATTEMPTED.value
            stats.new_podcasts += 1
        else:
            enrichment = self.video_enricher(normalized, raw.external_id or "")
            meta = enrichment.metadata
            transcript = enrichment.transcript

            # A metadata or transcript failure never blocks storing the video;
            # feed metadata is preserved either way.
            item.extraction_status = (
                ExtractionStatus.SUCCESS.value if meta.ok else ExtractionStatus.FAILED.value
            )
            item.description = raw.description or meta.description
            extra: dict = {}
            if meta.duration_seconds is not None:
                extra["duration_seconds"] = meta.duration_seconds
            if meta.chapters:
                extra["chapters"] = meta.chapters
            if meta.view_count is not None:
                extra["view_count"] = meta.view_count
            if meta.error:
                extra["metadata_error"] = meta.error

            item.transcript_status = transcript.status.value
            if transcript.status == TranscriptStatus.AVAILABLE:
                item.raw_text = transcript.text
                item.language = transcript.language
                extra["transcript_generated"] = transcript.is_generated
                stats.transcripts_available += 1
            elif transcript.status == TranscriptStatus.UNAVAILABLE:
                stats.transcripts_unavailable += 1
                log.info("transcript unavailable: %s", normalized)
            else:
                stats.transcripts_failed += 1
                log.warning("transcript failed: %s: %s", normalized, transcript.error)
            if transcript.error:
                extra["transcript_error"] = transcript.error

            item.metadata_json = {**item.metadata_json, **extra}
            stats.new_videos += 1

        item.content_hash = content_hash(item.title, item.raw_text)
        item.processing_status = ProcessingStatus.READY.value
        self.repo.add(item)
        log.info("new content: [%s] %s", source.name, item.title or normalized)

    def _extract(self, url: str) -> ExtractionResult:
        try:
            html = self.page_fetcher(url)
        except httpx.HTTPError as exc:
            return ExtractionResult(
                status=ExtractionStatus.FAILED, error=f"page fetch failed: {exc}"
            )
        return extract_article(html, url)
