from typing import Dict

from flask import Request, Response

CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
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


def get_security_headers() -> Dict[str, str]:
    return {
        "Content-Security-Policy": CSP_POLICY,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }


def apply_security_headers(response: Response) -> Response:
    headers = get_security_headers()
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


def has_csrf_header(request: Request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"