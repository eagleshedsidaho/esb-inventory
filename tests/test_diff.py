from __future__ import annotations

import pytest

from scraper.diff import compute_diff, is_empty, safety_rail_reason


def shed(shed_id, cash=5000.0, rto36=200.0, **extra):
    return {
        "inventory_id": shed_id,
        "style": "Utility",
        "width_ft": 10,
        "length_ft": 12,
        "cash_price_usd": cash,
        "rto_36mo_usd": rto36,
        "rto_60mo_usd": 150.0,
        "rto_84mo_usd": None,
        "oh_detail_url": f"https://oldhickorybuildings.com/product/{shed_id}/",
        **extra,
    }


def test_identical_lists_have_empty_diff():
    diff = compute_diff([shed("a"), shed("b")], [shed("a"), shed("b")])
    assert diff == {"added": [], "removed": [], "price_changed": []}
    assert is_empty(diff)


def test_added_and_removed():
    diff = compute_diff([shed("a"), shed("b")], [shed("b"), shed("c")])
    assert [s["inventory_id"] for s in diff["added"]] == ["c"]
    assert [s["inventory_id"] for s in diff["removed"]] == ["a"]
    assert diff["price_changed"] == []
    assert not is_empty(diff)


def test_price_changed_lists_only_moved_fields():
    diff = compute_diff([shed("a", cash=5000.0, rto36=200.0)], [shed("a", cash=4800.0)])
    (change,) = diff["price_changed"]
    assert change["inventory_id"] == "a"
    assert change["changes"] == {"cash_price_usd": {"old": 5000.0, "new": 4800.0}}


def test_non_price_changes_are_ignored():
    diff = compute_diff([shed("a", siding_color=None)], [shed("a", siding_color="Pewter Cast")])
    assert is_empty(diff)


def test_first_run_everything_added():
    diff = compute_diff([], [shed("a"), shed("b")])
    assert len(diff["added"]) == 2


@pytest.mark.parametrize(
    ("previous", "fresh", "trips"),
    [
        (12, 12, False),
        (12, 6, False),  # exactly half is allowed
        (12, 5, True),
        (12, 24, False),  # exactly double is allowed
        (12, 25, True),
        (12, 0, True),
        (0, 12, False),  # first run
        (0, 0, True),
    ],
)
def test_safety_rail(previous, fresh, trips):
    assert (safety_rail_reason(previous, fresh) is not None) is trips
