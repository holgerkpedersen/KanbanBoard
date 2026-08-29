import os
from pathlib import Path
from typing import Dict

from flask import Request, Response

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'{nonce}; "
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
    nonce_src = f" 'nonce-{nonce}'" if nonce else ""
    return {
        "Content-Security-Policy": CSP_POLICY.format(nonce=nonce_src),
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }


def apply_security_headers(response: Response, nonce: str | None = None) -> Response:
    headers = get_security_headers(nonce)
    for key, value in headers.items():
        response.headers[key] = value
    return response


def validate_length(text: str, max_len: int) -> str:
    if len(text) > max_len:
        raise ValueError(f"Input exceeds maximum length of {max_len} characters")
    return text


def validate_frame_title(title: str) -> str:
    return validate_length(title.strip(), MAX_FRAME_TITLE_LEN)


def validate_card_title(title: str) -> str:
    return validate_length(title.strip(), MAX_CARD_TITLE_LEN)


def validate_card_text(text: str) -> str:
    return validate_length(text, MAX_CARD_TEXT_LEN)


def validate_card_system(system: str) -> str:
    return validate_length(system.strip(), MAX_CARD_SYSTEM_LEN)


def has_csrf_header(request: Request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


# Windows-illegal characters in any path component. We reject paths
# containing them outright rather than try to sanitize, so callers get
# a clear error.
_ILLEGAL_PATH_CHARS = set('<>"|?*\x00')


def validate_board_path(raw: str, *, require_exists: bool = False) -> str:
    """Validate a board file path supplied by the client.

    Rules:
      * must be a non-empty string
      * must not contain null bytes or Windows-illegal characters
      * must be an absolute path (the string itself, before resolving)
      * must end in ``.json`` (case-insensitive)
      * the parent directory must exist (or be creatable by the server)
      * if ``require_exists`` is True, the file itself must also exist

    Returns the resolved absolute path as a string. Raises ``ValueError``
    on any failure (caught by the route layer to return 400).
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("path is required")
    if any(ch in _ILLEGAL_PATH_CHARS for ch in raw):
        raise ValueError("path contains illegal characters")
    # Reject relative paths up-front: on Windows ``Path("foo.json").resolve()``
    # silently becomes an absolute path under the process CWD, which is not
    # what the user typed. Only accept paths that are already absolute.
    p = Path(raw)
    if not p.is_absolute():
        raise ValueError("path must be absolute")
    try:
        resolved = p.resolve()
    except OSError as exc:
        raise ValueError(f"invalid path: {exc}") from exc
    if not resolved.is_absolute():
        raise ValueError("path must be absolute")
    if resolved.suffix.lower() != ".json":
        raise ValueError("path must point to a .json file")
    parent = resolved.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError("parent directory does not exist")
    if not os.access(str(parent), os.W_OK):
        raise ValueError("parent directory is not writable")
    if require_exists and not resolved.exists():
        raise ValueError("file does not exist")
    return str(resolved)


def validate_board_name(name: str) -> str:
    """Validate a board display name. Strips, caps at 100 chars, no separators."""
    if not isinstance(name, str):
        raise ValueError("name is required")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("name is required")
    if len(cleaned) > 100:
        raise ValueError("name exceeds maximum length of 100 characters")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError("name must not contain path separators")
    return cleaned