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
    from culture.models.content import ExtractionStatus, ProcessingStatus, TranscriptStatus
    from culture.models.source import Platform

    settings = get_settings()
    try:
        engine = get_engine()
        with session_scope(engine) as session:
            total_sources = session.scalar(select(func.count(Source.id))) or 0
            active_sources = session.scalar(
                select(func.count(Source.id)).where(Source.active.is_(True))
            ) or 0
            supported_sources = session.scalar(
                select(func.count(Source.id)).where(
                    Source.active.is_(True),
                    Source.platform.in_([Platform.WEB.value, Platform.YOUTUBE.value]),
                    Source.feed_url.is_not(None),
                )
            ) or 0
            last_ingestion = session.scalar(select(func.max(Source.last_checked_at)))
            total_items = session.scalar(select(func.count(ContentItem.id))) or 0
            new_items = session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.processing_status == ProcessingStatus.NEW.value
                )
            ) or 0
            unanalyzed = session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.processing_status.in_(
                        [ProcessingStatus.NEW.value, ProcessingStatus.READY.value]
                    )
                )
            ) or 0
            failed_extractions = session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.extraction_status == ExtractionStatus.FAILED.value
                )
            ) or 0
            failed_transcripts = session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.transcript_status == TranscriptStatus.FAILED.value
                )
            ) or 0
            failed_analyses = session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.processing_status == ProcessingStatus.FAILED.value
                )
            ) or 0
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
    console.print(f"Duplicates skipped: {stats.total_duplicates}")
    console.print()
    console.print(f"Articles extracted: {stats.total_extracted}")
    console.print(f"Partial extractions: {stats.total_partial}")
    console.print(f"Extraction failures: {stats.total_extraction_failed}")
    if stats.total_new_videos or stats.total_transcripts_available or stats.total_transcripts_recovered:
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
) -> None:
    """Run AI analysis on unprocessed content (retries earlier failures)."""
    from culture.analysis.item_analyzer import ItemAnalyzer
    from culture.analysis.provider import ProviderError, get_provider
    from culture.database import get_engine, session_scope

    try:
        provider = get_provider(get_settings())
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    with session_scope(get_engine()) as session:
        analyzer = ItemAnalyzer(session, provider)
        pending = len(analyzer.pending_items())
        if pending == 0:
            console.print("Nothing to analyze — all content is processed.")
            return
        todo = min(pending, limit) if limit else pending
        console.print(
            f"Analyzing {todo} of {pending} pending items with "
            f"{provider.name}/{provider.model}..."
        )
        stats = analyzer.analyze_pending(limit)
        remaining = len(analyzer.pending_items())

    console.print()
    console.print(f"Analyzed: {stats.analyzed}")
    console.print(f"Failed: {stats.failed}")
    for failure in stats.failures[:10]:
        console.print(f"  [red]- {failure}[/red]")
    console.print(f"Remaining unanalyzed: {remaining}")


@app.command()
def report(
    days: int = typer.Option(7, "--days", help="Reporting window in days."),
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
            session, provider, days=days, reports_dir=PROJECT_ROOT / "reports"
        )

    console.print()
    console.print(f"Sources monitored: {result.sources_covered}")
    console.print(f"Content items in window: {result.items_covered}")
    if result.items_unanalyzed:
        console.print(
            f"[yellow]{result.items_unanalyzed} item(s) not yet analyzed — "
            "run `culture analyze` and regenerate for full coverage.[/yellow]"
        )
    console.print()
    console.print(f"Weekly report generated:\n[bold]{result.path}[/bold]")


if __name__ == "__main__":
    app()
