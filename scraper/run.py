"""Nightly pipeline: scrape -> safety rail -> detail pages -> image cache -> diff -> publish.

Usage:
    python -m scraper.run                                # live run against Old Hickory
    python -m scraper.run --snapshot tests/snapshot.html # offline: parse a saved page,
                                                         # reuse cached details/images only

Exit codes:
    0  inventory written (diff may be empty)
    2  robots.txt disallowed the page, or the listing could not be fetched
    3  safety rail tripped; inventory.json left untouched, see last_scrape_warning.json
       (after confirming the change is real, rerun with --override-safety-rail)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from scraper import scrape
from scraper.diff import compute_diff, is_empty, safety_rail_reason
from scraper.images import cache_images, prune_images
from scraper.normalize import carry_forward_detail, merge_detail, needs_detail

EXIT_OK = 0
EXIT_FETCH_FAILED = 2
EXIT_SAFETY_RAIL = 3

INVENTORY_FILE = "inventory.json"
WARNING_FILE = "last_scrape_warning.json"
IMAGES_DIR = "images"

log = logging.getLogger("scraper.run")


def enrich_with_details(
    sheds: list[dict],
    previous_by_id: dict[str, dict],
    session: requests.Session | None,
    robots,
) -> tuple[list[dict], list[str]]:
    """Fetch detail pages where needed; reuse earlier detail data otherwise.

    With no session (offline), sheds that need a fetch keep listing-only data.
    A failed detail fetch is logged and the shed keeps its listing data.
    """
    result, fetched = [], []
    for shed in sheds:
        previous = previous_by_id.get(shed["inventory_id"])
        if not needs_detail(shed, previous):
            result.append(carry_forward_detail(shed, previous))
            continue
        if session is None:
            result.append(shed)
            continue
        url = shed["oh_detail_url"]
        try:
            scrape.ensure_allowed(robots, url)
            detail = scrape.parse_detail(scrape.fetch_page(url, session))
        except (requests.RequestException, scrape.RobotsDisallowed) as exc:
            log.warning("detail page for %s skipped: %s", shed["inventory_id"], exc)
            result.append(shed)
            continue
        fetched.append(shed["inventory_id"])
        result.append(merge_detail(shed, detail))
    return result, fetched


def _published_view(sheds: list[dict]) -> list[dict]:
    """Shed records minus last_seen, which changes every night by design."""
    return [{k: v for k, v in shed.items() if k != "last_seen"} for shed in sheds]


def run(
    docs_dir: Path,
    session: requests.Session | None = None,
    snapshot_html: str | None = None,
    diff_out: Path | None = None,
    now: datetime | None = None,
    override_safety_rail: bool = False,
) -> int:
    now = now or datetime.now(UTC)
    today = now.date().isoformat()
    inventory_path = docs_dir / INVENTORY_FILE
    warning_path = docs_dir / WARNING_FILE
    previous = scrape.load_previous(inventory_path)
    previous_by_id = {s["inventory_id"]: s for s in previous}

    robots = None
    if snapshot_html is not None:
        html = snapshot_html
        session = None
    else:
        session = session or scrape.make_session()
        try:
            robots = scrape.check_robots(scrape.SOURCE_URL, session)
            html = scrape.fetch_page(scrape.SOURCE_URL, session)
        except scrape.RobotsDisallowed as exc:
            log.error("aborting: %s", exc)
            return EXIT_FETCH_FAILED
        except requests.RequestException as exc:
            log.error("aborting: could not fetch %s: %s", scrape.SOURCE_URL, exc)
            return EXIT_FETCH_FAILED

    sheds = scrape.build_inventory(html, previous=previous, today=today)

    # Safety rail runs before any detail/image downloads so a broken page costs nothing.
    reason = safety_rail_reason(len(previous), len(sheds))
    if reason and override_safety_rail:
        log.warning("safety rail overridden by operator: %s", reason)
        reason = None
    if reason:
        diff = compute_diff(previous, sheds)
        scrape.write_json(
            warning_path,
            {
                "generated_at": now.replace(microsecond=0).isoformat(),
                "reason": reason,
                "previous_count": len(previous),
                "fresh_count": len(sheds),
                "diff": diff,
            },
        )
        log.error("SAFETY RAIL: %s. inventory.json NOT updated; see %s", reason, warning_path)
        return EXIT_SAFETY_RAIL

    sheds, detail_fetched = enrich_with_details(sheds, previous_by_id, session, robots)
    sheds, image_stats = cache_images(sheds, previous_by_id, docs_dir / IMAGES_DIR, session)
    diff = compute_diff(previous, sheds)

    scrape.write_json(inventory_path, scrape.inventory_document(sheds, generated_at=now))
    warning_path.unlink(missing_ok=True)
    pruned = prune_images(docs_dir / IMAGES_DIR, {f for shed in sheds for f in shed["image_files"]})
    if diff_out:
        scrape.write_json(
            diff_out,
            {
                **diff,
                # True whenever the published page would change (new specs/photos
                # included), not just added/removed/price changes; drives the commit.
                "content_changed": _published_view(previous) != _published_view(sheds),
                "generated_at": now.replace(microsecond=0).isoformat(),
                "source_url": scrape.SOURCE_URL,
            },
        )

    log.info("wrote %d sheds to %s", len(sheds), inventory_path)
    log.info("detail pages fetched: %d", len(detail_fetched))
    log.info(
        "images: %d downloaded, %d reused (%d resized), %d failed, %d pruned",
        len(image_stats.downloaded),
        len(image_stats.reused),
        len(image_stats.resized),
        len(image_stats.failed),
        len(pruned),
    )
    if is_empty(diff):
        log.info("diff: no changes")
    else:
        log.info(
            "diff: %d added, %d removed, %d price changes",
            len(diff["added"]),
            len(diff["removed"]),
            len(diff["price_changed"]),
        )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", type=Path, help="parse this saved HTML instead of fetching")
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument(
        "--diff-out", type=Path, default=Path("scrape_diff.json"), help="where to write the diff"
    )
    parser.add_argument(
        "--override-safety-rail",
        action="store_true",
        help="publish even if the shed count changed drastically (after checking it by hand)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    snapshot_html = args.snapshot.read_text(encoding="utf-8") if args.snapshot else None
    code = run(
        args.docs_dir,
        snapshot_html=snapshot_html,
        diff_out=args.diff_out,
        override_safety_rail=args.override_safety_rail,
    )
    if code == EXIT_OK and args.diff_out.exists():
        print(json.dumps(json.loads(args.diff_out.read_text(encoding="utf-8")), indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
