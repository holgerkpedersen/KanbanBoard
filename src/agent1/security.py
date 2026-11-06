"""Security utilities for the Kanban board service.

Provides:
- Content-Security-Policy and other HTTP security headers.
- CSRF token generation and double-submit validation (replaces the old,
  spoofable ``X-Requested-With`` check).
- Optional API-key authentication when an environment variable is set.
- Input-length validation for frames, cards, and system fields.
"""

from __future__ import annotations

import os
import secrets
from typing import Any, Dict, Optional

from flask import Request, Response, g


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
# CSRF token protection (double-submit cookie pattern).

# How it works
# ------------
# 1. On each request the ``before_request`` hook in *app.py* generates a
#    cryptographically random token and stores it on :data:`g` as
#    ``g.csrf_token``.  The value is also returned to the client inside the
#    HTML response body (via a <meta> tag) so the frontend JavaScript can
#    read it.
# 2. Every write endpoint (POST / PUT / DELETE on /api/*) MUST include that
#    token in an ``X-CSRF-Token`` request header whose value exactly matches
#    the stored token.
# 3. The centralized :func:`validate_csrf` helper is called from the
#    ``before_request`` hook; individual route handlers no longer call
#    ``has_csrf_header``.
# ---------------------------------------------------------------------------


def generate_csrf_token() -> str:
    """Generate a cryptographically random CSRF token."""
    return secrets.token_hex(32)  # 64 hex characters = 256 bits of entropy


def validate_csrf(request: Request) -> bool:
    """Validate the double-submit CSRF token.

    Returns ``True`` when either (a) the request method is safe (GET, HEAD,
    OPTIONS) or (b) both ``X-CSRF-Token`` header and stored token match.
    Sets ``g.csrf_error`` to a human-readable message on failure so the
    caller can return an appropriate response without leaking internals.

    This replaces the old ``has_csrf_header()`` which only checked for the
    spoofable ``X-Requested-With: XMLHttpRequest`` header.
    """
    # Safe methods never need CSRF validation.
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True

    token = getattr(g, "csrf_token", None)
    header_token = request.headers.get("X-CSRF-Token")

    if not token or not header_token:
        g.csrf_error = "CSRF token missing"
        return False

    # Constant-time comparison to prevent timing side-channels.
    return secrets.compare_digest(token, header_token)


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

    # CSRF path (when no API key is configured).
    if not validate_csrf(request):
        error_msg = getattr(g, "csrf_error", "CSRF validation failed")
        return {"error": error_msg}

    return None
