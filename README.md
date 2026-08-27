# Culture Intelligence

A fashion-first urban taste intelligence system: the intelligence layer between fashion scenes and fashion markets (see [POSITIONING.md](POSITIONING.md) — the verbatim north star). It continuously collects fashion, culture and lifestyle signals, analyzes them, accumulates them into **persistent cultural signals**, and produces weekly intelligence reports.

This is **not** a news aggregator or a trend dashboard. The system distinguishes editorial attention from social attention, social attention from real-world adoption, and adoption from commercial success — and tracks how signals form, who adopts them, where they emerge, how they spread between cities, and when they saturate.

## Current scope (V0 complete + signal layer + web UI)

| Phase | Status |
|---|---|
| 1. Foundation: project, config, database, models, migrations, CLI skeleton | ✅ done |
| 2. Source registry + seed import | ✅ done |
| 3. RSS collector + extraction + dedup (single publication) | ✅ done |
| 4. Multi-publication ingestion + resilience | ✅ done |
| 5. YouTube ingestion + transcripts | ✅ done |
| 6. AI provider abstraction + item analysis | ✅ done |
| 7. Weekly report (roundup + synthesis) | ✅ done |
| 8. Quality pass | ✅ done |

## Architecture

A synchronous CLI pipeline: `seed → ingest → analyze → report`.

- **Sources** are data, not code. Every source row carries platform, tier (`candidate/watch/core/dormant`), geography, and collection configuration. Only `web` (RSS) and `youtube` will be active collectors in V0.
- **Collectors** (Phase 3+) normalize all platforms into one intermediate schema; ingestion doesn't care where an item came from.
- **Raw content is never overwritten by AI output.** `content_items` holds what the source said; `content_analyses` holds what our system inferred, stamped with model, provider and version.
- **Dedup is enforced by the database**, not just application logic: unique constraints on `(source_id, external_id)` and `(source_id, normalized url)`.
- Status fields (`extraction_status`, `transcript_status`, `processing_status`) make every failure explicit and retryable. One failing source never kills a run.

## Technology

Python 3.11+ · uv · PostgreSQL · SQLAlchemy 2 · Alembic · Pydantic / pydantic-settings · Typer · Rich. Later phases add httpx, feedparser, trafilatura, yt-dlp, and the Anthropic/OpenAI SDKs.

## Design system

The web UI implements the **"briefing room"** design system, designed in Claude Design and
exported to [design/](design/) — the design source of truth (brand rules in
[design/readme.md](design/readme.md), repo conventions in
[design/README-REPO.md](design/README-REPO.md)).

- **Tokens** — `--ci-*` CSS custom properties (ink scale, oxblood accent, hairline rules,
  Newsreader + Archivo type scale, spacing). Canonical: `design/tokens/`. Runtime copy:
  `src/culture/web/templates/_tokens.css`, `{% include %}`d by `base.html`, `login.html`,
  `error.html`. Change tokens in both places or the next design sync will disagree.
- **Components** — Jinja2 macros in `src/culture/web/templates/_macros.html`
  (`stage_chip`, `score_meter`, `divergence`, `tier_badge`, `health_mark`, `stat_block`,
  `alert_band`, `signal_table`, `evidence_record`), each implementing the matching
  reference component in `design/components/core/` (React `.jsx` + `.d.ts` +
  `.prompt.md`). The product runs no JavaScript framework; the `.jsx` files are the
  visual/API reference and the future `/design-sync` input.
- **Adding a component**: start from its reference in `design/components/core/` (or add
  one there first), implement it as a macro using only `--ci-*` tokens, and give it the
  accessibility the reference lacks (real heading elements, `aria-hidden` decorative
  marks, labeled meters).
- **Prohibited** (from the design charter): gradients, shadows, rounded corners (7px
  stage dots excepted), icon fonts/SVG icons/emoji (unicode glyphs only: `†` `●` `○`
  `▾` `·`), pulsing or count-up animations, a second styling system, oxblood for
  anything but signal state and emphasis. Hard-coded colors in templates are a bug —
  use tokens.
- There is **no build step**: styles ship inline in the Jinja templates. No `dist/` is
  produced or needed. Reference screens live in `design/reference/*.dc.html` (Claude
  Design artboards with demo data — open in a browser to compare fidelity).

## Setup

### 1. PostgreSQL

```bash
brew install postgresql@16
brew services start postgresql@16
createdb culture_intelligence
```

### 2. Environment

```bash
cp .env.example .env
```

Set `DATABASE_URL` (default works for a local Homebrew Postgres):

```
DATABASE_URL=postgresql+psycopg://localhost:5432/culture_intelligence
```

AI keys are not needed until Phase 6.

### 3. Install

```bash
uv sync
```

> **⚠️ iCloud-synced folders (macOS):** if this repo lives under `~/Documents` with iCloud Drive sync enabled, do **not** keep the virtualenv inside the repo. iCloud marks files inside dot-directories as hidden and can evict their contents; CPython skips hidden `.pth` files, which silently breaks the editable install. Put the venv outside iCloud and symlink it before running `uv sync`:
>
> ```bash
> mkdir -p ~/.venvs/culture-intelligence
> ln -s ~/.venvs/culture-intelligence .venv
> uv sync
> ```

### 4. Database schema

```bash
uv run culture db init
```

Applies all Alembic migrations. Safe to run repeatedly.

## Usage

Working today:

```bash
uv run culture db init    # apply migrations
uv run culture seed       # import seeds/sources.yaml (idempotent, matches by name)
uv run culture sources    # list all sources with tier/active/feed state
uv run culture status     # system health: sources, content, backlog
uv run culture ingest     # collect new content from all active sources (RSS + YouTube)
uv run culture ingest --source "Sabukaru"   # single source
```

Ingestion normalizes URLs (tracking params, fragments, trailing slashes), extracts article text with trafilatura, records extraction status per item, and skips duplicates via DB-enforced constraints plus canonical-URL matching. A failing source never stops the run; sources without a verified feed are skipped visibly. Page fetches are rate-limited (0.5s between requests per run).

**YouTube**: channels are collected via their public Atom feeds (video ID, title, publication date, feed description). Each new video is enriched with yt-dlp metadata (duration, chapters, view count, full description) and a transcript via youtube-transcript-api, preferring English but accepting any language. Transcript availability is stored explicitly per video (`available` / `unavailable` / `failed`) — a transcript or metadata failure never blocks storing the video, and analysis will never pretend to know what an untranscribed video says. Videos dedup by video ID, so the same video arriving as `/shorts/<id>` and `watch?v=<id>` stores once.

**Boilerplate handling**: stored `raw_text` is exactly what the extractor produced — never modified. Site furniture that repeats across a source's articles (paywall prompts, newsletter upsells, "latest posts" widgets, affiliate disclaimers) is detected per source by cross-document paragraph frequency and stripped at consumption time (`services/boilerplate.py`), so detection improves as the corpus grows and stored data can never be corrupted by a cleaning bug.

```bash
uv run culture analyze             # AI analysis of all pending items
uv run culture analyze --limit 10  # bounded batch
```

Analysis uses structured outputs (`client.messages.parse` with a Pydantic schema), stores one `content_analyses` row per run stamped with provider/model/version, keeps source facts separate from system interpretations, and marks failures for automatic retry on the next run. Set `ANTHROPIC_API_KEY` in `.env`; model defaults to `claude-opus-5`, override with `AI_MODEL`.

```bash
uv run culture web               # local read-only web UI at http://127.0.0.1:8765
uv run culture signals list      # persistent signal registry
uv run culture signals update    # evaluate analyzed items against the registry
uv run culture report --days 7   # weekly report -> reports/<year>-W<week>.md
```

**The signal layer** is the core asset: items come and go weekly, signals persist. Each analyzed item is evaluated (LLM-assisted, high bar, fashion-anchored) against the registry — it either becomes evidence for an existing signal or founds a new one. Signal aggregates (evidence count, distinct sources, cities, score averages) are recomputed deterministically from evidence, and lifecycle stages come from a transparent heuristic (`compute_lifecycle`), never from weekly re-assertion. `culture analyze` runs signal matching automatically after item analysis.

The report has two parts. **Part 1** is a deterministic roundup rendered straight from the database: every active source appears (silent ones say "No new content found during this period"), each item with its full analysis, entities, signals, lifecycle, and any extraction/transcript limitation. **Part 2** is cross-source synthesis by the LLM — executive brief, emerging/strengthening signals, cross-source patterns, watch lists, city intelligence, saturation radar, commercial reality check, contradictions, week-over-week changes (the previous report is fed back in), and falsifiable hypotheses. Synthesis runs on `AI_SYNTHESIS_MODEL` (default `claude-opus-5`), separate from the per-item model.

### Seed file

[seeds/sources.yaml](seeds/sources.yaml) holds the seed universe: 13 publications, 7 YouTube channels, 19 Instagram accounts/discovery candidates. Every feed URL and YouTube channel ID in it was verified against a live response before being written down — sources where no feed could be verified (SSENSE Editorial, 032c, Throwing Fits) have blank collection fields and an explanatory note instead of invented configuration. Re-running `culture seed` updates seed-owned fields only; operational state (last checked, etc.) is never touched.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

`DATABASE_URL` from the environment/.env always wins over `alembic.ini`.

## Testing

```bash
uv run pytest
```

Unit tests run against in-memory SQLite (JSONB columns degrade to JSON via a type variant); nothing touches the network or the real database. PostgreSQL-specific behavior belongs in `tests/integration/`.

## Known limitations

- **YouTube rate-limits transcript fetching aggressively.** A large first-run backlog will trip an IP block partway through; affected videos are stored with `transcript_status: failed` and every subsequent `culture ingest` retries them (4s spacing, circuit breaker after 3 consecutive failures). The backlog recovers over a few runs once the block lifts.
- Business of Fashion is paywalled — extraction yields ~500-char teasers, honest but thin. Analysis must treat BoF items as headline-level evidence.
- Enum-like fields are stored as plain strings by design (vocabulary can evolve without migrations); invalid values are caught at the application layer, not by the database.
- Instagram/TikTok/X/Substack/Reddit/podcast sources are stored as records only; no collectors exist for them in V0 by design.

## Roadmap

V0: the pipeline above. V1: source discovery + source/content quality scoring. V2: more platforms, source graph, persistent signal database, historical intelligence. V3: client-specific networks and a UI. See the project specification for detail.
