"""Parser tests against the frozen Old Hickory snapshots in tests/."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scraper import scrape
from scraper.normalize import (
    SHED_FIELDS,
    apply_seen_dates,
    classify_price_label,
    inventory_id_from_url,
    merge_detail,
    normalize_shed,
    parse_feet,
    parse_money,
)

FIXTURES = Path(__file__).parent
TODAY = "2026-10-26"


@pytest.fixture(scope="module")
def listing_html() -> str:
    return (FIXTURES / "snapshot.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def detail_html() -> str:
    return (FIXTURES / "detail_snapshot.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def sheds(listing_html: str) -> list[dict]:
    return scrape.build_inventory(listing_html, today=TODAY)


def card(title="Utility", href="/product/abc123/", prices=None) -> str:
    """Minimal listing card markup mirroring the live page's structure."""
    link = (
        f'<div class="product-image"><a href="{href}"><img src="/x.jpg"></a></div>' if href else ""
    )
    pricing = "".join(
        f'<div class="pricing"><div class="price-type">{label}</div>'
        f'<div class="price-value">{value}</div></div>'
        for label, value in (prices or {"Internet Price:": "$1,000.00 + tax"}).items()
    )
    return (
        f'<li class="custom-product-item"><h2 class="product-title">{title}</h2>'
        f'<p class="product-width">Width: 10</p><p class="product-length">Length: 12</p>'
        f'{link}<div class="product-pricing">{pricing}</div></li>'
    )


# --------------------------------------------------------------------------- snapshot


def test_snapshot_has_twelve_sheds(sheds):
    assert len(sheds) == 12


def test_every_shed_has_exact_schema_keys_in_order(sheds):
    for shed in sheds:
        assert tuple(shed) == SHED_FIELDS


def test_inventory_ids_are_unique_lowercase_salesforce_ids(sheds):
    ids = [s["inventory_id"] for s in sheds]
    assert len(set(ids)) == len(ids)
    for shed_id in ids:
        assert re.fullmatch(r"[0-9a-z]{18}", shed_id)


def test_first_shed_parsed_exactly(sheds):
    assert sheds[0] == {
        "inventory_id": "01tjw00000j6kkwia3",
        "style": "Barn",
        "width_ft": 8,
        "length_ft": 12,
        "siding_color": None,
        "roof_type": None,
        "roof_color": None,
        "cash_price_usd": 3140.75,
        "rto_36mo_usd": 145.41,
        "rto_60mo_usd": 116.32,
        "rto_84mo_usd": None,
        "oh_detail_url": "https://oldhickorybuildings.com/product/01tjw00000j6kkwia3/",
        "oh_image_urls": [
            "https://ohbprodinv.s3.us-east-2.amazonaws.com/01tJw00000J6KKWIA3/featured/"
            "front_1782416604610_17824165928246804722992995783483.jpg"
        ],
        "image_files": [],
        "first_seen": TODAY,
        "last_seen": TODAY,
    }


def test_last_shed_parsed(sheds):
    last = sheds[-1]
    assert last["inventory_id"] == "01tpp00000c6a3vyar"
    assert last["style"] == "Lofted Barn"
    assert (last["width_ft"], last["length_ft"]) == (12, 24)
    assert last["cash_price_usd"] == 10469.60
    assert last["rto_36mo_usd"] == 484.70
    assert last["rto_60mo_usd"] == 387.76


def test_styles_in_snapshot(sheds):
    assert {s["style"] for s in sheds} == {
        "Barn",
        "Lofted Barn",
        "Chicken Coop",
        "Animal Shelter",
        "Utility",
        "Cabana",
    }


def test_all_sheds_have_prices_dimensions_and_hero_image(sheds):
    for shed in sheds:
        assert shed["cash_price_usd"] > 0
        assert shed["rto_36mo_usd"] > shed["rto_60mo_usd"] > 0
        assert shed["width_ft"] > 0 and shed["length_ft"] > 0
        assert len(shed["oh_image_urls"]) == 1
        assert shed["oh_image_urls"][0].startswith("https://ohbprodinv.s3.")
        assert shed["oh_detail_url"].endswith(f"/product/{shed['inventory_id']}/")


def test_listing_page_does_not_publish_84_month_or_siding_roof(sheds):
    for shed in sheds:
        assert shed["rto_84mo_usd"] is None
        assert shed["siding_color"] is None
        assert shed["roof_type"] is None
        assert shed["roof_color"] is None


def test_hero_image_id_matches_inventory_id_case_insensitively(sheds):
    for shed in sheds:
        assert f"/{shed['inventory_id']}/" in shed["oh_image_urls"][0].lower()


def test_inventory_json_is_serializable_and_round_trips(sheds):
    doc = scrape.inventory_document(sheds)
    assert doc["count"] == 12
    assert json.loads(json.dumps(doc))["sheds"] == sheds


# --------------------------------------------------------------------------- detail page


def test_parse_detail_page(detail_html):
    detail = scrape.parse_detail(detail_html)
    assert detail["style"] == "Barn"
    assert detail["serial_number"] == "WBN-E9297-0812-062526-SP"
    assert detail["siding_color"] == "Pewter Cast"
    assert detail["roof_color"] == "Black"
    assert detail["roof_type"] == "Metal"
    assert parse_feet(detail["width"]) == 8 and parse_feet(detail["length"]) == 12
    assert len(detail["image_urls"]) == 4
    assert "/featured/" in detail["image_urls"][0]
    assert all("/gallery/" in url for url in detail["image_urls"][1:])


def test_merge_detail_fills_specs_and_appends_gallery(sheds, detail_html):
    merged = merge_detail(sheds[0], scrape.parse_detail(detail_html))
    assert merged["siding_color"] == "Pewter Cast"
    assert merged["roof_type"] == "Metal"
    assert merged["roof_color"] == "Black"
    assert merged["oh_image_urls"][0] == sheds[0]["oh_image_urls"][0]
    assert len(merged["oh_image_urls"]) == 4  # hero not duplicated
    assert merged["cash_price_usd"] == sheds[0]["cash_price_usd"]


# --------------------------------------------------------------------------- edge cases


def test_84_month_rto_is_parsed_when_present():
    prices = {"Internet Price:": "$5,000 + tax", "84-Month RTO:": "$99.50 + tax"}
    html = f"<ul>{card(prices=prices)}</ul>"
    (shed,) = scrape.build_inventory(html, today=TODAY)
    assert shed["cash_price_usd"] == 5000.0
    assert shed["rto_84mo_usd"] == 99.50
    assert shed["rto_36mo_usd"] is None


def test_card_without_product_link_is_skipped():
    html = f"<ul>{card(href=None)}{card(title='Cabana', href='/product/zzz999/')}</ul>"
    sheds = scrape.build_inventory(html, today=TODAY)
    assert [s["inventory_id"] for s in sheds] == ["zzz999"]


def test_duplicate_cards_keep_first():
    html = f"<ul>{card(title='A')}{card(title='B')}</ul>"
    sheds = scrape.build_inventory(html, today=TODAY)
    assert [s["style"] for s in sheds] == ["A"]


def test_empty_page_yields_no_sheds():
    assert scrape.build_inventory("<html><body>No results</body></html>", today=TODAY) == []


def test_first_seen_carries_forward_from_previous(sheds):
    previous = [{"inventory_id": sheds[0]["inventory_id"], "first_seen": "2026-09-01"}]
    updated = apply_seen_dates(sheds, previous, "2026-10-27")
    assert updated[0]["first_seen"] == "2026-09-01"
    assert updated[1]["first_seen"] == "2026-10-27"
    assert all(s["last_seen"] == "2026-10-27" for s in updated)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$3,140.75 + tax", 3140.75),
        ("$ 10,469.60 + tax", 10469.6),
        ("$99 + tax", 99.0),
        ("Call for price", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_money(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Width: 8", 8), ("Length: 12", 12), ("8'", 8), ("11.33", 11.33), ("n/a", None)],
)
def test_parse_feet(text, expected):
    assert parse_feet(text) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://oldhickorybuildings.com/product/01tjw00000j6kkwia3/", "01tjw00000j6kkwia3"),
        ("https://oldhickorybuildings.com/product/01tJw00000J6KKWIA3", "01tjw00000j6kkwia3"),
        ("https://oldhickorybuildings.com/locations/eagle-idaho/", None),
        (None, None),
    ],
)
def test_inventory_id_from_url(url, expected):
    assert inventory_id_from_url(url) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Internet Price:", "cash_price_usd"),
        ("36-Month RTO:", "rto_36mo_usd"),
        ("60-Month RTO:", "rto_60mo_usd"),
        ("84 Month RTO", "rto_84mo_usd"),
        ("48-Month RTO:", None),
        ("Delivery:", None),
    ],
)
def test_classify_price_label(label, expected):
    assert classify_price_label(label) == expected


def test_normalize_shed_with_no_prices():
    shed = normalize_shed({"detail_url": "https://x/product/abc/", "style": " Utility "}, TODAY)
    assert shed["inventory_id"] == "abc"
    assert shed["style"] == "Utility"
    assert shed["cash_price_usd"] is None
    assert shed["oh_image_urls"] == []


# --------------------------------------------------------------------------- robots.txt


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requested: list[str] = []

    def get(self, url, timeout=None):
        self.requested.append(url)
        return self.response


LIVE_ROBOTS = """User-agent: *
Disallow: /wp-content/uploads/wc-logs/
Disallow: /wp-admin/
Allow: /wp-admin/admin-ajax.php

User-agent: *
Disallow:
"""


def test_robots_allows_inventory_page_with_current_rules():
    session = FakeSession(FakeResponse(200, LIVE_ROBOTS))
    scrape.check_robots(scrape.SOURCE_URL, session)
    assert session.requested == ["https://oldhickorybuildings.com/robots.txt"]


@pytest.mark.parametrize(
    "rules",
    [
        "User-agent: *\nDisallow: /locations/\n",
        "User-agent: ESB-Inventory-Mirror\nDisallow: /\n",
    ],
)
def test_robots_disallow_aborts(rules):
    with pytest.raises(scrape.RobotsDisallowed):
        scrape.check_robots(scrape.SOURCE_URL, FakeSession(FakeResponse(200, rules)))


def test_robots_missing_is_allowed():
    scrape.check_robots(scrape.SOURCE_URL, FakeSession(FakeResponse(404)))


def test_robots_server_error_aborts():
    with pytest.raises(scrape.RobotsDisallowed):
        scrape.check_robots(scrape.SOURCE_URL, FakeSession(FakeResponse(503)))
