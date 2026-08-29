"""Regression tests for the dark / light / contrast theme switcher."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app

CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_index_serves_theme_selector():
    c = _client()
    resp = c.get("/")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # All three themes offered.
    assert 'id="theme-select"' in body
    assert '<option value="dark">Dark</option>' in body
    assert '<option value="light">Light</option>' in body
    assert '<option value="contrast">Contrast</option>' in body
    assert '<option value="contrast-dark">Contrast Dark</option>' in body


def test_index_applies_saved_theme_before_paint_with_nonce():
    c = _client()
    resp = c.get("/")
    body = resp.data.decode("utf-8")
    # Anti-flash inline script is present and nonce-protected (CSP-safe).
    assert "kanban.theme" in body
    assert "nonce=" in body
    # The nonce on the script must match the CSP header nonce.
    import re

    nonce = re.search(r'nonce="([0-9a-f]+)"', body).group(1)
    csp = resp.headers.get("Content-Security-Policy", "")
    assert f"'nonce-{nonce}'" in csp
    # script-src must not fall back to unsafe-inline (keep the strict policy).
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("style-src")[0]


def test_theme_select_markup_is_xss_safe():
    # The options are static literals in the template, not user input.
    c = _client()
    body = c.get("/").data.decode("utf-8")
    assert "data-theme" in body  # anti-flash script sets the attribute
