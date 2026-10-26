"""Offline stand-ins for requests.Session used by the pipeline tests."""

from __future__ import annotations

import io
from collections.abc import Callable

import requests
from PIL import Image


def image_bytes(fmt: str = "JPEG", size: tuple[int, int] = (8, 6), color="red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format=fmt)
    return out.getvalue()


class FakeResponse:
    def __init__(self, status_code: int = 200, body: bytes | str = b""):
        self.status_code = status_code
        self.content = body.encode("utf-8") if isinstance(body, str) else body
        self.text = self.content.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


Route = FakeResponse | Callable[[str], FakeResponse]


class RoutingSession:
    """Serves canned responses by exact URL, then by URL prefix; 404 otherwise."""

    def __init__(self, routes: dict[str, Route] | None = None):
        self.routes: dict[str, Route] = dict(routes or {})
        self.requested: list[str] = []

    def get(self, url: str, timeout=None) -> FakeResponse:
        self.requested.append(url)
        route = self.routes.get(url)
        if route is None:
            route = next((r for prefix, r in self.routes.items() if url.startswith(prefix)), None)
        if route is None:
            return FakeResponse(404)
        return route(url) if callable(route) else route

    def requests_matching(self, fragment: str) -> list[str]:
        return [url for url in self.requested if fragment in url]
