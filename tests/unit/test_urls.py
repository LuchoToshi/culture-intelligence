import pytest

from culture.utils.urls import normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # tracking parameters stripped
        (
            "https://example.com/story?utm_source=x&utm_medium=social&utm_campaign=a&utm_content=b&utm_term=c",
            "https://example.com/story",
        ),
        ("https://example.com/story?fbclid=abc123", "https://example.com/story"),
        ("https://example.com/story?gclid=abc123", "https://example.com/story"),
        # meaningful params preserved, tracking removed, order kept
        (
            "https://example.com/articles?page=2&utm_source=rss&format=rss",
            "https://example.com/articles?page=2&format=rss",
        ),
        # trailing slash and fragment
        ("https://example.com/story/", "https://example.com/story"),
        ("https://example.com/story#comments", "https://example.com/story"),
        ("https://example.com/", "https://example.com/"),
        # case and default ports
        ("HTTPS://Example.COM/Story", "https://example.com/Story"),
        ("https://example.com:443/story", "https://example.com/story"),
        ("http://example.com:80/story", "http://example.com/story"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalization_is_idempotent():
    url = "https://example.com/story?a=1&utm_source=x"
    assert normalize_url(normalize_url(url)) == normalize_url(url)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # genuinely different URLs must never merge
        ("https://example.com/story?page=1", "https://example.com/story?page=2"),
        ("https://example.com/story", "https://example.com/Story"),
        ("https://example.com/story", "https://other.com/story"),
        ("https://example.com/story-one", "https://example.com/story-two"),
    ],
)
def test_distinct_urls_stay_distinct(a, b):
    assert normalize_url(a) != normalize_url(b)
