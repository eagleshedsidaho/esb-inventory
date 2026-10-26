"""Cache shed photos from Old Hickory's S3 bucket into docs/images/.

Files are named "{inventory_id}_{index}.jpg", where index is the photo's
position in the shed's oh_image_urls (0 = hero). A file is reused when it
exists and the URL at that position is unchanged since the previous run;
otherwise it is (re)downloaded. Every download is validated with Pillow and
anything that isn't a real JPEG or PNG is rejected. Accepted photos are
re-encoded as JPEG no larger than MAX_EDGE_PX on the long edge, upright per
their EXIF orientation, with metadata (including any GPS tags) stripped.

Files for sheds no longer in inventory are pruned after each successful run.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from scraper.scrape import REQUEST_TIMEOUT

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_EDGE_PX = 1600
JPEG_QUALITY = 82
ALLOWED_FORMATS = {"JPEG", "PNG"}
IMAGES_URL_PREFIX = "images/"  # relative to docs/, where the widget is served
# Only files matching our own naming scheme are ever pruned.
_CACHE_FILE_RE = re.compile(r"^[0-9a-z]+_\d+\.(jpg|tmp)$")

log = logging.getLogger("scraper.images")


class InvalidImage(ValueError):
    pass


@dataclass
class ImageStats:
    downloaded: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    resized: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def image_filename(inventory_id: str, index: int) -> str:
    return f"{inventory_id}_{index}.jpg"


def prepare_image(data: bytes) -> bytes:
    """Validate image bytes and return the web-sized JPEG to write to disk.

    Raises InvalidImage for anything that isn't a decodable JPEG or PNG,
    including truncated/corrupt files and HTML error pages.
    """
    if not data:
        raise InvalidImage("empty response")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidImage(f"{len(data)} bytes exceeds {MAX_IMAGE_BYTES}")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ALLOWED_FORMATS:
            raise InvalidImage(f"unsupported format {fmt}")
        # verify() doesn't decode pixel data; load() catches truncated files.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            upright = ImageOps.exif_transpose(image).convert("RGB")
        upright.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        upright.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return out.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise InvalidImage(str(exc)) from exc


def cache_images(
    sheds: list[dict],
    previous_by_id: dict[str, dict],
    images_dir: Path,
    session: requests.Session | None,
) -> tuple[list[dict], ImageStats]:
    """Download missing/changed photos and set each shed's image_files."""
    images_dir.mkdir(parents=True, exist_ok=True)
    stats = ImageStats()
    result = []
    for shed in sheds:
        shed_id = shed["inventory_id"]
        previous_urls = (previous_by_id.get(shed_id) or {}).get("oh_image_urls") or []
        files = []
        for index, url in enumerate(shed.get("oh_image_urls") or []):
            name = image_filename(shed_id, index)
            path = images_dir / name
            unchanged = index < len(previous_urls) and previous_urls[index] == url
            if path.exists() and unchanged:
                if _needs_reencode(path):
                    _write_atomic(path, prepare_image(path.read_bytes()))
                    stats.resized.append(name)
                stats.reused.append(name)
                files.append(IMAGES_URL_PREFIX + name)
                continue
            if session is None:  # offline run: only cached files can be used
                stats.skipped.append(name)
                continue
            try:
                response = session.get(url, timeout=REQUEST_TIMEOUT)
                response.raise_for_status()
                data = prepare_image(response.content)
            except (requests.RequestException, InvalidImage) as exc:
                log.warning("image %s (%s) rejected: %s", name, url, exc)
                stats.failed.append(name)
                continue
            _write_atomic(path, data)
            stats.downloaded.append(name)
            files.append(IMAGES_URL_PREFIX + name)
        result.append({**shed, "image_files": files})
    return result, stats


def prune_images(images_dir: Path, keep: set[str]) -> list[str]:
    """Delete cached photos not referenced by the current inventory.

    `keep` holds image_files entries ("images/<name>.jpg"). Returns deleted names.
    """
    if not images_dir.exists():
        return []
    keep_names = {Path(entry).name for entry in keep}
    deleted = []
    for path in sorted(images_dir.iterdir()):
        if path.is_file() and _CACHE_FILE_RE.match(path.name) and path.name not in keep_names:
            path.unlink()
            deleted.append(path.name)
    return deleted


def _needs_reencode(path: Path) -> bool:
    """True for cached files from before resizing/metadata stripping existed."""
    try:
        with Image.open(path) as image:
            return max(image.size) > MAX_EDGE_PX or bool(image.getexif())
    except (UnidentifiedImageError, OSError):
        return False


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
