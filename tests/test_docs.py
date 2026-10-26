"""Keep the README embed snippet, the local embed test page, and helpers in sync."""

from __future__ import annotations

import re
from pathlib import Path

from scraper import fetch_fixture, scrape
from tests.fakes import FakeResponse, RoutingSession

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent
PAGES_SRC = "https://{username}.github.io/esb-inventory/widget.html"


def _normalize(snippet: str) -> str:
    return "\n".join(line.strip() for line in snippet.strip().splitlines() if line.strip())


def readme_snippet() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("### The snippet", 1)[1]
    return re.search(r"```html\n(.*?)```", section, re.S).group(1)


def embed_test_snippet() -> str:
    page = (ROOT / "widget" / "embed-test.html").read_text(encoding="utf-8")
    return re.search(r"<!-- SNIPPET START -->(.*?)<!-- SNIPPET END -->", page, re.S).group(1)


def test_readme_snippet_matches_tested_embed_page():
    tested = embed_test_snippet().replace('src="../docs/widget.html"', f'src="{PAGES_SRC}"')
    assert _normalize(readme_snippet()) == _normalize(tested)


def test_readme_has_required_sections():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Embedding in the ESB AI Studio inventory page" in readme
    assert "## Troubleshooting" in readme
    assert PAGES_SRC in readme


def test_snippet_message_types_match_widget():
    js = (ROOT / "widget" / "render.js").read_text(encoding="utf-8")
    snippet = readme_snippet()
    for message_type in ("esb-inventory:height", "esb-inventory:request-height"):
        assert message_type in js
        assert message_type in snippet


def test_fetch_fixture_saves_listing_and_first_detail(tmp_path, monkeypatch):
    listing = (FIXTURES / "snapshot.html").read_text(encoding="utf-8")
    detail = (FIXTURES / "detail_snapshot.html").read_text(encoding="utf-8")
    first = "https://oldhickorybuildings.com/product/01tjw00000j6kkwia3/"
    session = RoutingSession(
        {
            "https://oldhickorybuildings.com/robots.txt": FakeResponse(200, "User-agent: *\n"),
            scrape.SOURCE_URL: FakeResponse(200, listing),
            first: FakeResponse(200, detail),
        }
    )
    monkeypatch.setattr(scrape, "make_session", lambda: session)
    monkeypatch.setattr(fetch_fixture, "TESTS_DIR", tmp_path)

    assert fetch_fixture.main() == 0
    assert (tmp_path / "snapshot.html").read_text(encoding="utf-8") == listing
    assert (tmp_path / "detail_snapshot.html").read_text(encoding="utf-8") == detail
    assert session.requested[-1] == first
