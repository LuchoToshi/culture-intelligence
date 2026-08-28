from culture.utils.identifiers import normalize_identifier


def test_instagram_handle_and_url_collide():
    variants = ["@DesfileDiario", "desfilediario", "instagram.com/desfilediario/",
                "https://www.instagram.com/DesfileDiario"]
    normalized = {normalize_identifier("instagram", v) for v in variants}
    assert len(normalized) == 1


def test_tiktok_and_x_use_the_same_handle_scheme():
    assert normalize_identifier("tiktok", "@user") == normalize_identifier("tiktok", "user")
    assert normalize_identifier("x", "@user").startswith("x:")


def test_youtube_channel_url_resolves_to_channel_id():
    channel_id = "UC" + "a" * 22
    by_url = normalize_identifier("youtube", f"https://youtube.com/channel/{channel_id}")
    by_id = normalize_identifier("youtube", channel_id)
    assert by_url == by_id == f"youtube:{channel_id}"


def test_youtube_handle_url_without_channel_id_falls_back_to_url_normalization():
    a = normalize_identifier("youtube", "https://youtube.com/@somechannel")
    b = normalize_identifier("youtube", "https://youtube.com/@somechannel/")
    assert a == b


def test_web_source_reuses_url_normalization_for_tracking_params_and_trailing_slash():
    a = normalize_identifier("web", "https://example.com/feed/?utm_source=x")
    b = normalize_identifier("web", "https://example.com/feed")
    assert a == b


def test_empty_value_normalizes_to_empty_string():
    assert normalize_identifier("web", "") == ""
    assert normalize_identifier("instagram", "   ") == ""


def test_different_platforms_do_not_collide():
    assert normalize_identifier("instagram", "user") != normalize_identifier("tiktok", "user")
