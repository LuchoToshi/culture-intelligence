from dataclasses import dataclass, field
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.analysis.prompts import ITEM_SYSTEM_PROMPT, build_item_prompt
from culture.analysis.provider import (
    AIProvider,
    BudgetExceededError,
    RoutingProvider,
    estimate_cost_usd,
)
from culture.database import retry_on_disconnect
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

# Batch API: how often to poll while waiting, and the default cap on how
# long one `culture analyze --batch` invocation blocks before giving up and
# leaving the batch to finish server-side (see collect_batch).
BATCH_POLL_INTERVAL_SECONDS = 20
BATCH_DEFAULT_WAIT_SECONDS = 600


@dataclass
class AnalyzeStats:
    analyzed: int = 0
    failed: int = 0
    stale_skipped: int = 0
    failures: list[str] = field(default_factory=list)
    budget_stopped: bool = False
    spent_usd: float = 0.0


@dataclass
class BatchAnalyzeStats:
    stale_skipped: int = 0
    submitted: int = 0
    succeeded: int = 0
    errored: int = 0
    errors: list[str] = field(default_factory=list)
    spent_usd: float = 0.0
    batch_id: str | None = None
    # True if the batch hadn't finished within the wait window — items stay
    # 'analyzing'; call collect_batch(batch_id) later to finish them. The
    # batch itself keeps running on Anthropic's side regardless.
    still_processing: bool = False


def _model_for(provider: AIProvider, images: list) -> str:
    """Which model a given item would use — mirrors RoutingProvider's own
    routing decision, without dispatching a call. Used to build Batch API
    requests, where each request picks its own model up front."""
    if isinstance(provider, RoutingProvider):
        return provider.capable.model if images else provider.cheap.model
    return provider.model


def _effort_for(provider: AIProvider, images: list) -> str | None:
    """The effort setting the routed model takes, so Batch API requests match
    what a live call through the same provider would send."""
    if isinstance(provider, RoutingProvider):
        return getattr(provider.capable if images else provider.cheap, "effort", None)
    return getattr(provider, "effort", None)


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
            except BudgetExceededError as exc:
                # Not a per-item failure — the call was never attempted.
                # Leave the item exactly as it was so it's retried next run.
                item.processing_status = ProcessingStatus.READY.value
                self.session.commit()
                stats.budget_stopped = True
                left = len(items) - stats.analyzed - stats.failed
                log.warning("%s (%d item(s) left unanalyzed this run)", exc, left)
                break
            except Exception as exc:
                item.processing_status = ProcessingStatus.FAILED.value
                stats.failed += 1
                stats.failures.append(f"[{item.id}] {item.title or item.url}: {exc}")
                log.error("analysis failed: [%d] %s: %s", item.id, item.title or item.url, exc)
            self.session.commit()
        stats.spent_usd = getattr(self.provider, "spent_usd", 0.0)
        return stats

    def analyze_pending_batch(
        self, limit: int, wait_seconds: int = BATCH_DEFAULT_WAIT_SECONDS
    ) -> BatchAnalyzeStats:
        """Submit pending items to the Message Batches API (50% off token
        rates) instead of one live call per item. Each request still picks
        its own model via the same routing rule as the live path.

        Blocks polling for up to wait_seconds. If the batch isn't done by
        then, items stay 'analyzing' (so a concurrent run won't resubmit
        them) and stats.still_processing is set — the batch keeps running
        server-side regardless; collect_batch(batch_id) finishes it later.
        """
        import time

        stats = BatchAnalyzeStats()
        items, requests = retry_on_disconnect(
            self.session, lambda: self._build_batch_requests(limit, stats)
        )
        if not items:
            return stats
        stats.submitted = len(items)
        # Deliberately not retried: if the server applied this commit but the reply was
        # lost, a rerun would no longer see these items as pending and would orphan them.
        for item in items:
            item.processing_status = ProcessingStatus.ANALYZING.value
        self.session.commit()

        client = self.provider.client
        try:
            batch = client.messages.batches.create(requests=requests)
        except Exception:
            # Nothing was submitted (e.g. credit balance empty): put the items back in
            # the queue, or they'd sit in 'analyzing' forever and never be retried.
            for item in items:
                item.processing_status = ProcessingStatus.READY.value
            self.session.commit()
            raise
        stats.batch_id = batch.id
        log.info("batch submitted: %s (%d items)", batch.id, len(requests))

        waited = 0
        while batch.processing_status != "ended" and waited < wait_seconds:
            time.sleep(BATCH_POLL_INTERVAL_SECONDS)
            waited += BATCH_POLL_INTERVAL_SECONDS
            batch = client.messages.batches.retrieve(batch.id)

        if batch.processing_status != "ended":
            stats.still_processing = True
            log.warning(
                "batch %s still processing after %ds — items stay 'analyzing'; "
                "run `culture batch-collect %s` later to finish it.",
                batch.id,
                wait_seconds,
                batch.id,
            )
            return stats

        self._apply_batch_results(client, batch.id, stats)
        return stats

    def _build_batch_requests(
        self, limit: int, stats: BatchAnalyzeStats
    ) -> tuple[list[ContentItem], list]:
        """The pending items and one Batch API request for each.

        The slow, read-heavy part of a batch run: the boilerplate check re-queries a
        source's full text for every item, which makes this the likeliest place for a
        connection to drop. A rollback loses nothing here and skipping stale items is
        safe to repeat, so retry_on_disconnect can restart it from the top.
        """
        from anthropic import transform_schema
        from anthropic.types.json_output_format_param import JSONOutputFormatParam
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        from culture.analysis.provider import _cached_system, build_message_content
        from culture.services.intake import load_item_images

        stats.stale_skipped += self.skip_stale_items()
        items = self.pending_items(limit)
        if not items:
            return items, []

        output_format_param = JSONOutputFormatParam(
            type="json_schema", schema=transform_schema(ItemAnalysisResponse)
        )
        requests = []
        for item in items:
            source = self.session.get(Source, item.source_id)
            assert source is not None
            text = cleaned_text_for(self.session, item)
            images = load_item_images(item) if item.metadata_json.get("images") else []
            prompt = build_item_prompt(source, item, text, has_images=bool(images))
            model = _model_for(self.provider, images)
            output_config: dict = {"format": output_format_param}
            effort = _effort_for(self.provider, images)
            if effort:
                output_config["effort"] = effort
            requests.append(
                Request(
                    custom_id=str(item.id),
                    params=MessageCreateParamsNonStreaming(
                        model=model,
                        max_tokens=16000,
                        system=_cached_system(ITEM_SYSTEM_PROMPT),
                        messages=[
                            {"role": "user", "content": build_message_content(prompt, images)}
                        ],
                        output_config=output_config,
                    ),
                )
            )
        return items, requests

    def collect_batch(self, batch_id: str) -> BatchAnalyzeStats:
        """Finish a batch that was still processing when analyze_pending_batch
        gave up waiting. Safe to call repeatedly — reports still_processing
        again if it's not done yet."""
        stats = BatchAnalyzeStats(batch_id=batch_id)
        client = self.provider.client
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            stats.still_processing = True
            return stats
        self._apply_batch_results(client, batch_id, stats)
        return stats

    def _apply_batch_results(self, client, batch_id: str, stats: BatchAnalyzeStats) -> None:
        for result in client.messages.batches.results(batch_id):
            # One result per attempt: a dropped connection repeats one row, not the batch.
            retry_on_disconnect(self.session, partial(self._apply_batch_result, result, stats))
        log.info(
            "batch %s collected: %d succeeded, %d errored, $%.4f",
            batch_id,
            stats.succeeded,
            stats.errored,
            stats.spent_usd,
        )

    def _apply_batch_result(self, result, stats: BatchAnalyzeStats) -> None:
        item = self.session.get(ContentItem, int(result.custom_id))
        if item is None:
            log.error("batch result for unknown item id %s", result.custom_id)
            return
        if item.processing_status != ProcessingStatus.ANALYZING.value:
            # Already collected by an earlier call (results stay
            # retrievable for 29 days). Re-applying would duplicate
            # the ContentAnalysis row. Not a per-result failure.
            return
        # Counted in `outcome` and merged only after the commit, so an attempt
        # that is retried after a dropped connection cannot count this result twice.
        outcome = BatchAnalyzeStats()
        if result.result.type == "succeeded":
            message = result.result.message
            usage = getattr(message, "usage", None)
            if usage is not None:
                cost = estimate_cost_usd(usage, message.model, batch=True)
                if cost is not None:
                    outcome.spent_usd += cost
            try:
                text = next(b.text for b in message.content if b.type == "text")
                response = ItemAnalysisResponse.model_validate_json(text)
                self.session.add(self._to_row(item, response, model=message.model))
                item.processing_status = ProcessingStatus.ANALYZED.value
                outcome.succeeded += 1
            except (StopIteration, ValueError) as exc:
                item.processing_status = ProcessingStatus.FAILED.value
                outcome.errored += 1
                outcome.errors.append(f"[{item.id}] {item.title or item.url}: {exc}")
                log.error("batch result failed validation: [%d] %s", item.id, exc)
        else:
            item.processing_status = ProcessingStatus.FAILED.value
            outcome.errored += 1
            outcome.errors.append(
                f"[{item.id}] {item.title or item.url}: batch result {result.result.type}"
            )
            log.error("batch item failed: [%d] %s", item.id, result.result.type)
        self.session.commit()
        stats.spent_usd += outcome.spent_usd
        stats.succeeded += outcome.succeeded
        stats.errored += outcome.errored
        stats.errors.extend(outcome.errors)

    def _analyze_item(self, item: ContentItem) -> None:
        from culture.services.intake import load_item_images

        source = self.session.get(Source, item.source_id)
        assert source is not None
        text = cleaned_text_for(self.session, item)
        images = load_item_images(item) if item.metadata_json.get("images") else []
        prompt = build_item_prompt(source, item, text, has_images=bool(images))
        if images:
            # Passed conditionally so text-only providers/fakes keep working.
            response = self.provider.generate_structured(
                ITEM_SYSTEM_PROMPT, prompt, ItemAnalysisResponse, images=images
            )
        else:
            response = self.provider.generate_structured(
                ITEM_SYSTEM_PROMPT, prompt, ItemAnalysisResponse
            )
        self.session.add(self._to_row(item, response))

    def _to_row(
        self, item: ContentItem, response: ItemAnalysisResponse, model: str | None = None
    ) -> ContentAnalysis:
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
            analysis_model=model or self.provider.model,
            analysis_provider=self.provider.name,
            analysis_version=ANALYSIS_VERSION,
            analyzed_at=now_utc(),
        )
        for field_name in _LIST_FIELDS:
            setattr(row, field_name, getattr(response, field_name))
        for entity_type in EntityType:
            setattr(row, entity_type.value, response.entity_names(entity_type))
        return row
