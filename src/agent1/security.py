from typing import Dict

from flask import Request, Response

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