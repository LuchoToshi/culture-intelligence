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
def seed() -> None:
    """Import seed sources from seeds/sources.yaml."""
    _not_yet("Phase 2 (source registry)")


@app.command()
def sources() -> None:
    """List monitored sources."""
    _not_yet("Phase 2 (source registry)")


@app.command()
def ingest(
    source: str | None = typer.Option(None, "--source", help="Ingest a single source by name."),
) -> None:
    """Check all active supported sources and store new content."""
    _not_yet("Phase 3 (RSS) / Phase 5 (YouTube)")


@app.command()
def analyze(
    limit: int | None = typer.Option(None, "--limit", help="Analyze at most N items."),
) -> None:
    """Run AI analysis on unprocessed content."""
    _not_yet("Phase 6 (AI analysis)")


@app.command()
def report(
    days: int = typer.Option(7, "--days", help="Reporting window in days."),
) -> None:
    """Generate the weekly intelligence report."""
    _not_yet("Phase 7 (weekly report)")


if __name__ == "__main__":
    app()
