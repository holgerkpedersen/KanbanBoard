"""Security utilities for the Kanban board service.

Provides:
- Content-Security-Policy and other HTTP security headers.
- CSRF token generation and double-submit validation (replaces the old,
  spoofable ``X-Requested-With`` check).
- Optional API-key authentication when an environment variable is set.
- Input-length validation for frames, cards, and system fields.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Any, Dict, Optional

from flask import Flask, Request, Response, current_app, g


# ---------------------------------------------------------------------------
# HTTP security headers
# ---------------------------------------------------------------------------

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' {nonce}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

MAX_FRAME_TITLE_LEN = 100
MAX_CARD_TITLE_LEN = 200
MAX_CARD_TEXT_LEN = 2000
MAX_CARD_SYSTEM_LEN = 60


def get_security_headers(nonce: str | None = None) -> Dict[str, str]:
    """Return a dict of security header name → value.

    Parameters
    ----------
    nonce:
        A one-time random string embedded in the CSP ``script-src`` directive.
        Pass the same value to :func:`render_secure_template` so the server-
        side nonce matches the client-side ``<meta>`` tag.
    """
    nonce_src = f" 'nonce-{nonce}'" if nonce else ""
    return {
        "Content-Security-Policy": CSP_POLICY.format(nonce=nonce_src),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        # HSTS — force HTTPS for 1 year including subdomains.
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }


def get_api_headers() -> Dict[str, str]:
    """Return headers specific to API responses (no nonce needed).

    - ``Cache-Control: no-store`` prevents caching of sensitive board data.
    - ``Permissions-Policy`` disables unnecessary browser features.
    - ``Cross-Origin-Opener-Policy`` isolates the browsing context.
    """
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Cross-Origin-Opener-Policy": "same-origin",
    }


def apply_security_headers(response: Response, nonce: str | None = None) -> Response:
    """Attach security headers to a Flask response object."""
    headers = get_security_headers(nonce)
    for key, value in headers.items():
        response.headers[key] = value
    return response


def apply_api_headers(response: Response) -> Response:
    """Attach API-specific headers (Cache-Control, Permissions-Policy, etc.)."""
    headers = get_api_headers()
    for key, value in headers.items():
        response.headers[key] = value
    return response




# ---------------------------------------------------------------------------
# Input validation (length checks only — content sanitisation is done by the
# CSP which blocks inline scripts and restricts ``connect-src``).
# ---------------------------------------------------------------------------


def validate_length(text: str, max_len: int) -> str:
    """Return *text* stripped of surrounding whitespace, or raise on overflow."""
    trimmed = text.strip() if isinstance(text, str) else ""
    if len(trimmed) > max_len:
        raise ValueError(f"Input exceeds maximum length of {max_len} characters")
    return trimmed


def validate_frame_title(title: str) -> str:
    return validate_length(title, MAX_FRAME_TITLE_LEN)


def validate_card_title(title: str) -> str:
    return validate_length(title, MAX_CARD_TITLE_LEN)


def validate_card_text(text: str) -> str:
    return validate_length(text, MAX_CARD_TEXT_LEN)


def validate_card_system(system: str) -> str:
    return validate_length(system, MAX_CARD_SYSTEM_LEN)


# ---------------------------------------------------------------------------
# CSRF token protection (signed double-submit cookie pattern).

# How it works
# ------------
# 1. The server issues a **signed** token: ``payload.signature`` where the
#    signature is HMAC-SHA256 over the payload with a server secret.  The
#    ``before_request`` hook in *app.py* calls :func:`issue_csrf_token`, which
#    reuses the cookie the browser already has (so the token survives page
#    reloads and parallel tabs) or mints a new one.
# 2. The token is handed to the client twice: inside the HTML body via the
#    ``<meta name="csrf-token">`` tag, and as the ``kanban_csrf`` cookie.
#    JavaScript reads the meta tag and sends the value back in the
#    ``X-CSRF-Token`` request header on every write (POST / PUT / DELETE) to
#    ``/api/*``.
# 3. :func:`validate_csrf` requires the header to **match the cookie** (the
#    double-submit property: a cross-origin attacker can make a request carry
#    the cookie but cannot read it back, so it cannot reproduce the header)
#    **and** the signature to verify with the server secret (so an attacker
#    who sets ``kanban_csrf`` from a response header cannot forge a token that
#    passes).  A per-request random token cannot work: the value embedded in a
#    page belongs to the GET that rendered it, and would be gone by the time
#    the browser makes its first write.
# 4. The centralized :func:`validate_csrf` helper runs from ``require_auth``;
#    individual route handlers do not call ``has_csrf_header``.
# ---------------------------------------------------------------------------

CSRF_COOKIE_NAME = "kanban_csrf"
_CSRF_ENTROPY_BYTES = 16  # 128 bits of entropy in the payload
_CSRF_SIG_HEX_LEN = 32    # truncated HMAC-SHA256 (128 bits) is ample here

#: How long a minted token stays valid, in seconds.  A token carries its own
#: issue time inside the signed payload, so expiry needs no server-side state:
#: past this window the signature still verifies but the token is refused and
#: a fresh one is issued on the next page load.  Overriding via
#: ``KANBAN_CSRF_MAX_AGE`` lets an operator trade re-issue frequency against
#: exposure of a stolen token.
_DEFAULT_CSRF_MAX_AGE = 12 * 3600  # 12 hours — a working day and a half


def get_csrf_max_age() -> int:
    """Return the token lifetime in seconds (env override, default 12 hours).

    A malformed or non-positive ``KANBAN_CSRF_MAX_AGE`` falls back to the
    default rather than disabling expiry: an unset/typo'd value must not
    silently restore the old never-expires behaviour.
    """
    raw = os.environ.get("KANBAN_CSRF_MAX_AGE", "").strip()
    if not raw:
        return _DEFAULT_CSRF_MAX_AGE
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_CSRF_MAX_AGE
    return value if value > 0 else _DEFAULT_CSRF_MAX_AGE


def _csrf_secret(app: Flask | None = None) -> bytes:
    """Return the app's HMAC secret, minting and caching one on first use.

    The secret lives in ``app.config`` only — it never reaches a cookie or a
    template.  A value supplied via ``KANBAN_CSRF_SECRET`` survives restarts;
    otherwise a random per-process secret is used, which simply invalidates
    open pages when the service restarts (a page reload re-issues the token).
    """
    if app is not None:
        cached = app.config.get("CSRF_SECRET")
        if cached:
            return cached.encode() if isinstance(cached, str) else cached

    secret = os.environ.get("KANBAN_CSRF_SECRET", "")
    if not secret:
        secret = secrets.token_hex(32)
    if app is not None:
        app.config["CSRF_SECRET"] = secret
    return secret.encode()


def _csrf_signature(payload: str, secret: bytes) -> str:
    """Truncated HMAC-SHA256 of *payload*, hex-encoded."""
    digest = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return digest[:_CSRF_SIG_HEX_LEN]


def generate_csrf_token(secret: bytes | None = None, max_age: int | None = None) -> str:
    """Mint a signed CSRF token of the form ``<payload>.<signature>``.

    The payload is ``<entropy><issue-time><lifetime>``, all hex: 32 chars of
    random entropy, a 16-char big-endian issue timestamp in seconds, and a
    8-char lifetime in seconds.  All three fields sit inside the signed region,
    so expiry is enforceable with no server-side state and neither the issue
    time nor the lifetime can be stretched without breaking the signature.

    The lifetime is baked in per token (rather than read from the environment
    at validation time) so that changing ``KANBAN_CSRF_MAX_AGE`` mid-flight
    cannot invalidate tokens that were minted under a longer window — a
    short-lived test override must not reject a page's own valid token.
    """
    entropy = secrets.token_hex(_CSRF_ENTROPY_BYTES)
    issued_at = int(time.time())
    lifetime = max_age if max_age is not None else get_csrf_max_age()
    payload = f"{entropy}{issued_at:016x}{lifetime:08x}"
    return f"{payload}.{_csrf_signature(payload, secret or _csrf_secret())}"


def verify_csrf_token(token: str, secret: bytes | None = None) -> bool:
    """Return ``True`` when *token* carries a signature made with *secret*.

    Constant-time comparison, so the check leaks nothing about the secret.
    Signature only — expiry is checked by :func:`csrf_token_is_current`.
    """
    if not token or "." not in token:
        return False
    payload, _, signature = token.partition(".")
    expected = _csrf_signature(payload, secret or _csrf_secret())
    return secrets.compare_digest(expected, signature)


def csrf_token_age(token: str) -> Optional[int]:
    """Return the token's age in seconds, or ``None`` if it carries no issue time.

    Legacy tokens (minted before the payload carried a timestamp) have no
    readable issue time and return ``None``; callers treat that as expired so
    old never-expiring tokens are rotated out rather than accepted forever.
    """
    parsed = _parse_csrf_payload(token)
    if parsed is None:
        return None
    _entropy, issued_at, _lifetime = parsed
    return max(0, int(time.time()) - issued_at)


def csrf_token_is_current(token: str, max_age: int | None = None) -> bool:
    """Return ``True`` when *token* is signed-and-issued within its lifetime.

    Expiry is deliberately a separate check from the signature so that
    :func:*`verify_csrf_token` keeps its single meaning (authenticity).  The
    window defaults to the token's own embedded lifetime; pass ``max_age`` to
    override it (used by tests).  A token with no readable issue time counts as
    expired: it predates expiry support and must not be immortal.
    """
    parsed = _parse_csrf_payload(token)
    if parsed is None:
        return False
    _entropy, issued_at, lifetime = parsed
    age = max(0, int(time.time()) - issued_at)
    return age <= (max_age if max_age is not None else lifetime)


def _parse_csrf_payload(token: str):
    """Split a token payload into ``(entropy, issued_at, lifetime)``.

    Returns ``None`` when the payload is not the current 56-hex-char shape
    (32 entropy + 16 timestamp + 8 lifetime), which covers legacy tokens.
    """
    if not token or "." not in token:
        return None
    payload = token.partition(".")[0]
    if len(payload) != 56:
        return None
    try:
        issued_at = int(payload[32:48], 16)
        lifetime = int(payload[48:], 16)
    except ValueError:
        return None
    return payload[:32], issued_at, lifetime


def is_secure_request(request: Request) -> bool:
    """Return ``True`` when the request arrived over HTTPS.

    The service normally terminates TLS upstream, so Flask sees plain HTTP and
    ``request.is_secure`` is False; the proxy says otherwise via
    ``X-Forwarded-Proto``.  Cookie flags depend on this: a ``Secure`` cookie on
    a plain-HTTP localhost dev server is silently dropped by the browser.
    """
    if request.is_secure:
        return True
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    return forwarded.split(",")[0].strip().lower() == "https"


def issue_csrf_token(request: Request, app: Flask | None = None) -> tuple[str, bool]:
    """Return ``(token, issued_new)`` for the current request.

    Reuses the cookie the client presented when its signature verifies **and**
    it has not expired; mints a fresh token (and reports ``issued_new=True`` so
    the caller sets the cookie) when there is no cookie, when it was
    forged/tampered with, or when it aged past ``KANBAN_CSRF_MAX_AGE``.
    """
    secret = _csrf_secret(app)
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    if verify_csrf_token(cookie_token, secret) and csrf_token_is_current(cookie_token):
        return cookie_token, False
    return generate_csrf_token(secret), True


def validate_csrf(request: Request, app: Flask | None = None) -> bool:
    """Validate the double-submit CSRF token.

    Returns ``True`` when either (a) the request method is safe (GET, HEAD,
    OPTIONS), (b) the request carries a valid API key (checked by the caller,
    :func:`require_auth`), or (c) the ``X-CSRF-Token`` header matches the
    ``kanban_csrf`` cookie **and** the token's signature verifies.

    Under ``TESTING`` the fixed ``g.csrf_token`` is accepted, so the suite can
    exercise the real code path without a cookie jar.

    Sets ``g.csrf_error`` to a human-readable message on failure so the caller
    can return an appropriate response without leaking internals.

    This replaces the old ``has_csrf_header()`` which only checked for the
    spoofable ``X-Requested-With: XMLHttpRequest`` header.
    """
    # Safe methods never need CSRF validation.
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True

    header_token = request.headers.get("X-CSRF-Token")
    if not header_token:
        g.csrf_error = "CSRF token missing"
        return False

    # Test shortcut: a fixed token configured by the suite (see create_app).
    fixed = getattr(g, "csrf_test_token", None)
    if fixed and secrets.compare_digest(fixed, header_token):
        return True

    secret = _csrf_secret(app)

    # Double-submit: the header must equal the cookie.  A cross-origin page
    # can force the browser to send the cookie, but cannot read it, so it
    # cannot put the same value in a header.
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not cookie_token:
        g.csrf_error = "CSRF token missing"
        return False
    if not secrets.compare_digest(cookie_token, header_token):
        g.csrf_error = "CSRF validation failed"
        return False

    # Signature: proves the pair was issued by this server.  Without it, any
    # response that echoes a value in a Set-Cookie would let an attacker
    # manufacture a matching (cookie, header) pair.
    if not verify_csrf_token(header_token, secret):
        g.csrf_error = "CSRF validation failed"
        return False

    # Expiry: the issue time is inside the signed payload, so a stolen token
    # stops working once it ages past the window.  A valid-signature-but-stale
    # token gets its own message so the client can prompt a reload.
    if not csrf_token_is_current(header_token):
        g.csrf_error = "CSRF token expired"
        return False

    return True


# ---------------------------------------------------------------------------
# Legacy compatibility — kept for importers but no longer used by routes.
# ---------------------------------------------------------------------------


def has_csrf_header(request: Request) -> bool:
    """Legacy check using ``X-Requested-With`` header.

    .. deprecated::
        This function is spoofable from any cross-origin context and must
        not be used for new endpoints.  All write operations are now
        protected by the centralized CSRF middleware in *app.py*.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


# ---------------------------------------------------------------------------
# Optional API-key authentication.

# When ``KANBAN_API_KEY`` is set to a non-empty value, every write endpoint
# (POST / PUT / DELETE on /api/*) must present the key via the
# ``X-API-Key`` request header as an alternative to CSRF validation.  If no
# API key is configured, only CSRF validation applies (existing behaviour).

# This allows a single-user deployment behind a reverse proxy with its own
# auth layer (e.g. nginx basic-auth or OAuth) to keep the Kanban app open
# while still protecting write operations from same-origin attacks.  A
# multi-user deployment can gate API-key access to specific keys and extend
# this module with per-user ACLs later.
# ---------------------------------------------------------------------------


def get_api_key() -> Optional[str]:
    """Return the configured API key, or ``None`` if not set."""
    return os.environ.get("KANBAN_API_KEY", "").strip() or None


def validate_api_key(request: Request) -> bool:
    """Check whether the request carries a valid API key.

    Returns ``True`` when no API key is configured (opt-in by env var) or
    when the provided key matches.  Only meaningful for write endpoints;
    GET endpoints are always accessible regardless of auth status.
    """
    configured = get_api_key()
    if not configured:
        return True  # opt-in: no key means no API-key requirement

    provided = request.headers.get("X-API-Key", "")
    return secrets.compare_digest(provided, configured)


def require_auth(request: Request) -> Optional[Dict[str, Any]]:
    """Centralised auth check for write endpoints.

    Parameters
    ----------
    request:
        The incoming Flask request object (must already have ``g.csrf_token``
        set by the ``before_request`` hook).

    Returns
    -------
    ``None`` when authentication passes, or a dict with an ``"error"`` key
    suitable for returning as a JSON response body.
    """
    # Fast path: safe methods never need auth.
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None

    # API-key path (when configured): takes precedence over CSRF.
    api_key = get_api_key()
    if api_key and validate_api_key(request):
        return None

    # If an API key is configured but missing/invalid → reject immediately.
    if api_key:
        g.csrf_error = "unauthorized"
        return {"error": "unauthorized"}

    # CSRF path (when no API key is configured).  Pass the app so validation
    # can reach the HMAC secret stored in its config.
    if not validate_csrf(request, current_app):
        error_msg = getattr(g, "csrf_error", "CSRF validation failed")
        return {"error": error_msg}

    return None
