from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.analysis.prompts import ITEM_SYSTEM_PROMPT, build_item_prompt
from culture.analysis.provider import AIProvider
from culture.logging import get_logger
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem, ProcessingStatus
from culture.models.source import Source
from culture.schemas.analysis import EntityType, ItemAnalysisResponse
from culture.services.boilerplate import cleaned_text_for
from culture.utils.dates import now_utc

log = get_logger("culture.analysis")

ANALYSIS_VERSION = "1"

# Items published further back than this are marked skipped instead of analyzed.
# New sources can expose deep back-catalogs (podcast archives of hundreds of
# episodes); analyzing years-old content costs real money and adds noise, while
# the items themselves stay stored and reversible (set status back to 'ready').
ANALYSIS_MAX_AGE_DAYS = 90

# Direct 1:1 list fields between the LLM response and the ContentAnalysis row.
# Entity columns are filled separately by fanning out the typed entities array.
_LIST_FIELDS = (
    "major_points",
    "topics",
    "tags",
    "consumer_archetypes",
    "possible_signals",
)


@dataclass
class AnalyzeStats:
    analyzed: int = 0
    failed: int = 0
    stale_skipped: int = 0
    failures: list[str] = field(default_factory=list)


class ItemAnalyzer:
    def __init__(self, session: Session, provider: AIProvider) -> None:
        self.session = session
        self.provider = provider

    def pending_items(self, limit: int | None = None) -> list[ContentItem]:
        query = (
            select(ContentItem)
            .where(
                ContentItem.processing_status.in_(
                    # 'failed' is included so every run retries earlier failures.
                    [ProcessingStatus.READY.value, ProcessingStatus.FAILED.value]
                )
            )
            .order_by(ContentItem.id)
        )
        if limit:
            query = query.limit(limit)
        return list(self.session.scalars(query))

    def skip_stale_items(self) -> int:
        """Mark pending items older than ANALYSIS_MAX_AGE_DAYS as skipped."""
        from datetime import timedelta

        from culture.utils.dates import ensure_utc

        cutoff = now_utc() - timedelta(days=ANALYSIS_MAX_AGE_DAYS)
        skipped = 0
        for item in self.pending_items():
            when = ensure_utc(item.published_at or item.discovered_at)
            if when and when < cutoff:
                item.processing_status = ProcessingStatus.SKIPPED.value
                skipped += 1
        if skipped:
            self.session.commit()
            log.info("skipped %d items older than %d days", skipped, ANALYSIS_MAX_AGE_DAYS)
        return skipped

    def analyze_pending(self, limit: int | None = None) -> AnalyzeStats:
        stats = AnalyzeStats()
        stats.stale_skipped = self.skip_stale_items()
        items = self.pending_items(limit)
        log.info("analysis started: %d items pending", len(items))
        for item in items:
            item.processing_status = ProcessingStatus.ANALYZING.value
            self.session.commit()
            try:
                self._analyze_item(item)
                item.processing_status = ProcessingStatus.ANALYZED.value
                stats.analyzed += 1
                log.info("analysis completed: [%d] %s", item.id, item.title or item.url)
            except Exception as exc:
                item.processing_status = ProcessingStatus.FAILED.value
                stats.failed += 1
                stats.failures.append(f"[{item.id}] {item.title or item.url}: {exc}")
                log.error("analysis failed: [%d] %s: %s", item.id, item.title or item.url, exc)
            self.session.commit()
        return stats

    def _analyze_item(self, item: ContentItem) -> None:
        source = self.session.get(Source, item.source_id)
        assert source is not None
        text = cleaned_text_for(self.session, item)
        prompt = build_item_prompt(source, item, text)
        response = self.provider.generate_structured(
            ITEM_SYSTEM_PROMPT, prompt, ItemAnalysisResponse
        )
        self.session.add(self._to_row(item, response))

    def _to_row(self, item: ContentItem, response: ItemAnalysisResponse) -> ContentAnalysis:
        row = ContentAnalysis(
            content_item_id=item.id,
            summary=response.summary,
            why_it_matters=response.why_it_matters,
            lifecycle_stage=response.lifecycle_stage.value if response.lifecycle_stage else None,
            cultural_origin_score=response.scores.cultural_origin,
            editorial_momentum_score=response.scores.editorial_momentum,
            urban_adoption_score=response.scores.urban_adoption,
            meme_recognition_score=response.scores.meme_recognition,
            creator_adoption_score=response.scores.creator_adoption,
            commercial_evidence_score=response.scores.commercial_evidence,
            saturation_risk_score=response.scores.saturation_risk,
            longevity_score=response.scores.longevity,
            facts_json={"claims": response.facts},
            interpretations_json={"inferences": response.interpretations},
            analysis_model=self.provider.model,
            analysis_provider=self.provider.name,
            analysis_version=ANALYSIS_VERSION,
            analyzed_at=now_utc(),
        )
        for field_name in _LIST_FIELDS:
            setattr(row, field_name, getattr(response, field_name))
        for entity_type in EntityType:
            setattr(row, entity_type.value, response.entity_names(entity_type))
        return row
