"""Application factory for the Kanban board service.

The app is single-process and single-user; "multi-board" means we can
hold any number of board files on disk and switch which one is loaded
into memory at a time. The board-management endpoints live in
``routes_boards.py``; this module wires them up to a
:class:`BoardRegistry` and a per-app "live store" holder so that the
existing frame/card blueprints keep working unchanged.
"""

from __future__ import annotations

import os
import secrets
import threading
from typing import Optional

from flask import Flask, Response, request, render_template, make_response, g

from .store import BoardStore
from .board_registry import BoardRegistry
from .routes_frames import create_frame_blueprint
from .routes_cards import create_card_blueprint
from .routes_boards import create_boards_blueprint
from .security import apply_security_headers

# Default on-disk location for the running app. Tests call create_app()
# with no path, so they stay purely in-memory and deterministic.
DEFAULT_DATA_PATH = os.environ.get("KANBAN_DATA_PATH", "data/board.json")
DEFAULT_REGISTRY_PATH = os.environ.get(
    "KANBAN_REGISTRY_PATH",
    os.path.join(os.path.dirname(DEFAULT_DATA_PATH) or "data", "boards.json"),
)
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # cap for the open-file upload path


def _make_live_holder() -> dict:
    return {"store": None, "path": None, "lock": threading.RLock()}


def create_app(
    data_path: str | None = None,
    registry_path: str | None = None,
) -> Flask:
    """Build a Flask app.

    Parameters
    ----------
    data_path:
        On-disk path of the initial board file. If the registry is empty
        (e.g. first run), this file is registered and marked active.
        ``None`` (the test-suite default) keeps the app in-memory.
    registry_path:
        On-disk path of the board registry. Defaults to
        ``<dir of data_path>/boards.json`` when ``data_path`` is set,
        else ``data/boards.json``.
    """
    app = Flask(__name__)
    app.config["DEBUG"] = False
    app.config["TESTING"] = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    if registry_path is None:
        if data_path:
            registry_path = os.path.join(
                os.path.dirname(data_path) or "data", "boards.json"
            )
        else:
            registry_path = DEFAULT_REGISTRY_PATH

    registry = BoardRegistry(path=registry_path)
    app.config["BOARD_REGISTRY"] = registry
    app.config["LIVE_STORE_HOLDER"] = _make_live_holder()

    # On startup, if a default data_path was provided, ensure the
    # registry has it (first-run convenience). In tests, data_path is
    # None so the registry stays empty and the live store is None until
    # the first /api/boards call wires it up.
    bootstrap_store: Optional[BoardStore] = None
    if data_path:
        entry = registry.ensure_default(
            name=os.path.splitext(os.path.basename(data_path))[0] or "Board",
            path=data_path,
        )
        bootstrap_store = BoardStore(path=entry["path"])
        holder = app.config["LIVE_STORE_HOLDER"]
        with holder["lock"]:
            holder["store"] = bootstrap_store
            holder["path"] = entry["path"]

    app.config["BOOTSTRAP_STORE"] = bootstrap_store

    # Per-request hook: bind the currently-active store onto g so the
    # frames/cards blueprints pick it up via their current_store() helper.
    # If neither the live store nor a bootstrap store is configured (e.g.
    # the test suite, or a request arriving before the user picks a
    # board), allocate a single persistent in-memory store on the holder
    # and reuse it across requests. That preserves the previous test
    # behavior where the app is a single shared in-memory board.
    @app.before_request
    def _bind_active_store() -> None:
        holder = app.config["LIVE_STORE_HOLDER"]
        with holder["lock"]:
            store = holder["store"]
            if store is None:
                store = BoardStore(path=None)
                holder["store"] = store
                holder["path"] = None
        g.board_store = store

    # Register blueprints. The frames/cards blueprints accept a
    # "bootstrap" store arg for legacy/test usage; the per-request
    # g-bound store takes precedence when present.
    frames_bp = create_frame_blueprint(bootstrap_store or BoardStore(path=None))
    cards_bp = create_card_blueprint(bootstrap_store or BoardStore(path=None))
    boards_bp = create_boards_blueprint()
    app.register_blueprint(frames_bp, url_prefix="/api/frames")
    app.register_blueprint(cards_bp, url_prefix="/api/cards")
    app.register_blueprint(boards_bp, url_prefix="/api/boards")

    @app.get("/")
    def index() -> Response:
        # Per-request nonce so the inline anti-flash theme script is allowed
        # by the strict CSP (script-src 'self' 'nonce-...').
        nonce = secrets.token_hex(16)
        resp = make_response(render_template("index.html", theme_nonce=nonce))
        return apply_security_headers(resp, nonce)

    @app.after_request
    def _secure(response: Response) -> Response:
        # Static assets (JS/CSS) carry no inline script, so they need no nonce.
        if "Content-Security-Policy" not in response.headers:
            return apply_security_headers(response)
        return response

    return app


app: Flask = create_app(DEFAULT_DATA_PATH)

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
