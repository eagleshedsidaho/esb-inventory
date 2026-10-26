"""Turn raw strings scraped from Old Hickory pages into the canonical shed schema."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

# Field order here is the order keys appear in inventory.json.
SHED_FIELDS = (
    "inventory_id",
    "style",
    "width_ft",
    "length_ft",
    "siding_color",
    "roof_type",
    "roof_color",
    "cash_price_usd",
    "rto_36mo_usd",
    "rto_60mo_usd",
    "rto_84mo_usd",
    "oh_detail_url",
    "oh_image_urls",
    "image_files",
    "first_seen",
    "last_seen",
)

# Only published on product detail pages, not on the dealer listing.
SPEC_FIELDS = ("siding_color", "roof_type", "roof_color")

PRICE_FIELDS = ("cash_price_usd", "rto_36mo_usd", "rto_60mo_usd", "rto_84mo_usd")

RTO_TERMS = (36, 60, 84)

_MONEY_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)")
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")
_PRODUCT_PATH_RE = re.compile(r"^/product/([A-Za-z0-9]+)/?$")
_RTO_LABEL_RE = re.compile(r"(\d+)\s*-?\s*month\s+rto", re.IGNORECASE)


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace; return None for empty strings."""
    if value is None:
        return None
    text = " ".join(value.split())
    return text or None


def parse_money(value: str | None) -> float | None:
    """'$3,140.75 + tax' -> 3140.75. Returns None if no amount is present."""
    if not value:
        return None
    match = _MONEY_RE.search(value)
    if not match:
        return None
    return round(float(match.group(1).replace(",", "")), 2)


def parse_feet(value: str | None) -> int | float | None:
    """'Width: 8', "8'", '11.33' -> 8 / 8 / 11.33. Whole numbers come back as int."""
    if not value:
        return None
    match = _NUMBER_RE.search(value)
    if not match:
        return None
    number = float(match.group(1))
    return int(number) if number.is_integer() else number


def inventory_id_from_url(url: str | None) -> str | None:
    """Extract the inventory ID from a product URL.

    https://oldhickorybuildings.com/product/01tjw00000j6kkwia3/ -> '01tjw00000j6kkwia3'

    Old Hickory lowercases the ID in product URLs (the S3 image paths keep the
    original Salesforce casing), so we lowercase to keep the key stable.
    """
    if not url:
        return None
    match = _PRODUCT_PATH_RE.match(urlparse(url).path)
    return match.group(1).lower() if match else None


def classify_price_label(label: str | None) -> str | None:
    """Map a pricing label on the page to a schema field name, or None if unknown."""
    text = (clean_text(label) or "").lower()
    rto = _RTO_LABEL_RE.search(text)
    if rto:
        term = int(rto.group(1))
        return f"rto_{term}mo_usd" if term in RTO_TERMS else None
    if "price" in text:
        return "cash_price_usd"
    return None


def normalize_shed(raw: dict, seen_on: str) -> dict:
    """Build a canonical shed record from the raw values a parser extracted.

    `raw` keys: detail_url, style, width, length, siding_color, roof_type,
    roof_color, prices (label -> text), image_urls.
    """
    detail_url = raw.get("detail_url")
    shed = dict.fromkeys(SHED_FIELDS)
    shed.update(
        inventory_id=inventory_id_from_url(detail_url),
        style=clean_text(raw.get("style")),
        width_ft=parse_feet(raw.get("width")),
        length_ft=parse_feet(raw.get("length")),
        siding_color=clean_text(raw.get("siding_color")),
        roof_type=clean_text(raw.get("roof_type")),
        roof_color=clean_text(raw.get("roof_color")),
        oh_detail_url=detail_url,
        oh_image_urls=_dedupe(raw.get("image_urls") or []),
        image_files=[],
        first_seen=seen_on,
        last_seen=seen_on,
    )
    for label, text in (raw.get("prices") or {}).items():
        field = classify_price_label(label)
        # First label wins, so an "Internet Price" is not overwritten by a later
        # strikethrough/regular price block.
        if field and shed[field] is None:
            shed[field] = parse_money(text)
    return shed


def merge_detail(shed: dict, detail: dict) -> dict:
    """Fill gaps in a listing-derived shed from a parsed detail page.

    Listing values win for fields both pages carry; detail images are appended
    after the listing hero image.
    """
    merged = dict(shed)
    for field in SPEC_FIELDS:
        if merged.get(field) is None:
            merged[field] = clean_text(detail.get(field))
    merged["oh_image_urls"] = _dedupe(
        list(shed.get("oh_image_urls") or []) + list(detail.get("image_urls") or [])
    )
    return merged


def needs_detail(shed: dict, previous: dict | None) -> bool:
    """Should we fetch this shed's detail page on this run?

    Yes for sheds we haven't seen, sheds still missing any siding/roof value,
    and sheds whose listing hero photo changed (the gallery likely changed too).
    """
    if previous is None:
        return True
    if any(previous.get(field) is None for field in SPEC_FIELDS):
        return True
    return (previous.get("oh_image_urls") or [])[:1] != (shed.get("oh_image_urls") or [])[:1]


def carry_forward_detail(shed: dict, previous: dict) -> dict:
    """Reuse the detail-page data captured on an earlier run."""
    return merge_detail(
        shed,
        {
            **{field: previous.get(field) for field in SPEC_FIELDS},
            "image_urls": previous.get("oh_image_urls") or [],
        },
    )


def apply_seen_dates(sheds: Iterable[dict], previous: Iterable[dict], today: str) -> list[dict]:
    """Carry first_seen forward from the previous inventory; stamp last_seen = today."""
    first_seen = {s["inventory_id"]: s.get("first_seen") for s in previous}
    result = []
    for shed in sheds:
        updated = dict(shed)
        updated["first_seen"] = first_seen.get(shed["inventory_id"]) or today
        updated["last_seen"] = today
        result.append(updated)
    return result


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
