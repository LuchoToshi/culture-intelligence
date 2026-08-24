from culture.utils.hashing import content_hash


def test_hash_is_deterministic():
    assert content_hash("Title", "Body text") == content_hash("Title", "Body text")


def test_hash_ignores_whitespace_and_case_noise():
    assert content_hash("Title", "Body   text\n\nhere") == content_hash("title", "body text here")


def test_different_content_different_hash():
    assert content_hash("Title", "Body A") != content_hash("Title", "Body B")
    assert content_hash("Title A", "Body") != content_hash("Title B", "Body")


def test_no_text_means_no_hash():
    assert content_hash("Title only", None) is None
    assert content_hash("Title only", "   ") is None
