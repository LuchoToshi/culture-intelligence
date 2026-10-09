"""Side-by-side quality check before switching text-only item analysis to a new model.

Runs the same recent text-only items through the current cheap model and a candidate,
live, and writes both analyses next to each other as Markdown. Read-only: nothing is
written to the database. Costs roughly $0.30 for 20 items (mostly the Haiku 4.5 side).

    SUPABASE_DATABASE_URL="" uv run python scripts/compare_cheap_model.py --out compare.md
"""

from pathlib import Path

import typer
from sqlalchemy import select

from culture.analysis.prompts import ITEM_SYSTEM_PROMPT, build_item_prompt
from culture.analysis.provider import DEFAULT_CHEAP_MODEL, AnthropicProvider, _anthropic_api_key
from culture.config import get_settings
from culture.database import get_engine, session_scope
from culture.models.content import ContentItem, ProcessingStatus
from culture.models.source import Source
from culture.schemas.analysis import ItemAnalysisResponse
from culture.services.boilerplate import cleaned_text_for


def _render(r: ItemAnalysisResponse) -> list[str]:
    entities = ", ".join(f"{e.name} ({e.type.value})" for e in r.entities) or "none"
    s = r.scores
    return [
        f"**Summary:** {r.summary}",
        f"**Why it matters:** {r.why_it_matters}",
        f"**Entities ({len(r.entities)}):** {entities}",
        f"**Possible signals:** {'; '.join(r.possible_signals) or 'none'}",
        f"**Tags:** {', '.join(r.tags)}",
        f"**Lifecycle:** {r.lifecycle_stage.value if r.lifecycle_stage else 'none'} · "
        f"origin {s.cultural_origin}, momentum {s.editorial_momentum}, "
        f"adoption {s.urban_adoption}, saturation {s.saturation_risk}",
    ]


def main(
    candidate: str = typer.Option("claude-haiku-5-5", help="Model to evaluate."),
    effort: str = typer.Option("low", help="output_config.effort for the candidate."),
    n: int = typer.Option(20, help="Number of recent text-only items."),
    out: Path = typer.Option(Path("compare.md"), help="Markdown output path."),
) -> None:
    settings = get_settings()
    key = _anthropic_api_key(settings) or None
    baseline = AnthropicProvider(model=DEFAULT_CHEAP_MODEL, api_key=key)
    trial = AnthropicProvider(model=candidate, api_key=key, effort=effort or None)

    lines = [f"# {DEFAULT_CHEAP_MODEL} vs {candidate} (effort {effort or 'default'})", ""]
    entity_totals = {baseline.model: 0, trial.model: 0}
    with session_scope(get_engine()) as session:
        rows = session.scalars(
            select(ContentItem)
            .where(ContentItem.processing_status == ProcessingStatus.ANALYZED.value)
            .order_by(ContentItem.id.desc())
            .limit(500)
        )
        items = [i for i in rows if not i.metadata_json.get("images")][:n]
        for item in items:
            source = session.get(Source, item.source_id)
            prompt = build_item_prompt(
                source, item, cleaned_text_for(session, item), has_images=False
            )
            lines += [f"## [{item.id}] {item.title or item.url}", f"Source: {source.name}", ""]
            for provider in (baseline, trial):
                result = provider.generate_structured(
                    ITEM_SYSTEM_PROMPT, prompt, ItemAnalysisResponse
                )
                entity_totals[provider.model] += len(result.entities)
                lines += [f"### {provider.model}", *_render(result), ""]
            typer.echo(f"done [{item.id}]")

    summary = [
        "| Model | Cost for these items | Entities found |",
        "|---|---|---|",
        *(
            f"| {p.model} | ${p.spent_usd:.3f} | {entity_totals[p.model]} |"
            for p in (baseline, trial)
        ),
        "",
    ]
    out.write_text("\n".join(lines[:2] + summary + lines[2:]))
    costs = " · ".join(f"{p.model} ${p.spent_usd:.3f}" for p in (baseline, trial))
    typer.echo(f"wrote {out} · {costs}")


if __name__ == "__main__":
    typer.run(main)
