"""Compare a fresh scrape against the published inventory, and the safety rail."""

from __future__ import annotations

from scraper.normalize import PRICE_FIELDS

SUMMARY_FIELDS = (
    "inventory_id",
    "style",
    "width_ft",
    "length_ft",
    "cash_price_usd",
    "oh_detail_url",
)

MIN_RATIO = 0.5
MAX_RATIO = 2.0


def summarize(shed: dict) -> dict:
    return {key: shed.get(key) for key in SUMMARY_FIELDS}


def compute_diff(previous: list[dict], current: list[dict]) -> dict:
    """Return {added, removed, price_changed} between two shed lists.

    price_changed entries carry only the price fields that moved:
    {..summary.., "changes": {"cash_price_usd": {"old": 5000.0, "new": 4800.0}}}
    """
    before = {s["inventory_id"]: s for s in previous}
    after = {s["inventory_id"]: s for s in current}

    price_changed = []
    for shed_id, shed in after.items():
        old = before.get(shed_id)
        if old is None:
            continue
        changes = {
            field: {"old": old.get(field), "new": shed.get(field)}
            for field in PRICE_FIELDS
            if old.get(field) != shed.get(field)
        }
        if changes:
            price_changed.append({**summarize(shed), "changes": changes})

    return {
        "added": [summarize(s) for i, s in after.items() if i not in before],
        "removed": [summarize(s) for i, s in before.items() if i not in after],
        "price_changed": price_changed,
    }


def is_empty(diff: dict) -> bool:
    return not (diff["added"] or diff["removed"] or diff["price_changed"])


def safety_rail_reason(previous_count: int, fresh_count: int) -> str | None:
    """Explain why a fresh scrape looks wrong, or None if it's safe to publish.

    Trips when the shed count drops below half or more than doubles versus the
    published inventory, or when the scrape found nothing at all. A first run
    (no previous inventory) only trips on zero sheds.
    """
    if fresh_count == 0:
        return "fresh scrape found 0 sheds"
    if previous_count == 0:
        return None
    if fresh_count < previous_count * MIN_RATIO:
        return f"shed count fell from {previous_count} to {fresh_count} (below {MIN_RATIO:.0%})"
    if fresh_count > previous_count * MAX_RATIO:
        return f"shed count rose from {previous_count} to {fresh_count} (above {MAX_RATIO:.0%})"
    return None
