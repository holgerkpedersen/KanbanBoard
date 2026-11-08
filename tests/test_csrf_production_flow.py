"""CSRF must work for a real browser, not only under ``TESTING``.

The suite runs the app with ``TESTING=True``, where ``create_app`` installs a
fixed token (see ``tests/conftest.py``) so every write passes the double-submit
check.  That shortcut hid a production-only defect: ``before_request`` used to
mint a *new* random token on **every** request, so the token in the page's
``<meta name="csrf-token">`` tag belonged to the GET that rendered it and could
never match the token the server expected on the following POST.  Every
mutating call from a real browser returned 403.

These tests run the app with ``TESTING=False`` — the configuration the shipped
service actually uses — and follow the browser's exact path: read the token out
of the served HTML, send it back in ``X-CSRF-Token``, expect the write to
succeed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


_META_TOKEN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


@pytest.fixture()
def prod_client(monkeypatch):
    """A test client for an app built the way production builds it.

    ``create_app()`` with no arguments is hermetic — in-memory store, throwaway
    temp dirs, no sync thread — so this cannot touch the user's live board.
    ``app.config["TESTING"]`` is left False (it is False by default in
    ``create_app``), which is the configuration the shipped service runs with;
    every other test in the suite flips it to True and gets the fixed token.

    Imported inside the fixture: the suite puts the repo root on ``sys.path``
    in each test module (see ``test_api.py``) and imports through the ``src``
    package, so this module has to do the same.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.agent1 import create_app

    monkeypatch.delenv("KANBAN_API_KEY", raising=False)
    app = create_app()
    assert app.config["TESTING"] is False, "fixture must exercise the production path"
    with app.test_client() as client:
        yield client


def _page_token(client) -> str:
    """The CSRF token a browser would read from the served page."""
    res = client.get("/")
    assert res.status_code == 200
    match = _META_TOKEN.search(res.get_data(as_text=True))
    assert match, "served page has no csrf-token meta tag"
    return match.group(1)


def test_page_token_is_accepted_by_a_write(prod_client):
    """The token in the page must be valid for the next mutating request."""
    token = _page_token(prod_client)

    res = prod_client.post(
        "/api/frames",
        json={"title": "Backlog"},
        headers={"X-CSRF-Token": token},
    )

    assert res.status_code == 201, res.get_data(as_text=True)


def test_write_without_token_is_rejected(prod_client):
    """Dropping the header must still be refused — the fix keeps enforcement."""
    _page_token(prod_client)

    res = prod_client.post("/api/frames", json={"title": "Backlog"})

    assert res.status_code == 403


def test_write_with_forged_token_is_rejected(prod_client):
    """A token the server never issued must be refused (signature checked)."""
    _page_token(prod_client)

    res = prod_client.post(
        "/api/frames",
        json={"title": "Backlog"},
        headers={"X-CSRF-Token": "deadbeef" * 8},
    )

    assert res.status_code == 403


def test_token_survives_a_second_write(prod_client):
    """The token is not single-use: two writes in a row both succeed.

    A per-request token would be consumed by the first write and fail the
    second, which is the shape of the original bug.
    """
    token = _page_token(prod_client)

    first = prod_client.post(
        "/api/frames", json={"title": "A"}, headers={"X-CSRF-Token": token}
    )
    second = prod_client.post(
        "/api/frames", json={"title": "B"}, headers={"X-CSRF-Token": token}
    )

    assert first.status_code == 201, first.get_data(as_text=True)
    assert second.status_code == 201, second.get_data(as_text=True)


def test_token_is_stable_across_requests(prod_client):
    """Reloading the page must not invalidate the token the client already has.

    The browser keeps one token in the meta tag; if a reload swapped it for a
    new value, an in-flight request carrying the old one would start failing.
    """
    first = _page_token(prod_client)
    prod_client.post(
        "/api/frames", json={"title": "A"}, headers={"X-CSRF-Token": first}
    )
    second = _page_token(prod_client)

    assert second == first
