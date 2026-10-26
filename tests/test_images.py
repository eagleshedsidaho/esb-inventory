from __future__ import annotations

import io

import pytest
from PIL import Image

from scraper.images import (
    MAX_EDGE_PX,
    InvalidImage,
    cache_images,
    image_filename,
    prepare_image,
    prune_images,
)
from tests.fakes import FakeResponse, RoutingSession, image_bytes

URL_A = "https://ohbprodinv.s3.us-east-2.amazonaws.com/X/featured/a.jpg"
URL_B = "https://ohbprodinv.s3.us-east-2.amazonaws.com/X/gallery/b.jpg"


def shed(urls, shed_id="abc"):
    return {"inventory_id": shed_id, "oh_image_urls": list(urls), "image_files": []}


def test_image_filename():
    assert image_filename("01tjw00000j6kkwia3", 0) == "01tjw00000j6kkwia3_0.jpg"


def open_bytes(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def test_small_jpeg_keeps_its_size():
    img = open_bytes(prepare_image(image_bytes("JPEG", size=(800, 600))))
    assert (img.format, img.size) == ("JPEG", (800, 600))


def test_png_is_converted_to_jpeg():
    assert open_bytes(prepare_image(image_bytes("PNG"))).format == "JPEG"


@pytest.mark.parametrize(
    ("size", "expected"),
    [((4160, 3120), (1600, 1200)), ((1500, 2000), (1200, 1600)), ((2000, 924), (1600, 739))],
)
def test_large_images_are_downscaled_to_max_edge(size, expected):
    img = open_bytes(prepare_image(image_bytes("JPEG", size=size)))
    assert img.size == expected
    assert max(img.size) == MAX_EDGE_PX


def test_exif_orientation_applied_and_metadata_stripped():
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW
    exif[0x010F] = "PhoneMaker"
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(out, format="JPEG", exif=exif)
    img = open_bytes(prepare_image(out.getvalue()))
    assert img.size == (20, 40)
    assert not img.getexif()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<html>Access Denied</html>",
        image_bytes("GIF"),
        image_bytes("JPEG", size=(200, 200))[:300],  # truncated
    ],
    ids=["empty", "html-error-page", "gif", "truncated-jpeg"],
)
def test_invalid_images_rejected(data):
    with pytest.raises(InvalidImage):
        prepare_image(data)


def test_downloads_new_images(tmp_path):
    session = RoutingSession(
        {URL_A: FakeResponse(200, image_bytes()), URL_B: FakeResponse(200, image_bytes("PNG"))}
    )
    sheds, stats = cache_images([shed([URL_A, URL_B])], {}, tmp_path, session)
    assert sheds[0]["image_files"] == ["images/abc_0.jpg", "images/abc_1.jpg"]
    assert stats.downloaded == ["abc_0.jpg", "abc_1.jpg"]
    for name in stats.downloaded:
        with Image.open(tmp_path / name) as img:
            assert img.format == "JPEG"


def test_existing_unchanged_images_are_not_redownloaded(tmp_path):
    (tmp_path / "abc_0.jpg").write_bytes(image_bytes())
    session = RoutingSession()
    previous = {"abc": shed([URL_A])}
    sheds, stats = cache_images([shed([URL_A])], previous, tmp_path, session)
    assert session.requested == []
    assert stats.reused == ["abc_0.jpg"]
    assert sheds[0]["image_files"] == ["images/abc_0.jpg"]


def test_changed_url_at_same_index_is_redownloaded(tmp_path):
    (tmp_path / "abc_0.jpg").write_bytes(image_bytes(color="blue"))
    session = RoutingSession({URL_B: FakeResponse(200, image_bytes(color="green"))})
    _, stats = cache_images([shed([URL_B])], {"abc": shed([URL_A])}, tmp_path, session)
    assert stats.downloaded == ["abc_0.jpg"]
    with Image.open(tmp_path / "abc_0.jpg") as img:
        assert img.convert("RGB").getpixel((0, 0))[1] > 100  # green, not the old blue


def test_invalid_download_is_skipped_and_not_written(tmp_path):
    session = RoutingSession(
        {URL_A: FakeResponse(200, b"<html>nope</html>"), URL_B: FakeResponse(404)}
    )
    sheds, stats = cache_images([shed([URL_A, URL_B])], {}, tmp_path, session)
    assert sheds[0]["image_files"] == []
    assert stats.failed == ["abc_0.jpg", "abc_1.jpg"]
    assert list(tmp_path.iterdir()) == []


def test_offline_uses_cache_only(tmp_path):
    (tmp_path / "abc_0.jpg").write_bytes(image_bytes())
    previous = {"abc": shed([URL_A])}
    sheds, stats = cache_images([shed([URL_A, URL_B])], previous, tmp_path, session=None)
    assert sheds[0]["image_files"] == ["images/abc_0.jpg"]
    assert stats.skipped == ["abc_1.jpg"]


def test_oversized_cached_file_is_resized_in_place(tmp_path):
    (tmp_path / "abc_0.jpg").write_bytes(image_bytes(size=(3000, 2000)))
    session = RoutingSession()
    _, stats = cache_images([shed([URL_A])], {"abc": shed([URL_A])}, tmp_path, session)
    assert session.requested == []
    assert stats.resized == ["abc_0.jpg"]
    with Image.open(tmp_path / "abc_0.jpg") as img:
        assert img.size == (1600, 1067)


def test_prune_removes_only_unreferenced_cache_files(tmp_path):
    for name in ["keep_0.jpg", "gone_0.jpg", "gone_1.jpg", "keep_5.jpg", "half.tmp"]:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "README.txt").write_text("not ours")
    (tmp_path / "Logo.jpg").write_bytes(b"x")  # not our naming scheme

    deleted = prune_images(tmp_path, {"images/keep_0.jpg"})
    assert deleted == ["gone_0.jpg", "gone_1.jpg", "keep_5.jpg"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "Logo.jpg",
        "README.txt",
        "half.tmp",
        "keep_0.jpg",
    ]


def test_prune_missing_dir(tmp_path):
    assert prune_images(tmp_path / "nope", set()) == []
