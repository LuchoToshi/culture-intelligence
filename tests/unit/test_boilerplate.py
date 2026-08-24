from culture.models.content import ContentItem
from culture.models.source import Source
from culture.services.boilerplate import (
    clean_text,
    cleaned_text_for,
    find_boilerplate,
)

UPSELL = "The essential daily round-up of fashion news, analysis, and breaking news alerts."
SIGNIN = (
    "Please sign in to ensure you can read our agenda-setting intelligence, analysis and advice."
)
AFFILIATE = (
    "We may receive a commission from your purchase through affiliate marketing partnerships."
)


def article(body: str, n: int) -> str:
    return (
        f"{SIGNIN}\n{UPSELL}\n"
        f"Real reporting paragraph number {n} about a designer collection with substance.\n"
        f"More unique analysis in article {n} that only this article contains.\n"
        f"{AFFILIATE}"
    )


def test_repeated_paragraphs_detected_across_documents():
    bodies = [article(f"body {i}", i) for i in range(8)]
    boilerplate = find_boilerplate(bodies)
    assert UPSELL in boilerplate
    assert SIGNIN in boilerplate
    assert AFFILIATE in boilerplate
    assert not any("Real reporting" in p for p in boilerplate)


def test_clean_text_strips_only_boilerplate():
    bodies = [article(f"body {i}", i) for i in range(8)]
    boilerplate = find_boilerplate(bodies)
    cleaned = clean_text(bodies[0], boilerplate)
    assert UPSELL not in cleaned
    assert SIGNIN not in cleaned
    assert "Real reporting paragraph number 0" in cleaned
    assert "only this article contains" in cleaned


def test_small_corpus_detects_nothing():
    bodies = [article("a", 1), article("b", 2)]  # below MIN_ITEMS
    assert find_boilerplate(bodies) == set()


def test_short_repeated_lines_are_left_alone():
    # e.g. an interviewer label repeated across articles — too short to strip
    bodies = [f"sabukaru:\nUnique long-form answer with substance, number {i}." for i in range(10)]
    assert find_boilerplate(bodies) == set()


def test_unique_content_never_flagged():
    bodies = [
        f"Completely unique article body {i} with its own long analysis of the scene in city {i}."
        for i in range(10)
    ]
    assert find_boilerplate(bodies) == set()


def test_cleaned_text_for_uses_source_corpus(session):
    source = Source(name="Paywalled Pub", platform="web", feed_url="https://x.example/feed")
    session.add(source)
    session.commit()
    for i in range(6):
        session.add(
            ContentItem(
                source_id=source.id,
                url=f"https://x.example/a{i}",
                content_type="article",
                raw_text=article(f"b{i}", i),
            )
        )
    session.commit()

    item = session.query(ContentItem).first()
    cleaned = cleaned_text_for(session, item)
    assert SIGNIN not in cleaned
    assert "Real reporting" in cleaned
    assert SIGNIN in item.raw_text  # stored text untouched


def test_cleaned_text_for_item_without_text(session):
    source = Source(name="Pub", platform="web")
    session.add(source)
    session.commit()
    item = ContentItem(source_id=source.id, url="https://x.example/a", content_type="article")
    session.add(item)
    session.commit()
    assert cleaned_text_for(session, item) is None
