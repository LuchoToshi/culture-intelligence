"""Regression guard: the em dash (—, U+2014) never appears anywhere a person
can read it — page templates, rendered pages, seed data, or the copy the
pipeline generates. Draft requirement, 2026-08-27: replace every em dash with
a hyphen, colon, comma or sentence break; keep it from coming back.

Three layers, matching where an em dash could be reintroduced:
1. Template source — static English copy authored directly in the .html files.
   Jinja `{# ... #}` comments are stripped first: they never reach the browser,
   so they are developer notes, not UI content, and are exempt.
2. Rendered pages — catches em dashes coming from Python string literals or
   seeded/generated data that a static template scan can't see.
3. The LLM system prompts that author user-facing copy — a lightweight check
   that the "never use an em dash" instruction is still present, so the rule
   can't be silently deleted later.

Deliberately out of scope: Python source comments/docstrings and CSS comments.
They are developer-facing text that never renders to a user, so they aren't
"UI, content, or generated output" — see design/README-REPO.md and the
project's engineering conventions on not touching what doesn't need touching.
"""

import re
from pathlib import Path

from test_web import client  # noqa: F401  (shared fixture, not reimplemented here)

from culture.analysis.prompts import ITEM_SYSTEM_PROMPT
from culture.analysis.signal_prompts import SIGNAL_SYSTEM_PROMPT
from culture.analysis.synthesis_prompts import WEEKLY_SYSTEM_PROMPT
from culture.web.app import TEMPLATES_DIR

EM_DASH = "—"
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)


def _visible_template_text(path: Path) -> str:
    """Template source with Jinja comments stripped — what a browser could
    ever actually receive is a superset of this, so a violation here is
    always a real one."""
    return JINJA_COMMENT.sub("", path.read_text(encoding="utf-8"))


def test_no_em_dash_in_template_source():
    offenders = {}
    for path in sorted(TEMPLATES_DIR.glob("*.html")):
        text = _visible_template_text(path)
        if EM_DASH in text:
            lines = [i + 1 for i, line in enumerate(text.splitlines()) if EM_DASH in line]
            offenders[path.name] = lines
    assert not offenders, f"Em dash found in template source: {offenders}"


def test_no_em_dash_in_seed_data():
    seeds_path = Path(__file__).resolve().parents[2] / "seeds" / "sources.yaml"
    text = seeds_path.read_text(encoding="utf-8")
    lines = [i + 1 for i, line in enumerate(text.splitlines()) if EM_DASH in line]
    assert not lines, f"Em dash found in seeds/sources.yaml at lines: {lines}"


def test_no_em_dash_in_rendered_pages(client):  # noqa: F811
    routes = [
        "/", "/intelligence", "/brief", "/about", "/the-brief", "/methodology",
        "/dashboard", "/signals", f"/signals/{client.ids['signal']}",
        "/taste-systems", "/archetypes", "/cities", "/cities/compare",
        f"/items/{client.ids['item']}", "/search", "/search?q=workwear",
        "/stream", "/sources", "/reports", "/reports/2026-W35",
        "/login",
    ]
    offenders = {}
    for route in routes:
        response = client.get(route)
        if EM_DASH in response.text:
            offenders[route] = response.text.count(EM_DASH)
    assert not offenders, f"Em dash found in rendered output: {offenders}"


def test_no_em_dash_in_error_page(client):  # noqa: F811
    response = client.get("/signals/999999")
    assert response.status_code == 404
    assert EM_DASH not in response.text


def test_markdown_rendering_never_enables_smart_punctuation():
    """python-markdown's `smarty` extension rewrites plain hyphens into en/em
    dashes. Report rendering must never enable it — this is the one place a
    typography setting could silently reintroduce the character we just
    removed everywhere else."""
    app_py = Path(__file__).resolve().parents[2] / "src" / "culture" / "web" / "app.py"
    app_source = app_py.read_text(encoding="utf-8")
    for match in re.finditer(r"md\.markdown\([^)]*\)", app_source, re.DOTALL):
        assert "smarty" not in match.group(0), (
            "markdown.markdown() call enables the smarty extension, which "
            "would reintroduce em dashes via smart punctuation"
        )


def test_report_generation_source_has_no_em_dash():
    """The deterministic parts of the weekly report (source roundup, registry
    appendix, change log) are plain Python string literals, not LLM output,
    so the prompt-level ban can't reach them. A hardcoded em dash here
    reappears in every future report regardless of what the model does."""
    reporting_py = (
        Path(__file__).resolve().parents[2] / "src" / "culture" / "services" / "reporting.py"
    )
    offenders = [
        i + 1
        for i, line in enumerate(reporting_py.read_text(encoding="utf-8").splitlines())
        if EM_DASH in line and not line.strip().startswith("#")
    ]
    assert not offenders, f"Em dash found in reporting.py at lines: {offenders}"


def test_generation_prompts_forbid_the_em_dash():
    """The three LLM call sites that author copy which ends up in the UI
    (item analysis, signal naming, weekly synthesis) must each carry an
    explicit instruction never to use an em dash. This doesn't verify what
    a model actually returns — only that the guardrail text hasn't been
    quietly removed from the prompt that is supposed to prevent it."""
    for name, prompt in [
        ("item analysis", ITEM_SYSTEM_PROMPT),
        ("signal naming", SIGNAL_SYSTEM_PROMPT),
        ("weekly synthesis", WEEKLY_SYSTEM_PROMPT),
    ]:
        assert EM_DASH in prompt, f"{name} prompt should quote the character it bans"
        assert "em dash" in prompt.lower(), f"{name} prompt is missing the em dash ban"
