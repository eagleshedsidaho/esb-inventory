"""End-to-end pipeline tests with all HTTP served from fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from scraper import run, scrape
from tests.fakes import FakeResponse, RoutingSession, image_bytes

FIXTURES = Path(__file__).parent
ROBOTS_URL = "https://oldhickorybuildings.com/robots.txt"
FIRST_DETAIL = "https://oldhickorybuildings.com/product/01tjw00000j6kkwia3/"
S3 = "https://ohbprodinv.s3.us-east-2.amazonaws.com/"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
ALLOW_ALL = "User-agent: *\nDisallow: /wp-admin/\n"


@pytest.fixture
def listing_html() -> str:
    return (FIXTURES / "snapshot.html").read_text(encoding="utf-8")


@pytest.fixture
def session(listing_html) -> RoutingSession:
    """Live-like site: listing + one detail page (others 404) + any S3 image."""
    return RoutingSession(
        {
            ROBOTS_URL: FakeResponse(200, ALLOW_ALL),
            scrape.SOURCE_URL: FakeResponse(200, listing_html),
            FIRST_DETAIL: FakeResponse(
                200, (FIXTURES / "detail_snapshot.html").read_text(encoding="utf-8")
            ),
            S3: FakeResponse(200, image_bytes()),
        }
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fake_previous(docs: Path, count: int) -> None:
    sheds = [{"inventory_id": f"old{i}", "oh_image_urls": []} for i in range(count)]
    scrape.write_json(docs / "inventory.json", {"count": count, "sheds": sheds})


def test_first_live_run(tmp_path, session):
    docs = tmp_path / "docs"
    diff_out = tmp_path / "diff.json"
    assert run.run(docs, session=session, diff_out=diff_out, now=NOW) == run.EXIT_OK

    doc = read_json(docs / "inventory.json")
    assert doc["count"] == 12
    first = doc["sheds"][0]
    assert first["siding_color"] == "Pewter Cast"
    assert (first["roof_type"], first["roof_color"]) == ("Metal", "Black")
    assert len(first["oh_image_urls"]) == 4
    assert first["image_files"] == [f"images/01tjw00000j6kkwia3_{i}.jpg" for i in range(4)]
    for other in doc["sheds"][1:]:  # their detail pages 404 -> listing data kept
        assert other["siding_color"] is None
        assert len(other["image_files"]) == 1

    assert len(list((docs / "images").glob("*.jpg"))) == 4 + 11
    assert len(session.requests_matching("/product/")) == 12
    assert session.requested[:2] == [ROBOTS_URL, scrape.SOURCE_URL]
    assert len(read_json(diff_out)["added"]) == 12
    assert read_json(diff_out)["content_changed"] is True
    assert not (docs / run.WARNING_FILE).exists()


def test_second_run_reuses_details_and_images(tmp_path, session, listing_html):
    docs = tmp_path / "docs"
    run.run(docs, session=session, now=NOW)
    second = RoutingSession(session.routes)
    diff_out = tmp_path / "diff.json"
    assert run.run(docs, session=second, diff_out=diff_out, now=NOW) == run.EXIT_OK

    # Shed 0 has full spec data now, so only the 11 still missing it are retried.
    assert FIRST_DETAIL not in second.requested
    assert len(second.requests_matching("/product/")) == 11
    assert second.requests_matching(S3) == []
    diff = read_json(diff_out)
    assert (diff["added"], diff["removed"], diff["price_changed"]) == ([], [], [])
    assert diff["content_changed"] is False
    assert read_json(docs / "inventory.json")["sheds"][0]["siding_color"] == "Pewter Cast"


def test_price_change_detected(tmp_path, session):
    docs = tmp_path / "docs"
    run.run(docs, session=session, now=NOW)
    doc = read_json(docs / "inventory.json")
    doc["sheds"][0]["cash_price_usd"] = 3500.0
    scrape.write_json(docs / "inventory.json", doc)

    diff_out = tmp_path / "diff.json"
    run.run(docs, session=RoutingSession(session.routes), diff_out=diff_out, now=NOW)
    (change,) = read_json(diff_out)["price_changed"]
    assert change["changes"] == {"cash_price_usd": {"old": 3500.0, "new": 3140.75}}


@pytest.mark.parametrize("previous_count", [25, 5], ids=["shrank-below-half", "more-than-doubled"])
def test_safety_rail_blocks_publish(tmp_path, session, previous_count):
    docs = tmp_path / "docs"
    fake_previous(docs, previous_count)
    before = (docs / "inventory.json").read_text(encoding="utf-8")

    assert run.run(docs, session=session, now=NOW) == run.EXIT_SAFETY_RAIL
    assert (docs / "inventory.json").read_text(encoding="utf-8") == before
    warning = read_json(docs / run.WARNING_FILE)
    assert (warning["previous_count"], warning["fresh_count"]) == (previous_count, 12)
    assert len(warning["diff"]["added"]) == 12
    assert len(warning["diff"]["removed"]) == previous_count
    # Nothing downloaded beyond robots.txt + the listing page.
    assert session.requested == [ROBOTS_URL, scrape.SOURCE_URL]


def test_empty_listing_trips_rail_even_on_first_run(tmp_path, session):
    session.routes[scrape.SOURCE_URL] = FakeResponse(200, "<html>No results found</html>")
    docs = tmp_path / "docs"
    assert run.run(docs, session=session, now=NOW) == run.EXIT_SAFETY_RAIL
    assert not (docs / "inventory.json").exists()


def test_successful_run_clears_old_warning(tmp_path, session):
    docs = tmp_path / "docs"
    scrape.write_json(docs / run.WARNING_FILE, {"reason": "old"})
    assert run.run(docs, session=session, now=NOW) == run.EXIT_OK
    assert not (docs / run.WARNING_FILE).exists()


def test_robots_disallow_aborts_before_listing(tmp_path, session):
    session.routes[ROBOTS_URL] = FakeResponse(200, "User-agent: *\nDisallow: /locations/\n")
    docs = tmp_path / "docs"
    assert run.run(docs, session=session, now=NOW) == run.EXIT_FETCH_FAILED
    assert session.requested == [ROBOTS_URL]
    assert not docs.exists()


def test_robots_disallowing_products_skips_detail_pages(tmp_path, session):
    session.routes[ROBOTS_URL] = FakeResponse(200, "User-agent: *\nDisallow: /product/\n")
    assert run.run(tmp_path / "docs", session=session, now=NOW) == run.EXIT_OK
    assert session.requests_matching("/product/") == []


def test_listing_server_error_aborts(tmp_path, session):
    session.routes[scrape.SOURCE_URL] = FakeResponse(500)
    assert run.run(tmp_path / "docs", session=session, now=NOW) == run.EXIT_FETCH_FAILED


def test_offline_snapshot_cli(tmp_path):
    docs = tmp_path / "docs"
    args = [
        "--snapshot",
        str(FIXTURES / "snapshot.html"),
        "--docs-dir",
        str(docs),
        "--diff-out",
        str(tmp_path / "diff.json"),
    ]
    assert run.main(args) == run.EXIT_OK
    doc = read_json(docs / "inventory.json")
    assert doc["count"] == 12
    assert all(s["image_files"] == [] for s in doc["sheds"])  # offline: no downloads

    doc["sheds"][0]["first_seen"] = "2026-01-01"
    scrape.write_json(docs / "inventory.json", doc)
    run.main(args)
    assert read_json(docs / "inventory.json")["sheds"][0]["first_seen"] == "2026-01-01"


def test_throttled_session_spaces_requests(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: None)
    session = scrape.ThrottledSession(min_interval=0.5, sleep=sleeps.append)
    session.get("https://example.com/1")
    session.get("https://example.com/2")
    session.get("https://example.com/3")
    assert len(sleeps) == 2
    assert all(0.4 < s <= 0.5 for s in sleeps)
    assert session.headers["User-Agent"] == scrape.USER_AGENT


def test_images_of_removed_sheds_are_pruned(tmp_path, session):
    docs = tmp_path / "docs"
    run.run(docs, session=session, now=NOW)
    (docs / "images" / "soldshed123_0.jpg").write_bytes(image_bytes())
    assert run.run(docs, session=RoutingSession(session.routes), now=NOW) == run.EXIT_OK
    assert not (docs / "images" / "soldshed123_0.jpg").exists()
    assert len(list((docs / "images").glob("*.jpg"))) == 15


def test_safety_rail_does_not_prune(tmp_path, session):
    docs = tmp_path / "docs"
    fake_previous(docs, 30)
    (docs / "images").mkdir()
    (docs / "images" / "old0_0.jpg").write_bytes(image_bytes())
    assert run.run(docs, session=session, now=NOW) == run.EXIT_SAFETY_RAIL
    assert (docs / "images" / "old0_0.jpg").exists()


def test_new_detail_data_counts_as_content_change_without_diff(tmp_path, session):
    docs = tmp_path / "docs"
    detail = session.routes.pop(FIRST_DETAIL)
    run.run(docs, session=session, now=NOW)  # every detail page 404s

    session.routes[FIRST_DETAIL] = detail
    diff_out = tmp_path / "diff.json"
    run.run(docs, session=RoutingSession(session.routes), diff_out=diff_out, now=NOW)
    diff = read_json(diff_out)
    assert (diff["added"], diff["removed"], diff["price_changed"]) == ([], [], [])
    assert diff["content_changed"] is True


def test_override_publishes_despite_safety_rail(tmp_path, session):
    docs = tmp_path / "docs"
    fake_previous(docs, 30)
    code = run.run(docs, session=session, now=NOW, override_safety_rail=True)
    assert code == run.EXIT_OK
    assert read_json(docs / "inventory.json")["count"] == 12
    assert not (docs / run.WARNING_FILE).exists()
