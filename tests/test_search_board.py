"""Regression tests for the Kanban board card search.

The search bar must match a card's System property in addition to its
title, description and tags -- system names are how cards are grouped by
subsystem, so they must be findable.

The matching rules are pure JS (``static/search.js``), unit-tested in
``tests/test_search_filter.js`` via ``tests/test_search_js.py``. These tests
cover the wiring: the page must load the helper that main.js calls.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "agent1" / "static"


def _index_body() -> str:
    app = create_app()
    app.config["TESTING"] = True
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    return resp.data.decode("utf-8")


def test_index_loads_search_helper_before_main_js():
    """search.js must be loaded, and before main.js uses it."""
    body = _index_body()
    scripts = re.findall(r'<script src="([^"]+)"', body)
    assert any("search.js" in s for s in scripts), scripts
    assert scripts.index(next(s for s in scripts if "search.js" in s)) < scripts.index(
        next(s for s in scripts if "main.js" in s)
    )


def test_search_placeholder_mentions_system():
    body = _index_body()
    placeholder = re.search(r'id="search"[^>]*placeholder="([^"]+)"', body, re.S)
    assert placeholder, "search input not found"
    assert "system" in placeholder.group(1).lower()


def test_search_helper_is_served_and_defines_matches_query():
    app = create_app()
    app.config["TESTING"] = True
    resp = app.test_client().get("/static/search.js")
    assert resp.status_code == 200
    source = resp.data.decode("utf-8")
    assert "matchesQuery" in source
    assert "card.system" in source


def test_board_payload_exposes_system_for_client_search():
    """The client can only search a property the API actually sends."""
    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    frame = c.post(
        "/api/frames",
        json={"title": "Backlog"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).get_json()
    card = c.post(
        "/api/cards",
        json={
            "frame_id": frame["id"],
            "title": "Add invoice export",
            "text": "",
            "tags": [],
            "system": "Billing Service",
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
    ).get_json()
    assert card["system"] == "Billing Service"

    board = c.get("/api/frames/board").get_json()
    systems = [card["system"] for f in board for card in f["cards"]]
    assert systems == ["Billing Service"]


def test_main_js_delegates_search_to_helper():
    """main.js must not keep a second, system-blind copy of the rules."""
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    assert "CardSearch.matchesQuery(card, state.query)" in main_js
    # The old inline filter returned early on title; that blind copy is gone.
    assert "function matchesQuery(card) {\n  const q" not in main_js
