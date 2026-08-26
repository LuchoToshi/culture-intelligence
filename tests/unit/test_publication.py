import pytest

from culture.web.publication import PublicationPost, _parse_feed, latest_posts

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Urban Taste Intelligence</title>
    <item>
      <title>The scene before the trend</title>
      <link>https://example.substack.com/p/the-scene-before-the-trend</link>
      <description>Why cultural movement is visible long before it gets a name.</description>
      <pubDate>Mon, 25 Aug 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Polyester is a moral category now</title>
      <link>https://example.substack.com/p/polyester</link>
      <description><![CDATA[<p>Fibre content as a <b>trust</b> language.</p>]]></description>
      <pubDate>Mon, 18 Aug 2026 09:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Malformed entry, no link</title>
      <link></link>
    </item>
    <item>
      <title>Fourth post beyond the limit</title>
      <link>https://example.substack.com/p/fourth</link>
    </item>
  </channel>
</rss>
"""


def test_parse_feed_extracts_posts_in_order():
    posts = _parse_feed(SAMPLE_FEED, limit=3)
    assert [p.title for p in posts] == [
        "The scene before the trend",
        "Polyester is a moral category now",
        "Fourth post beyond the limit",  # malformed entry skipped, limit still honored
    ]
    assert posts[0].published_at is not None
    assert posts[0].published_at.strftime("%d-%m-%Y") == "25-08-2026"


def test_parse_feed_strips_html_from_excerpts():
    posts = _parse_feed(SAMPLE_FEED, limit=3)
    assert posts[1].excerpt == "Fibre content as a trust language."


def test_parse_feed_rejects_dtd():
    hostile = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "y">]><rss><channel></channel></rss>'
    with pytest.raises(ValueError, match="DTD"):
        _parse_feed(hostile, limit=3)


def test_latest_posts_empty_when_unconfigured():
    assert latest_posts("") == []


def test_latest_posts_survives_unreachable_feed():
    # A dead feed URL must return [] (section hidden), never raise.
    assert latest_posts("https://127.0.0.1:1/feed") == []


def test_post_dataclass_shape():
    post = PublicationPost(title="t", url="u", published_at=None, excerpt="e")
    assert (post.title, post.url, post.excerpt) == ("t", "u", "e")
