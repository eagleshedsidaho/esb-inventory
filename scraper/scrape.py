"""Fetch and parse Old Hickory Buildings dealer listing and product detail pages.

The end-to-end nightly pipeline lives in scraper/run.py.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup, Tag

from scraper.normalize import apply_seen_dates, clean_text, normalize_shed

SOURCE_URL = "https://oldhickorybuildings.com/locations/eagle-idaho/"
USER_AGENT = "ESB-Inventory-Mirror/1.0 (contact: info@eagleshedsidaho.com)"
REQUEST_TIMEOUT = 30
MIN_REQUEST_INTERVAL = 0.5  # seconds between any two requests

log = logging.getLogger("scraper")


class RobotsDisallowed(RuntimeError):
    """robots.txt forbids fetching the inventory page (or could not be read)."""


# --------------------------------------------------------------------------- fetch


class ThrottledSession(requests.Session):
    """A requests session that waits `min_interval` seconds between any two requests."""

    def __init__(self, min_interval: float = MIN_REQUEST_INTERVAL, sleep=time.sleep):
        super().__init__()
        self.headers["User-Agent"] = USER_AGENT
        self.min_interval = min_interval
        self._sleep = sleep
        self._last_request: float | None = None

    def request(self, *args, **kwargs):
        if self._last_request is not None:
            remaining = self.min_interval - (time.monotonic() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        try:
            return super().request(*args, **kwargs)
        finally:
            self._last_request = time.monotonic()


def make_session() -> requests.Session:
    return ThrottledSession()


def check_robots(url: str, session: requests.Session) -> RobotFileParser | None:
    """Raise RobotsDisallowed unless robots.txt lets USER_AGENT fetch `url`.

    Returns the parsed rules (None if the site has no robots.txt) so later
    fetches on the same host can be checked without re-downloading it. A
    missing robots.txt (4xx) means everything is allowed; a server error or
    network failure aborts the run rather than guessing.
    """
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise RobotsDisallowed(f"could not fetch {robots_url}: {exc}") from exc
    if response.status_code >= 500:
        raise RobotsDisallowed(f"{robots_url} returned HTTP {response.status_code}")
    if response.status_code >= 400:
        return None
    rules = RobotFileParser()
    rules.parse(response.text.splitlines())
    ensure_allowed(rules, url)
    return rules


def ensure_allowed(rules: RobotFileParser | None, url: str) -> None:
    if rules is not None and not rules.can_fetch(USER_AGENT, url):
        raise RobotsDisallowed(f"robots.txt disallows {url}")


def fetch_page(url: str, session: requests.Session) -> str:
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


# --------------------------------------------------------------------------- parse


def parse_listing(html: str, base_url: str = SOURCE_URL) -> list[dict]:
    """Return one raw dict per shed card on the dealer listing page."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for card in soup.select("li.custom-product-item"):
        link = card.select_one(".product-image a[href]") or card.select_one(
            "a.product-info-button[href]"
        )
        image = card.select_one(".product-image img[src]")
        items.append(
            {
                "detail_url": urljoin(base_url, link["href"]) if link else None,
                "style": _text(card.select_one(".product-title")),
                "width": _text(card.select_one(".product-width")),
                "length": _text(card.select_one(".product-length")),
                "prices": _parse_prices(card),
                "image_urls": [urljoin(base_url, image["src"])] if image else [],
            }
        )
    return items


def parse_detail(html: str, base_url: str = SOURCE_URL) -> dict:
    """Parse a single product detail page (spec list + photo gallery).

    The listing page does not carry siding/roof details or gallery photos, so
    these only come from detail pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    specs: dict[str, str] = {}
    for li in soup.select(".product-details ul li"):
        label = li.find("strong")
        if not label:
            continue
        key = (clean_text(label.get_text()) or "").rstrip(":").lower()
        label.extract()
        specs[key] = clean_text(li.get_text()) or ""

    image_urls = []
    for thumb in soup.select("img.product-thumbnail"):
        src = thumb.get("data-large-image") or thumb.get("src")
        if src:
            image_urls.append(urljoin(base_url, src))
    main_image = soup.select_one("img.main-product-image, .product-main-image img")
    if main_image and main_image.get("src"):
        image_urls.insert(0, urljoin(base_url, main_image["src"]))

    return {
        "style": _text(soup.select_one(".product-details .product-title")),
        "serial_number": specs.get("inventory #"),
        "width": specs.get("width"),
        "length": specs.get("length"),
        "siding_color": specs.get("siding color"),
        "roof_color": specs.get("roof color"),
        "roof_type": specs.get("roof type"),
        "prices": _parse_prices(soup.select_one(".product-details") or soup),
        "image_urls": list(dict.fromkeys(image_urls)),
    }


def _parse_prices(scope: Tag) -> dict[str, str]:
    prices = {}
    for block in scope.select(".product-pricing .pricing"):
        label = _text(block.select_one(".price-type"))
        value = _text(block.select_one(".price-value"))
        if label and value and label not in prices:
            prices[label] = value
    return prices


def _text(node: Tag | None) -> str | None:
    return clean_text(node.get_text(" ")) if node else None


# --------------------------------------------------------------------------- build


def build_inventory(
    html: str, previous: list[dict] | None = None, today: str | None = None
) -> list[dict]:
    """Parse listing HTML into canonical shed records, deduped by inventory_id."""
    today = today or datetime.now(UTC).date().isoformat()
    sheds: dict[str, dict] = {}
    for raw in parse_listing(html):
        shed = normalize_shed(raw, today)
        shed_id = shed["inventory_id"]
        if not shed_id:
            log.warning("skipping card with no product link: %s", raw.get("style"))
            continue
        if shed_id in sheds:
            log.warning("duplicate inventory_id %s on page; keeping first", shed_id)
            continue
        sheds[shed_id] = shed
    return apply_seen_dates(sheds.values(), previous or [], today)


def inventory_document(sheds: list[dict], generated_at: datetime | None = None) -> dict:
    generated_at = generated_at or datetime.now(UTC)
    return {
        "source_url": SOURCE_URL,
        "generated_at": generated_at.replace(microsecond=0).isoformat(),
        "count": len(sheds),
        "sheds": sheds,
    }


def load_previous(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("sheds", [])


def write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
