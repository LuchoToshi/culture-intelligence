from datetime import UTC, datetime

import pytest

from culture.models.content import ContentItem
from culture.models.source import Source
from culture.schemas.analysis import ItemAnalysisResponse
from culture.services import intake
from culture.services.intake import IntakeError, add_post, infer_source, load_item_images


@pytest.fixture(autouse=True)
def media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "MEDIA_DIR", tmp_path / "media")
    return tmp_path / "media"


@pytest.fixture
def ig_source(session):
    source = Source(
        name="Nolita Dirtbag",
        platform="instagram",
        url="https://www.instagram.com/nolitadirtbag",
        tier="watch",
        active=False,
    )
    session.add(source)
    session.commit()
    return source


def make_image(tmp_path, name="shot.png"):
    path = tmp_path / name
    # 1x1 transparent PNG
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c626001000000ffff03000006000557bfabd40000000049454e44ae426082"
        )
    )
    return path


def test_add_post_infers_source_from_url(session, ig_source, tmp_path):
    image = make_image(tmp_path)
    item = add_post(
        session,
        "https://www.instagram.com/nolitadirtbag/p/Cxyz123/?igsh=track",
        caption="downtown guys discovering barbour",
        image_path=image,
        published=datetime(2026, 8, 20, tzinfo=UTC),
    )

    assert item.source_id == ig_source.id
    assert item.content_type == "post"
    assert item.url == "https://www.instagram.com/nolitadirtbag/p/Cxyz123"  # normalized
    assert item.description == "downtown guys discovering barbour"
    assert item.processing_status == "ready"
    assert item.metadata_json["manual_intake"] is True
    stored = item.metadata_json["images"][0]
    assert stored["media_type"] == "image/png"
    assert len(stored["sha256"]) == 64
    images = load_item_images(item)
    assert images[0][0] == "image/png"
    assert len(images[0][1]) > 0


def test_add_post_caption_only(session, ig_source):
    item = add_post(
        session,
        "https://www.instagram.com/nolitadirtbag/p/Cabc/",
        caption="text only post",
    )
    assert "images" not in item.metadata_json


def test_add_post_requires_content(session, ig_source):
    with pytest.raises(IntakeError, match="empty post"):
        add_post(session, "https://www.instagram.com/nolitadirtbag/p/Cempty/")


def test_add_post_duplicate_rejected(session, ig_source):
    add_post(session, "https://www.instagram.com/nolitadirtbag/p/Cdup/", caption="x")
    with pytest.raises(IntakeError, match="Already stored"):
        add_post(
            session, "https://www.instagram.com/nolitadirtbag/p/Cdup/?utm_source=y", caption="x"
        )
    assert session.query(ContentItem).count() == 1


def test_add_post_unknown_account_requires_explicit_source(session, ig_source):
    with pytest.raises(IntakeError, match="--source"):
        add_post(session, "https://www.instagram.com/someoneelse/p/Cx/", caption="x")
    with pytest.raises(IntakeError, match="No source named"):
        add_post(
            session,
            "https://www.instagram.com/someoneelse/p/Cx/",
            caption="x",
            source_name="Not Registered",
        )


def test_infer_source_tiktok_handle(session):
    source = Source(
        name="Some TikToker",
        platform="tiktok",
        url="https://www.tiktok.com/@some.creator",
        tier="watch",
        active=False,
    )
    session.add(source)
    session.commit()
    found = infer_source(session, "https://www.tiktok.com/@some.creator/video/123456")
    assert found is not None and found.id == source.id


def test_unsupported_image_type_rejected(session, ig_source, tmp_path):
    bad = tmp_path / "clip.mp4"
    bad.write_bytes(b"fake")
    with pytest.raises(IntakeError, match="Unsupported image type"):
        add_post(
            session,
            "https://www.instagram.com/nolitadirtbag/p/Cvid/",
            caption="x",
            image_path=bad,
        )


def test_analyzer_passes_images_to_provider(session, ig_source, tmp_path):
    from culture.analysis.item_analyzer import ItemAnalyzer

    image = make_image(tmp_path)
    add_post(
        session,
        "https://www.instagram.com/nolitadirtbag/p/Cvision/",
        caption="meme about gorpcore",
        image_path=image,
    )

    calls = {}

    class VisionFake:
        name = "fake"
        model = "fake-1"

        def generate_structured(self, system, user, output_format, images=None):
            calls["images"] = images
            calls["user"] = user
            return ItemAnalysisResponse(summary="A meme showing a gorpcore outfit.")

        def generate_text(self, system, user, max_tokens=16000):
            return "t"

    stats = ItemAnalyzer(session, VisionFake()).analyze_pending()

    assert stats.analyzed == 1
    assert calls["images"] is not None and calls["images"][0][0] == "image/png"
    assert "image(s) attached ABOVE" in calls["user"]


def test_analyzer_text_only_posts_get_caption_only_guard(session, ig_source):
    from culture.analysis.item_analyzer import ItemAnalyzer

    add_post(session, "https://www.instagram.com/nolitadirtbag/p/Ctext/", caption="words")

    calls = {}

    class TextFake:
        name = "fake"
        model = "fake-1"

        # no images kwarg — proves the analyzer only passes it when images exist
        def generate_structured(self, system, user, output_format):
            calls["user"] = user
            return ItemAnalysisResponse(summary="ok")

        def generate_text(self, system, user, max_tokens=16000):
            return "t"

    stats = ItemAnalyzer(session, TextFake()).analyze_pending()
    assert stats.analyzed == 1
    assert "never guess what the visual showed" in calls["user"]
