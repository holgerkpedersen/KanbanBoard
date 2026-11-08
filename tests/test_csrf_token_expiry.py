"""CSRF tokens must expire.

Tokens used to be immortal: the payload was pure randomness, so a token that
once verified kept verifying forever.  A stolen ``kanban_csrf`` cookie stayed
usable indefinitely.  The fix moves the issue time *inside* the signed payload
(``<entropy><issue-time>.<signature>``), so expiry is enforceable with no
server-side state and cannot be extended without breaking the signature.

These tests cover both halves: a token past its window is refused, and a token
inside the window keeps working (the fix must not break normal writes).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1.security import (  # noqa: E402
    csrf_token_age,
    csrf_token_is_current,
    generate_csrf_token,
    get_csrf_max_age,
    verify_csrf_token,
)


_SECRET = b"test-secret-for-csrf-expiry"


def _token_issued_at(
    issued_at: int, secret: bytes = _SECRET, lifetime: int = 3600
) -> str:
    """Mint a token whose signed payload claims it was issued at *issued_at*."""
    import secrets

    from src.agent1.security import _csrf_signature

    entropy = secrets.token_hex(16)
    payload = f"{entropy}{issued_at:016x}{lifetime:08x}"
    return f"{payload}.{_csrf_signature(payload, secret)}"


# --- unit: the token carries its own age ----------------------------------

def test_fresh_token_is_current():
    token = generate_csrf_token(_SECRET)
    assert csrf_token_age(token) is not None
    assert csrf_token_age(token) <= 1
    assert csrf_token_is_current(token, max_age=60)


def test_old_token_is_not_current():
    """A correctly-signed token from beyond the window must be refused."""
    ten_hours_ago = int(time.time()) - 10 * 3600
    token = _token_issued_at(ten_hours_ago)

    assert verify_csrf_token(token, _SECRET), "signature must still be valid"
    assert not csrf_token_is_current(token, max_age=3600)


def test_age_is_read_from_the_signed_payload():
    issued = int(time.time()) - 120
    token = _token_issued_at(issued)
    age = csrf_token_age(token)
    assert age is not None and 119 <= age <= 122


def test_legacy_token_without_timestamp_is_expired():
    """Pre-expiry tokens (32-char payload) must not be immortal."""
    # A payload that is not exactly entropy+timestamp reads as "no age", and
    # no-age means expired — so old never-expiring tokens get rotated out.
    assert csrf_token_age("aabbcc.signature") is None
    assert not csrf_token_is_current("aabbcc.signature")


def test_tampered_timestamp_breaks_the_signature():
    """Moving the timestamp without re-signing must fail the signature check."""
    token = _token_issued_at(int(time.time()))
    payload, _, sig = token.partition(".")
    forged = f"{payload[:-16]}{'ffffffffffffffff'}.{sig}"

    assert not verify_csrf_token(forged, _SECRET)


# --- unit: lifetime is configurable --------------------------------------

def test_default_max_age_is_bounded(monkeypatch):
    monkeypatch.delenv("KANBAN_CSRF_MAX_AGE", raising=False)
    assert get_csrf_max_age() == 12 * 3600


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("60", 60),
        ("", 12 * 3600),
        ("not-a-number", 12 * 3600),
        ("0", 12 * 3600),
        ("-5", 12 * 3600),
    ],
)
def test_max_age_override_falls_back_safely(monkeypatch, raw, expected):
    """A typo must not disable expiry — it falls back to the default."""
    monkeypatch.setenv("KANBAN_CSRF_MAX_AGE", raw)
    assert get_csrf_max_age() == expected


# --- integration: the production path enforces expiry --------------------

def _make_prod_client(monkeypatch, max_age: str | None = None):
    """Build a production-shaped client (TESTING=False).

    ``KANBAN_CSRF_MAX_AGE`` is read when a token is minted and baked into that
    token's signed payload, so it has to be in the environment *before* the app
    is built — setting it after the fixture yields would have no effect.
    """
    monkeypatch.delenv("KANBAN_API_KEY", raising=False)
    if max_age is None:
        monkeypatch.delenv("KANBAN_CSRF_MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("KANBAN_CSRF_MAX_AGE", max_age)
    from src.agent1 import create_app

    app = create_app()
    assert app.config["TESTING"] is False
    return app.test_client()


@pytest.fixture()
def prod_client(monkeypatch):
    """A client for an app built the way production builds it (TESTING=False)."""
    with _make_prod_client(monkeypatch) as client:
        yield client


def test_expired_token_write_is_rejected(monkeypatch):
    """A stale token that signed correctly must be refused on a write."""
    import re

    # 1-second lifetime is baked into the token minted by the GET below.
    with _make_prod_client(monkeypatch, max_age="1") as client:
        res = client.get("/")
        assert res.status_code == 200
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            res.get_data(as_text=True),
        ).group(1)

        # Let the token age past its own embedded 1-second lifetime.
        time.sleep(1.5)

        res = client.post(
            "/api/frames",
            json={"title": "Backlog"},
            headers={"X-CSRF-Token": token},
        )

    assert res.status_code == 403, res.get_data(as_text=True)
    assert "expired" in res.get_json().get("error", "").lower()


def test_current_token_write_still_succeeds(prod_client):
    """Expiry must not break the normal browser flow."""
    import re

    res = prod_client.get("/")
    token = re.search(
        r'<meta name="csrf-token" content="([^"]+)"', res.get_data(as_text=True)
    ).group(1)

    res = prod_client.post(
        "/api/frames", json={"title": "Backlog"}, headers={"X-CSRF-Token": token}
    )
    assert res.status_code == 201, res.get_data(as_text=True)
