from dataclasses import dataclass

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from yt_dlp import YoutubeDL

from culture.logging import get_logger
from culture.models.content import TranscriptStatus

log = get_logger("culture.extraction.youtube")

PREFERRED_LANGUAGES = ("en",)

# Errors that mean "this video has no transcript", as opposed to "we were
# blocked or the network broke" — the distinction is stored per video.
_UNAVAILABLE_ERRORS = (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable)


@dataclass
class VideoMetadata:
    ok: bool
    duration_seconds: int | None = None
    chapters: list[dict] | None = None
    description: str | None = None
    view_count: int | None = None
    error: str | None = None


@dataclass
class TranscriptResult:
    status: TranscriptStatus
    text: str | None = None
    language: str | None = None
    is_generated: bool | None = None
    error: str | None = None


@dataclass
class VideoEnrichment:
    metadata: VideoMetadata
    transcript: TranscriptResult


def fetch_video_metadata(url: str) -> VideoMetadata:
    """Fetch duration, chapters, description and view count via yt-dlp."""
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        log.warning("video metadata fetch failed for %s: %s", url, exc)
        return VideoMetadata(ok=False, error=str(exc))
    chapters = [
        {"title": c.get("title"), "start_time": c.get("start_time")}
        for c in (info.get("chapters") or [])
    ]
    return VideoMetadata(
        ok=True,
        duration_seconds=info.get("duration"),
        chapters=chapters or None,
        description=(info.get("description") or "").strip() or None,
        view_count=info.get("view_count"),
    )


def fetch_transcript(video_id: str) -> TranscriptResult:
    """Fetch a transcript, preferring English, accepting any language."""
    api = YouTubeTranscriptApi()
    try:
        try:
            fetched = api.fetch(video_id, languages=list(PREFERRED_LANGUAGES))
        except NoTranscriptFound:
            transcripts = list(api.list(video_id))
            if not transcripts:
                raise
            fetched = transcripts[0].fetch()
    except _UNAVAILABLE_ERRORS as exc:
        return TranscriptResult(status=TranscriptStatus.UNAVAILABLE, error=type(exc).__name__)
    except Exception as exc:
        log.warning("transcript fetch failed for %s: %s", video_id, exc)
        return TranscriptResult(status=TranscriptStatus.FAILED, error=str(exc))

    text = " ".join(s.text.strip() for s in fetched.snippets if s.text.strip())
    if not text:
        return TranscriptResult(status=TranscriptStatus.UNAVAILABLE, error="empty transcript")
    return TranscriptResult(
        status=TranscriptStatus.AVAILABLE,
        text=text,
        language=fetched.language_code,
        is_generated=fetched.is_generated,
    )


def enrich_video(url: str, video_id: str) -> VideoEnrichment:
    return VideoEnrichment(
        metadata=fetch_video_metadata(url),
        transcript=fetch_transcript(video_id),
    )
