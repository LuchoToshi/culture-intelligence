from culture.services.images import og_image_from_html, youtube_thumbnail
from culture.web.report_layout import _match_image, parse_report

REPORT = """# Cultural Intelligence Report: 2026-W47

_Window: 2026-11-10 to 2026-11-17 · 56 monitored sources · 555 content items_

# Part 1. Complete Source Roundup

## Hypebeast
### A launch

# Part 2. Weekly Cultural Intelligence

# Cross-Source Cultural Intelligence. Week of 10 to 16 November 2026

**Evidence quality note.** Mostly publications this week.

## Executive Brief

1. **Rimowa and Margiela sell wear as a feature.** The Classic Cabin gets cracking paint.

2. **Vinted is built like a feed.** Dazed reported one in four Britons use it.

---

## Emerging Signals
**Kallmeyer.** A bootstrapped label took a tennis partnership.

**Onitsuka Tiger.** Spun off in January 2027.

## What to Watch Next

1. **A second craft dispute within six weeks.** Watch BoF and Dazed.
2. **Vinted regulation talk.** UK only.

# Part 3. Signal Registry

| Signal | Stage |
|---|---|
"""


def test_parse_report_structure():
    parsed = parse_report(REPORT)
    assert parsed.window[0].isoformat() == "2026-11-10"
    assert parsed.week_title.startswith("Cross-Source Cultural Intelligence")
    assert [b.headline for b in parsed.brief] == [
        "Rimowa and Margiela sell wear as a feature.",
        "Vinted is built like a feed.",
    ]
    assert "cracking paint" in parsed.brief[0].body_text
    assert [e.name for e in parsed.sections["Emerging Signals"].entries] == [
        "Kallmeyer",
        "Onitsuka Tiger",
    ]
    assert [h.name for h in parsed.hypotheses] == [
        "A second craft dispute within six weeks",
        "Vinted regulation talk",
    ]
    assert "Evidence quality note" in parsed.intro_html
    assert parsed.appendix_md.startswith("# Part 1")
    assert "Signal Registry" not in "".join(s.html for s in parsed.sections.values())


def test_unstructured_report_parses_without_brief():
    parsed = parse_report("# Notes\n\nJust prose, no sections.")
    assert parsed.brief == [] and parsed.hypotheses == [] and parsed.appendix_md is None


def test_image_needs_two_named_matches():
    card = {"id": 1, "image": "https://x/i.jpg", "source": "Hypebeast"}
    one_name = [(card, {"Rimowa"})]
    assert _match_image("Rimowa paints a suitcase", one_name, set()) is None
    two_names = [(card, {"Rimowa", "Maison Margiela"})]
    assert _match_image("Rimowa and Maison Margiela paint a case", two_names, set()) == card
    # whole words only: "Rimowa" must not match inside another word
    assert _match_image("Rimowas and Maison Margielas", two_names, set()) is None


def test_image_helpers():
    assert (
        youtube_thumbnail("https://www.youtube.com/shorts/IDyIDZwYZXI")
        == "https://i.ytimg.com/vi/IDyIDZwYZXI/hqdefault.jpg"
    )
    assert youtube_thumbnail("https://example.com/a") is None
    page = (
        '<meta property="og:image" content="//cdn.example.com/a.jpg">'
        '<meta name="twitter:image" content="/logo.png">'
    )
    assert og_image_from_html(page, "https://example.com/post") == "https://cdn.example.com/a.jpg"
    assert (
        og_image_from_html(
            '<meta property="og:image" content="/img/site-logo.png">', "https://e.com/x"
        )
        is None
    )
