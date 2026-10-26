"""Re-download the test fixtures from the live Old Hickory site.

Saves the Eagle listing page to tests/snapshot.html and the first shed's
detail page to tests/detail_snapshot.html, using the same User-Agent,
robots.txt check, and 500ms spacing as the nightly scraper.

Usage:
    python -m scraper.fetch_fixture

Afterwards, run pytest: tests that pin values from the old snapshot (shed
count, first/last shed, the detail-page shed's colors) will need their expected
values updated to match the new pages.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scraper import scrape

TESTS_DIR = Path("tests")


def main() -> int:
    session = scrape.make_session()
    robots = scrape.check_robots(scrape.SOURCE_URL, session)
    listing = scrape.fetch_page(scrape.SOURCE_URL, session)
    (TESTS_DIR / "snapshot.html").write_text(listing, encoding="utf-8")

    cards = scrape.parse_listing(listing)
    print(f"tests/snapshot.html: {len(cards)} shed cards parsed")
    detail_url = next((c["detail_url"] for c in cards if c["detail_url"]), None)
    if detail_url is None:
        print("No product links found; the listing markup may have changed.", file=sys.stderr)
        return 1

    scrape.ensure_allowed(robots, detail_url)
    detail = scrape.fetch_page(detail_url, session)
    (TESTS_DIR / "detail_snapshot.html").write_text(detail, encoding="utf-8")
    parsed = scrape.parse_detail(detail)
    print(f"tests/detail_snapshot.html: {detail_url}")
    print(
        f"  siding={parsed['siding_color']!r} roof={parsed['roof_color']!r} "
        f"{parsed['roof_type']!r} photos={len(parsed['image_urls'])}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
