from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from culture.config import get_settings
from culture.logging import configure_logging, get_logger

app = typer.Typer(help="Cultural intelligence platform.", no_args_is_help=True)
db_app = typer.Typer(help="Database management.", no_args_is_help=True)
app.add_typer(db_app, name="db")

console = Console()
log = get_logger("culture.cli")

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@app.callback()
def main() -> None:
    configure_logging(get_settings().log_level)


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    return cfg


@db_app.command("init")
def db_init() -> None:
    """Apply all pending database migrations."""
    from alembic import command

    command.upgrade(_alembic_config(), "head")
    console.print("[green]Database schema is up to date.[/green]")


@app.command()
def status() -> None:
    """Show system health: sources, content, analysis backlog."""
    from sqlalchemy import func, select

    from culture.database import get_engine, session_scope
    from culture.models import ContentAnalysis, ContentItem, Source
    from culture.models.content import (
        ExtractionStatus,
        ProcessingStatus,
        TranscriptStatus,
    )
    from culture.models.source import Platform

    settings = get_settings()
    try:
        engine = get_engine()
        with session_scope(engine) as session:
            total_sources = session.scalar(select(func.count(Source.id))) or 0
            active_sources = (
                session.scalar(select(func.count(Source.id)).where(Source.active.is_(True))) or 0
            )
            supported_sources = (
                session.scalar(
                    select(func.count(Source.id)).where(
                        Source.active.is_(True),
                        Source.platform.in_(
                        [
                            Platform.WEB.value,
                            Platform.YOUTUBE.value,
                            Platform.SUBSTACK.value,
                            Platform.NEWSLETTER.value,
                            Platform.PODCAST.value,
                        ]
                    ),
                        Source.feed_url.is_not(None),
                    )
                )
                or 0
            )
            last_ingestion = session.scalar(select(func.max(Source.last_checked_at)))
            total_items = session.scalar(select(func.count(ContentItem.id))) or 0
            new_items = (
                session.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.processing_status == ProcessingStatus.NEW.value
                    )
                )
                or 0
            )
            unanalyzed = (
                session.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.processing_status.in_(
                            [ProcessingStatus.NEW.value, ProcessingStatus.READY.value]
                        )
                    )
                )
                or 0
            )
            failed_extractions = (
                session.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.extraction_status == ExtractionStatus.FAILED.value
                    )
                )
                or 0
            )
            failed_transcripts = (
                session.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.transcript_status == TranscriptStatus.FAILED.value
                    )
                )
                or 0
            )
            failed_analyses = (
                session.scalar(
                    select(func.count(ContentItem.id)).where(
                        ContentItem.processing_status == ProcessingStatus.FAILED.value
                    )
                )
                or 0
            )
            total_analyses = session.scalar(select(func.count(ContentAnalysis.id))) or 0
            latest_content = session.scalar(select(func.max(ContentItem.published_at)))
    except Exception as exc:
        console.print(f"[red]Cannot reach database:[/red] {settings.database_url}")
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    table = Table(title="Culture Intelligence — Status", show_header=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Total sources", str(total_sources))
    table.add_row("Active sources", str(active_sources))
    table.add_row("Supported sources (web/youtube)", str(supported_sources))
    table.add_row("Last ingestion check", str(last_ingestion) if last_ingestion else "never")
    table.add_row("Total content items", str(total_items))
    table.add_row("New items", str(new_items))
    table.add_row("Unanalyzed items", str(unanalyzed))
    table.add_row("Failed extractions", str(failed_extractions))
    table.add_row("Failed transcripts", str(failed_transcripts))
    table.add_row("Failed analyses", str(failed_analyses))
    table.add_row("Stored analyses", str(total_analyses))
    table.add_row("Latest content date", str(latest_content) if latest_content else "none")
    console.print(table)


def _not_yet(phase: str) -> None:
    console.print(f"[yellow]Not implemented yet — arrives in {phase}.[/yellow]")
    raise typer.Exit(1)


@app.command()
def seed(
    file: Path = typer.Option(
        PROJECT_ROOT / "seeds" / "sources.yaml", "--file", help="Seed YAML file."
    ),
) -> None:
    """Import seed sources. Idempotent: matches by name, updates seed-owned fields only."""
    from culture.database import get_engine, session_scope
    from culture.services.seeding import import_seeds, load_seed_records

    try:
        records = load_seed_records(file)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        result = import_seeds(session, records)

    console.print(
        f"Seed import: [green]{len(result.created)} created[/green], "
        f"{len(result.updated)} updated, {len(result.unchanged)} unchanged."
    )
    if result.errors:
        console.print(f"[red]{len(result.errors)} invalid record(s) skipped:[/red]")
        for error in result.errors:
            console.print(f"  [red]- {error}[/red]")
        raise typer.Exit(1)


@app.command()
def sources() -> None:
    """List monitored sources."""
    from culture.database import get_engine, session_scope
    from culture.repositories.sources import SourceRepository

    def fmt(value) -> str:
        return value.strftime("%Y-%m-%d %H:%M") if value else "—"

    with session_scope(get_engine()) as session:
        rows = SourceRepository(session).list_with_latest_item()
        table = Table(title=f"Sources ({len(rows)})")
        table.add_column("Name")
        table.add_column("Platform")
        table.add_column("Tier")
        table.add_column("Active")
        table.add_column("Feed")
        table.add_column("Last checked")
        table.add_column("Last success")
        table.add_column("Latest item")
        for source, latest_item in rows:
            table.add_row(
                source.name,
                source.platform,
                source.tier,
                "yes" if source.active else "no",
                "yes" if source.feed_url else "—",
                fmt(source.last_checked_at),
                fmt(source.last_successful_check_at),
                fmt(latest_item),
            )
        console.print(table)


@app.command()
def ingest(
    source: str | None = typer.Option(None, "--source", help="Ingest a single source by name."),
) -> None:
    """Check all active supported sources and store new content."""
    from culture.database import get_engine, session_scope
    from culture.services.ingestion import IngestionService

    with session_scope(get_engine()) as session:
        service = IngestionService(session)
        try:
            stats = service.ingest(source_name=source)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc

    console.print()
    console.print(f"Sources checked: {stats.checked}")
    console.print(f"Successful: {stats.successful}")
    console.print(f"Failed: {len(stats.failed)}")
    for failure in stats.failed:
        console.print(f"  [red]- {failure.source_name}: {failure.error}[/red]")
    if stats.skipped:
        console.print(f"Skipped: {len(stats.skipped)}")
        for skip in stats.skipped:
            console.print(f"  [dim]- {skip.source_name}: {skip.skipped_reason}[/dim]")
    console.print()
    console.print(f"New articles: {stats.total_new_articles}")
    console.print(f"New videos: {stats.total_new_videos}")
    console.print(f"New podcast episodes: {stats.total_new_podcasts}")
    console.print(f"New social posts: {stats.total_new_posts}")
    console.print(f"Duplicates skipped: {stats.total_duplicates}")
    console.print()
    console.print(f"Articles extracted: {stats.total_extracted}")
    console.print(f"Partial extractions: {stats.total_partial}")
    console.print(f"Extraction failures: {stats.total_extraction_failed}")
    if (
        stats.total_new_videos
        or stats.total_transcripts_available
        or stats.total_transcripts_recovered
    ):
        console.print()
        console.print("YouTube transcripts:")
        console.print(f"Available: {stats.total_transcripts_available}")
        console.print(f"Unavailable: {stats.total_transcripts_unavailable}")
        console.print(f"Failed: {stats.total_transcripts_failed}")
        if stats.total_transcripts_recovered:
            console.print(f"Recovered from earlier failures: {stats.total_transcripts_recovered}")


@app.command()
def analyze(
    limit: int | None = typer.Option(None, "--limit", help="Analyze at most N items."),
    max_spend: float | None = typer.Option(
        None,
        "--max-spend",
        help="Real dollar cap for this run (item analysis + signal matching combined). "
        "Defaults to AI_MAX_SPEND_PER_RUN.",
    ),
    routing: bool = typer.Option(
        True,
        "--routing/--no-routing",
        help="Route text-only items to a cheaper model, images to the capable one "
        "(see RoutingProvider). Disable to force every item onto the capable model.",
    ),
    batch: bool = typer.Option(
        False,
        "--batch",
        help="Submit via the Message Batches API instead of one live call per item: "
        "50% off every token, in exchange for asynchronous processing (usually under "
        "an hour, up to 24h). Requires --limit. --max-spend is NOT enforced in this "
        "mode — there's no per-item checkpoint to stop at once a batch is submitted.",
    ),
    batch_wait: int = typer.Option(
        600,
        "--batch-wait",
        help="Seconds to block polling a --batch run before giving up and leaving it "
        "to finish server-side (see `culture batch-collect`).",
    ),
) -> None:
    """Run AI analysis on unprocessed content (retries earlier failures)."""
    from culture.analysis.item_analyzer import ItemAnalyzer
    from culture.analysis.provider import (
        AIProvider,
        ProviderError,
        RoutingProvider,
        get_provider,
        get_routing_provider,
    )
    from culture.database import get_engine, session_scope

    if batch and not limit:
        console.print(
            "[red]--batch requires --limit — bounds the size of one batch submission.[/red]"
        )
        raise typer.Exit(1)

    settings = get_settings()
    effective_cap = max_spend if max_spend else -1.0
    try:
        provider = (
            get_routing_provider(settings, max_spend_usd=effective_cap)
            if routing
            else get_provider(settings, max_spend_usd=effective_cap)
        )
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if batch:
        assert limit is not None  # enforced by the --batch/--limit check above
        with session_scope(get_engine()) as session:
            analyzer = ItemAnalyzer(session, provider)
            pending = len(analyzer.pending_items())
            if pending == 0:
                console.print("Nothing to analyze — all content is processed.")
                return
            todo = min(pending, limit)
            console.print(f"Submitting {todo} of {pending} pending items as a batch...")
            batch_stats = analyzer.analyze_pending_batch(limit, wait_seconds=batch_wait)

        console.print()
        if batch_stats.stale_skipped:
            console.print(
                f"Skipped {batch_stats.stale_skipped} back-catalog item(s) older than "
                "the analysis window."
            )
        console.print(f"Batch: [bold]{batch_stats.batch_id}[/bold]")
        if batch_stats.still_processing:
            console.print(
                f"[yellow]Still processing after {batch_wait}s — items stay queued. "
                f"Run `culture batch-collect {batch_stats.batch_id}` later to finish.[/yellow]"
            )
            return
        console.print(f"Succeeded: {batch_stats.succeeded}")
        console.print(f"Errored: {batch_stats.errored}")
        for failure in batch_stats.errors[:10]:
            console.print(f"  [red]- {failure}[/red]")
        console.print(
            f"Spend this run: [bold]${batch_stats.spent_usd:.2f}[/bold] (batch-discounted)"
        )
        console.print(
            "Signal matching wasn't run — batch mode only covers item analysis. "
            "Run `culture signals update` to feed these into the registry."
        )
        return

    with session_scope(get_engine()) as session:
        analyzer = ItemAnalyzer(session, provider)
        pending = len(analyzer.pending_items())
        if pending == 0:
            console.print("Nothing to analyze — all content is processed.")
            return
        todo = min(pending, limit) if limit else pending
        if isinstance(provider, RoutingProvider):
            console.print(
                f"Analyzing {todo} of {pending} pending items — routed: "
                f"{provider.cheap.model} for text-only, {provider.capable.model} for images..."
            )
        else:
            console.print(
                f"Analyzing {todo} of {pending} pending items "
                f"with {provider.name}/{provider.model}..."
            )
        stats = analyzer.analyze_pending(limit)
        remaining = len(analyzer.pending_items())

    console.print()
    if stats.stale_skipped:
        console.print(
            f"Skipped {stats.stale_skipped} back-catalog item(s) older than the analysis window."
        )
    console.print(f"Analyzed: {stats.analyzed}")
    console.print(f"Failed: {stats.failed}")
    for failure in stats.failures[:10]:
        console.print(f"  [red]- {failure}[/red]")
    console.print(f"Remaining unanalyzed: {remaining}")
    if stats.budget_stopped:
        console.print(
            "[yellow]Stopped early: spend cap reached. Remaining items retry next run.[/yellow]"
        )

    # Newly analyzed items feed the signal registry in the same run. Signal
    # matching batches many items into one shared call, so it can't route
    # per-item — always the capable model, but still drawing from the same
    # cumulative spend/cap as the (possibly routed) analysis stage above.
    from culture.services.signals import SignalService

    signal_provider: AIProvider
    if isinstance(provider, RoutingProvider):
        signal_provider = provider.capable
        signal_provider.spent_usd = provider.spent_usd
        signal_provider.max_spend_usd = provider.max_spend_usd
    else:
        signal_provider = provider

    with session_scope(get_engine()) as session:
        signal_stats = SignalService(session, signal_provider).update_signals()
    if signal_stats.items_processed:
        console.print()
        console.print(
            f"Signal registry: {signal_stats.items_processed} items evaluated, "
            f"{signal_stats.links_created} evidence links, "
            f"{signal_stats.signals_created} new signals."
        )
        for name in signal_stats.new_signal_names[:15]:
            console.print(f"  [green]+ {name}[/green]")
    if signal_stats.budget_stopped:
        console.print(
            "[yellow]Stopped early: spend cap reached. Remaining items retry next run.[/yellow]"
        )

    # signal_provider.spent_usd is the source of truth once routing is on:
    # it was pre-seeded with the router's combined total before signal
    # matching ran, so re-adding provider.spent_usd here would double-count
    # the cheap model's share.
    final_spent_usd = (
        signal_provider.spent_usd if isinstance(provider, RoutingProvider) else provider.spent_usd
    )
    console.print()
    console.print(f"Spend this run: [bold]${final_spent_usd:.2f}[/bold]")


@app.command(name="batch-collect")
def batch_collect(
    batch_id: str = typer.Argument(..., help="Batch ID printed by `culture analyze --batch`."),
) -> None:
    """Finish a --batch analyze run that was still processing when it gave up waiting."""
    from culture.analysis.item_analyzer import ItemAnalyzer
    from culture.analysis.provider import ProviderError, get_provider
    from culture.database import get_engine, session_scope

    try:
        provider = get_provider(get_settings(), max_spend_usd=None)
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        stats = ItemAnalyzer(session, provider).collect_batch(batch_id)

    if stats.still_processing:
        console.print(f"[yellow]Batch {batch_id} is still processing. Try again later.[/yellow]")
        return
    console.print(f"Succeeded: {stats.succeeded}")
    console.print(f"Errored: {stats.errored}")
    for failure in stats.errors[:10]:
        console.print(f"  [red]- {failure}[/red]")
    console.print(f"Spend this run: [bold]${stats.spent_usd:.2f}[/bold] (batch-discounted)")
    console.print(
        "Signal matching wasn't run — batch mode only covers item analysis. "
        "Run `culture signals update` to feed these into the registry."
    )


@app.command()
def web(
    port: int = typer.Option(8765, "--port", help="Port to serve on."),
) -> None:
    """Serve the local read-only web interface (localhost only)."""
    import uvicorn

    from culture.web.app import create_app

    console.print(
        f"Culture Intelligence UI: [bold]http://127.0.0.1:{port}[/bold]  (Ctrl+C to stop)"
    )
    try:
        uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
    except SystemExit:
        console.print(
            f"[red]Port {port} is already in use — the UI is probably already running.[/red]\n"
            f"Open [bold]http://127.0.0.1:{port}[/bold] in your browser, or stop the other "
            f"instance first:  [bold]lsof -ti :{port} | xargs kill[/bold]  — or serve on "
            f"another port:  [bold]uv run culture web --port {port + 1}[/bold]"
        )
        raise typer.Exit(1) from None


@app.command()
def discover(
    min_sources: int = typer.Option(
        2, "--min-sources", help="Independent citing sources required to create a candidate."
    ),
) -> None:
    """Mine collected evidence for new candidate sources (entities + outbound links)."""
    from culture.database import get_engine, session_scope
    from culture.services.discovery import run_discovery

    with session_scope(get_engine()) as session:
        stats = run_discovery(session, min_citing_sources=min_sources)

    console.print(
        f"New candidates: [green]{len(stats.created)}[/green] · "
        f"updated: {stats.updated} · below threshold: {stats.below_threshold} · "
        f"already known: {stats.skipped_known}"
    )
    for name in stats.created[:20]:
        console.print(f"  [green]+ {name}[/green]")
    if len(stats.created) > 20:
        console.print(f"  [dim]… and {len(stats.created) - 20} more — see culture sources[/dim]")


@app.command()
def add(
    url: str = typer.Argument(..., help="Post URL (Instagram, TikTok, ...)."),
    caption: str | None = typer.Option(None, "--caption", help="Post caption / text."),
    image: Path | None = typer.Option(None, "--image", help="Path to a saved screenshot."),
    source: str | None = typer.Option(
        None, "--source", help="Registered source name (inferred from URL when possible)."
    ),
    date: str | None = typer.Option(None, "--date", help="Publication date, YYYY-MM-DD."),
) -> None:
    """Manually submit a social post as evidence (the compliant IG/TikTok path)."""
    from datetime import UTC, datetime

    from culture.database import get_engine, session_scope
    from culture.services.intake import IntakeError, add_post

    published = None
    if date:
        try:
            published = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError as exc:
            console.print(f"[red]Invalid --date {date!r} — use YYYY-MM-DD.[/red]")
            raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        try:
            item = add_post(
                session,
                url,
                caption=caption,
                image_path=image,
                source_name=source,
                published=published,
            )
        except IntakeError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        source_name = item.source.name
    console.print(
        f"[green]Stored post #{item.id}[/green] for {source_name}"
        f"{' with image' if image else ' (caption only)'} — "
        "it will be analyzed on the next `culture analyze` or daily run."
    )


@app.command()
def lifecycle() -> None:
    """Apply source-tier transitions from collection evidence (promote/retire)."""
    from culture.database import get_engine, session_scope
    from culture.services.source_lifecycle import apply_lifecycle, quiet_core_sources

    with session_scope(get_engine()) as session:
        stats = apply_lifecycle(session)
        flagged = quiet_core_sources(session)

    for tr in stats.promoted:
        console.print(
            f"  [green]UP {tr.source_name}: {tr.from_tier} -> {tr.to_tier}[/green] — {tr.reason}"
        )
    for tr in stats.retired:
        console.print(
            f"  [yellow]DOWN {tr.source_name}: {tr.from_tier} -> {tr.to_tier}[/yellow]"
            f" — {tr.reason}"
        )
    if not stats.promoted and not stats.retired:
        console.print("No tier changes — every source is where its evidence puts it.")
    for source, quiet in flagged:
        console.print(
            f"  [red]FLAG core source quiet: {source.name} — no content in {quiet} days "
            f"(recommendation only; core is never auto-retired)[/red]"
        )


@app.command()
def review(
    done: str | None = typer.Option(None, "--done", help="Mark this source as reviewed today."),
) -> None:
    """Weekly human review queue: manual-platform accounts and unverified candidates."""
    from culture.database import get_engine, session_scope
    from culture.services.source_lifecycle import mark_reviewed, review_queue

    with session_scope(get_engine()) as session:
        if done:
            try:
                source = mark_reviewed(session, done)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(1) from exc
            console.print(f"[green]Marked reviewed: {source.name}[/green]")
            return
        queue = review_queue(session)

        if queue["due"]:
            table = Table(title=f"Accounts due for review ({len(queue['due'])})")
            table.add_column("Account")
            table.add_column("Platform")
            table.add_column("Tier")
            table.add_column("Last reviewed")
            for source, overdue in queue["due"]:
                table.add_row(
                    source.name,
                    source.platform,
                    source.tier,
                    f"{overdue} days ago" if overdue is not None else "never",
                )
            console.print(table)
            console.print('[dim]After checking an account: culture review --done "<name>"[/dim]')
        else:
            console.print("No accounts due for review.")

        if queue["candidates"]:
            table = Table(
                title=f"Discovered candidates awaiting verification ({len(queue['candidates'])})"
            )
            table.add_column("Name")
            table.add_column("Platform")
            table.add_column("Cited by")
            table.add_column("Mentions")
            for source in queue["candidates"]:
                d = source.discovery_json
                table.add_row(
                    source.name,
                    source.platform,
                    ", ".join(d.get("citing_sources", [])[:3]),
                    str(d.get("mentions", "—")),
                )
            console.print(table)
            console.print(
                "[dim]Verify a candidate, then either edit seeds/sources.yaml to adopt it, "
                "or delete the row if it is noise.[/dim]"
            )


signals_app = typer.Typer(help="Persistent signal registry.", no_args_is_help=True)
app.add_typer(signals_app, name="signals")


@signals_app.command("list")
def signals_list() -> None:
    """Show the signal registry."""
    from culture.database import get_engine, session_scope
    from culture.services.signals import SignalService

    with session_scope(get_engine()) as session:
        signals = SignalService(session).active_signals()
        table = Table(title=f"Signal Registry ({len(signals)})")
        table.add_column("Signal", max_width=50)
        table.add_column("Stage")
        table.add_column("Items", justify="right")
        table.add_column("Sources", justify="right")
        table.add_column("Cities", max_width=30)
        table.add_column("First seen")
        table.add_column("Last evidence")
        for s in sorted(signals, key=lambda s: (s.evidence_count, s.id), reverse=True):
            table.add_row(
                s.name,
                s.lifecycle_stage,
                str(s.evidence_count),
                str(s.source_count),
                ", ".join(s.cities[:3]),
                s.first_detected_at.date().isoformat() if s.first_detected_at else "—",
                s.last_evidence_at.date().isoformat() if s.last_evidence_at else "—",
            )
        console.print(table)


@signals_app.command("update")
def signals_update(
    limit: int | None = typer.Option(None, "--limit", help="Evaluate at most N items."),
) -> None:
    """Evaluate analyzed items against the signal registry."""
    from culture.analysis.provider import ProviderError, get_provider
    from culture.database import get_engine, session_scope
    from culture.services.signals import SignalService

    try:
        provider = get_provider(get_settings())
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        stats = SignalService(session, provider).update_signals(limit)
    console.print(
        f"Items evaluated: {stats.items_processed} · evidence links: {stats.links_created} · "
        f"new signals: {stats.signals_created} · invalid links skipped: {stats.invalid_links}"
    )
    for name in stats.new_signal_names:
        console.print(f"  [green]+ {name}[/green]")
    if stats.budget_stopped:
        console.print(
            "[yellow]Stopped early: spend cap reached. Remaining items retry next run.[/yellow]"
        )
    console.print(f"Spend this run: [bold]${provider.spent_usd:.2f}[/bold]")


@app.command()
def report(
    days: int = typer.Option(7, "--days", help="Reporting window in days."),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Make this report publicly readable at /brief on the live site. "
        "Off by default — publishing is an explicit decision, not automatic.",
    ),
) -> None:
    """Generate the weekly intelligence report (roundup + cross-source synthesis)."""
    from culture.analysis.provider import (
        DEFAULT_SYNTHESIS_MODEL,
        ProviderError,
        get_provider,
    )
    from culture.database import get_engine, session_scope
    from culture.services.reporting import generate_report

    settings = get_settings()
    try:
        provider = get_provider(
            settings, model=settings.ai_synthesis_model or DEFAULT_SYNTHESIS_MODEL
        )
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        result = generate_report(
            session, provider, days=days, reports_dir=PROJECT_ROOT / "reports", publish=publish
        )

    console.print()
    console.print(f"Sources monitored: {result.sources_covered}")
    console.print(f"Content items in window: {result.items_covered}")
    if result.items_unanalyzed:
        console.print(
            f"[yellow]{result.items_unanalyzed} item(s) not yet analyzed — "
            "run `culture analyze` and regenerate for full coverage.[/yellow]"
        )
    console.print(f"Spend this run: [bold]${provider.spent_usd:.2f}[/bold] ({provider.model})")
    console.print()
    console.print(f"Weekly report generated:\n[bold]{result.path}[/bold]")
    console.print("Saved to the database for the hosted site's private report archive.")
    if result.is_public:
        console.print("[green]Public: live at /brief on the deployed site.[/green]")
    else:
        console.print("Private — rerun with --publish to make it public.")


@app.command(name="draft-post")
def draft_post() -> None:
    """Draft a public Substack essay from the latest private weekly report.

    The essay is deliberately NOT the report: one editorial story, no source
    names, no registry/scores/evidence tables — the intelligence depth stays
    on the platform. Output is a draft for human review; Substack has no
    write API, so publishing is always a manual paste."""
    import re

    from sqlalchemy import select

    from culture.analysis.provider import DEFAULT_SYNTHESIS_MODEL, ProviderError, get_provider
    from culture.analysis.synthesis_prompts import (
        PUBLIC_BRIEF_SYSTEM_PROMPT,
        build_public_draft_prompt,
    )
    from culture.database import get_engine, session_scope
    from culture.models.report import WeeklyReport
    from culture.models.source import Source

    settings = get_settings()
    try:
        provider = get_provider(
            settings, model=settings.ai_synthesis_model or DEFAULT_SYNTHESIS_MODEL
        )
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        report_row = session.scalar(
            select(WeeklyReport).order_by(WeeklyReport.iso_week.desc())
        )
        if report_row is None:
            console.print("[red]No weekly report exists yet — run `culture report` first.[/red]")
            raise typer.Exit(1)
        source_names = [s.name for s in session.scalars(select(Source)) if s.active]

    match = re.search(r"^# Part 2.*?$", report_row.content_markdown, re.MULTILINE)
    synthesis = (
        report_row.content_markdown[match.start() :] if match else report_row.content_markdown
    )

    console.print(f"Drafting public essay from {report_row.iso_week} ({provider.model})...")
    draft = provider.generate_text(
        PUBLIC_BRIEF_SYSTEM_PROMPT,
        build_public_draft_prompt(synthesis, report_row.iso_week),
        max_tokens=8000,
    )

    # Mechanical leak check on top of the prompt rules: the draft must not
    # name any monitored source. The prompt should prevent this; this catches
    # it if the model slips anyway.
    from culture.utils.leakcheck import compile_patterns, find_leaks

    leaked = find_leaks(draft, compile_patterns(source_names))

    drafts_dir = PROJECT_ROOT / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    path = drafts_dir / f"substack-{report_row.iso_week}.md"
    path.write_text(draft, encoding="utf-8")

    console.print(f"Spend this run: [bold]${provider.spent_usd:.2f}[/bold] ({provider.model})")
    console.print(f"\nDraft written to:\n[bold]{path}[/bold]")
    if leaked:
        console.print(
            f"[red]LEAK CHECK FAILED — draft names monitored source(s): "
            f"{', '.join(leaked)}. Edit these out before publishing.[/red]"
        )
    else:
        console.print("[green]Leak check passed — no monitored source names found.[/green]")
    console.print(
        "Review and edit, then paste into the Substack editor to publish — "
        "the homepage picks up published posts automatically via the feed."
    )



@app.command(name="leak-check")
def leak_check(
    base_url: str = typer.Option(
        "https://culture-intelligence.vercel.app",
        "--base-url",
        help="Deployment to scan. Public paths are taken from the auth allowlist.",
    ),
) -> None:
    """Fetch every public page and fail if any names a monitored source.

    Exit code 1 on any hit, so this can gate a deploy. Scans rendered HTML,
    not templates — generated text stored in the database is exactly what
    template review cannot catch.
    """
    import urllib.request

    from sqlalchemy import select as sa_select

    from culture.database import get_engine, session_scope
    from culture.models.source import Source
    from culture.utils.leakcheck import compile_patterns, find_leaks
    from culture.web.auth import PUBLIC_PATHS

    with session_scope(get_engine()) as session:
        names = [s.name for s in session.scalars(sa_select(Source)) if s.active]
    patterns = compile_patterns(names)

    # Auth-flow endpoints are public but not pages (some are POST-only);
    # scanning them produces 405 noise, not signal.
    skip_prefixes = ("/login", "/logout", "/auth")
    skip = {"/robots.txt", "/sitemap.xml"}
    paths = sorted(
        p for p in PUBLIC_PATHS if p not in skip and not p.startswith(skip_prefixes)
    )
    failures: dict[str, list[str]] = {}
    for path in paths:
        url = base_url.rstrip("/") + path
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 — a dead page is a finding, not a crash
            failures[path] = [f"(fetch failed: {exc})"]
            console.print(f"{path}: [red]fetch failed: {exc}[/red]")
            continue
        leaked = find_leaks(html, patterns)
        if leaked:
            failures[path] = leaked
        console.print(
            f"{path}: " + (f"[red]{', '.join(leaked)}[/red]" if leaked else "[green]clean[/green]")
        )

    if failures:
        console.print(f"\n[red]LEAK CHECK FAILED — {len(failures)} public page(s) affected.[/red]")
        raise typer.Exit(1)
    console.print(
        f"\n[green]Leak check passed: {len(paths)} public pages, "
        f"{len(patterns)} monitored names.[/green]"
    )

auth_app = typer.Typer(help="Account provisioning and migration.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


@auth_app.command("provision-owner")
def auth_provision_owner() -> None:
    """Idempotently provision the single owner account from OWNER_EMAIL.

    Safe to rerun: finds or creates the Supabase identity, then upserts the
    matching profiles row as role=owner/status=approved. Refuses to create a
    second owner (the DB's partial unique index also enforces this) — use
    the documented ownership-transfer procedure instead.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    from sqlalchemy import select

    from culture.database import get_engine, session_scope
    from culture.models.profile import ROLE_OWNER, STATUS_APPROVED, Profile
    from culture.web import auth as web_auth
    from culture.web import supabase

    settings = get_settings()
    if not settings.owner_email:
        console.print("[red]OWNER_EMAIL is not set.[/red]")
        raise typer.Exit(1)
    owner_email = settings.owner_email.strip().lower()

    try:
        user = supabase.admin_get_or_create_user(owner_email, settings)
    except supabase.SupabaseAuthError as exc:
        console.print(f"[red]Supabase error: {exc.detail}[/red]")
        raise typer.Exit(1) from exc

    user_id = UUID(user["id"])
    with session_scope(get_engine()) as session:
        other_owner = session.scalars(
            select(Profile).where(Profile.role == ROLE_OWNER, Profile.id != user_id)
        ).first()
        if other_owner is not None:
            console.print(
                f"[red]An owner already exists ({other_owner.email}). Ownership transfer "
                "is a manual procedure documented in the README, not this command.[/red]"
            )
            raise typer.Exit(1)

        profile = session.get(Profile, user_id)
        if profile is None:
            profile = Profile(id=user_id, email=owner_email)
            session.add(profile)
        now = datetime.now(UTC)
        profile.role = ROLE_OWNER
        profile.status = STATUS_APPROVED
        profile.email_verified_at = profile.email_verified_at or now
        profile.approved_at = profile.approved_at or now
        profile.approved_by = "system:provision-owner"

    web_auth.record_event("owner_provisioned", email=owner_email)
    console.print(f"[green]Owner provisioned: {owner_email}[/green]")


@auth_app.command("migrate-allowlist")
def auth_migrate_allowlist(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report only; write nothing."),
) -> None:
    """Create pending, legacy-flagged profiles for every email on the old
    magic-link allow-list (ALLOWED_EMAILS / ADMIN_EMAILS), so no legacy user
    keeps access silently. No legacy user is auto-approved — the owner
    reviews each one in /admin/approvals like any other applicant. Idempotent:
    rerunning creates nothing for emails already migrated. Run with
    --dry-run first; nothing is written until you drop it.
    """
    from sqlalchemy import select

    from culture.database import get_engine, session_scope
    from culture.models.email_log import EmailLog
    from culture.models.profile import STATUS_PENDING, Profile
    from culture.web import auth as web_auth
    from culture.web import supabase

    settings = get_settings()
    owner_email = settings.owner_email.strip().lower() if settings.owner_email else None
    legacy_emails = (settings.allowed_email_set | settings.admin_email_set) - {owner_email}
    if not legacy_emails:
        console.print("[yellow]No legacy emails to migrate.[/yellow]")
        return

    with session_scope(get_engine()) as session:
        already_migrated = {p.email for p in session.scalars(select(Profile))}
    to_migrate = sorted(legacy_emails - already_migrated)

    console.print(
        f"Legacy emails: {len(legacy_emails)}. Already migrated: "
        f"{len(legacy_emails) - len(to_migrate)}. New: {len(to_migrate)}."
    )
    for email in to_migrate:
        console.print(f"  {email}")
    if dry_run:
        console.print("[yellow]Dry run: nothing written.[/yellow]")
        return
    if not to_migrate:
        return

    migrated: list[str] = []
    for email in to_migrate:
        try:
            user = supabase.admin_get_or_create_user(email, settings)
        except supabase.SupabaseAuthError as exc:
            console.print(f"[red]{email}: Supabase error: {exc.detail}[/red]")
            continue
        from uuid import UUID

        user_id = UUID(user["id"])
        with session_scope(get_engine()) as session:
            profile = session.get(Profile, user_id)
            if profile is None:
                profile = Profile(id=user_id, email=email)
                session.add(profile)
            profile.status = STATUS_PENDING
            profile.legacy_magic_link = True
            session.add(
                EmailLog(
                    to_email=email,
                    template="activation_sent",
                    trigger_event="migrate_allowlist",
                    dedupe_key=f"activation_sent:{email}:{user_id}",
                )
            )
        web_auth.record_event("activation_sent", email=email, detail="legacy_magic_link")
        migrated.append(f"{email} ({user_id})")

    console.print(f"[green]Migrated {len(migrated)} legacy account(s):[/green]")
    for line in migrated:
        console.print(f"  {line}")
    console.print(
        "[yellow]Rollback: delete these Supabase users and their profile rows "
        "(listed above) if this run needs to be reversed.[/yellow]"
    )


if __name__ == "__main__":
    app()
