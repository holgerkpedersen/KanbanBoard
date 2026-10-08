"""Application factory for the Kanban board service.

The app is single-process and single-user. The set of available boards
is determined by the contents of a catalog folder (default
``data/boards/``) that Agent1 maintains. When the user picks a board,
the server copies the seed to a working copy in the working folder
(default ``data/``) and loads that into memory.

The board-management endpoints live in ``routes_boards.py``; this
module wires them up to a :class:`BoardRegistry` (active-id pointer), a
:class:`BoardCatalog` (read-only view of the seed folder), and a
per-app "live store" holder so the existing frame/card blueprints keep
working unchanged.
"""

from __future__ import annotations

import atexit
import logging
import os
import secrets
import shutil
import sys
import tempfile
import threading
from typing import Optional

from flask import Flask, Response, request, render_template, make_response, g

from .store import BoardStore
from .board_registry import BoardRegistry
from .board_catalog import BoardCatalog
from .routes_frames import create_frame_blueprint
from .routes_cards import create_card_blueprint
from .routes_boards import create_boards_blueprint
from .security import apply_security_headers

# Default on-disk locations for the *production* app. The module-level
# ``app`` below passes these explicitly. Tests call create_app() with no
# path, which keeps them in-memory and deterministic.
DEFAULT_WORKING_DIR = os.environ.get("KANBAN_WORKING_DIR", "data")
DEFAULT_CATALOG_DIR = os.environ.get(
    "KANBAN_CATALOG_DIR", os.path.join(DEFAULT_WORKING_DIR, "boards")
)
DEFAULT_REGISTRY_PATH = os.environ.get(
    "KANBAN_REGISTRY_PATH", os.path.join(DEFAULT_WORKING_DIR, "active.json")
)
DEFAULT_UPLOADS_DIR = os.environ.get(
    "KANBAN_UPLOADS_DIR", os.path.join(DEFAULT_WORKING_DIR, "uploads")
)
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # cap for any future upload path

logger = logging.getLogger(__name__)

# The sync processor this process started, if any. create_app() reuses this
# handle for every later call, so exactly one daemon poller runs per process;
# module-private — no request path consumes it (see the sync block in
# create_app), and a leaked reference is only an exit-time shutdown hook.
_SYNC_HANDLES: list = []


def _make_live_holder() -> dict:
    return {"store": None, "path": None, "lock": threading.RLock()}


def create_app(
    working_dir: str | None = None,
    catalog_dir: str | None = None,
    registry_path: str | None = None,
    uploads_dir: str | None = None,
) -> Flask:
    """Build a Flask app.

    Parameters
    ----------
    working_dir:
        Folder where the per-board working copies live
        (e.g. ``data/board-<id>.json``). The current board is here.
        ``None`` (the test-suite default) keeps the app in-memory.
    catalog_dir:
        Folder of seed ``.json`` files Agent1 maintains
        (e.g. ``data/boards/``). Read-only from the app's perspective.
    registry_path:
        On-disk path of the active-id pointer file.
    uploads_dir:
        Folder where uploaded board JSON is staged before being promoted
        into the catalog (``data/boards/``). The upload endpoint writes the
        canonical catalog copy directly, so this is mainly a documented
        landing area; it defaults to ``<working_dir>/uploads``.
    """
    app = Flask(__name__)
    app.config["DEBUG"] = False
    app.config["TESTING"] = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    # ``working_dir is None`` means "no on-disk home": the app stays
    # in-memory and hermetic, exactly as the docstring above promises.
    # The board routes still need *some* folder to point at, so give them
    # a throwaway temp dir that no other run — and, crucially, not the
    # real ``data/`` board — can see. Without this, a bare
    # ``create_app()`` in a test would hydrate the user's live board from
    # ``data/`` and write test frames/cards into it.
    if working_dir is None:
        working_dir = tempfile.mkdtemp(prefix="kanban-ephemeral-")
        atexit.register(shutil.rmtree, working_dir, True)
    if catalog_dir is None:
        catalog_dir = os.path.join(working_dir, "boards")
    if registry_path is None:
        registry_path = os.path.join(working_dir, "active.json")
    if uploads_dir is None:
        uploads_dir = os.path.join(working_dir, "uploads")

    os.makedirs(working_dir, exist_ok=True)
    os.makedirs(catalog_dir, exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)

    catalog = BoardCatalog(folder=catalog_dir)
    registry = BoardRegistry(path=registry_path)
    app.config["BOARD_CATALOG"] = catalog
    app.config["BOARD_REGISTRY"] = registry
    app.config["WORKING_DIR"] = working_dir
    app.config["UPLOADS_DIR"] = uploads_dir
    holder = _make_live_holder()

    # On boot, if the active-id pointer is set and the matching working
    # copy exists, hydrate the in-memory store from disk so a restart
    # preserves the user's progress. If there's no active id yet, leave
    # the holder empty; the per-request hook will allocate a single
    # shared in-memory store (which keeps the test suite happy).
    active_id = registry.get_active()
    if active_id:
        working_path = os.path.join(working_dir, f"board-{active_id}.json")
        if os.path.isfile(working_path):
            holder["store"] = BoardStore(path=working_path)
            holder["path"] = working_path

    app.config["LIVE_STORE_HOLDER"] = holder

    # Per-request hook: bind the currently-active store onto g so the
    # frames/cards blueprints pick it up via their current_store() helper.
    # If no board is active yet (no /api/boards/select call this run),
    # allocate a single persistent in-memory store on the holder and
    # reuse it across requests. That keeps the test suite, which never
    # selects a board, working with a single in-memory board.
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
    bootstrap_store: Optional[BoardStore] = None
    frames_bp = create_frame_blueprint(bootstrap_store or BoardStore(path=None))
    cards_bp = create_card_blueprint(bootstrap_store or BoardStore(path=None))
    boards_bp = create_boards_blueprint()
    app.register_blueprint(frames_bp, url_prefix="/api/frames")
    app.register_blueprint(cards_bp, url_prefix="/api/cards")
    app.register_blueprint(boards_bp, url_prefix="/api/boards")

    # Start one Kanban sync background thread per process, if enabled. The
    # processor reads the live store from app.config at run_once() time, and
    # importing this module already ran create_app() once for the production
    # app — so every later call (extra apps in tests, helper scripts) must
    # reuse that first processor instead of spawning another daemon poller on
    # the same queue dirs. Each extra poller widens the window where a kill
    # lands messages.jsonl mid-append: the reader then silently drops the torn
    # line while advancing past it (see kanban_sync.read_queue). The handle is
    # module-private — no request path consumes it, so callers cannot reach
    # the live app through it; an extra create_app() only re-points which app a
    # processor reads its store from.
    if os.environ.get("KANBAN_SYNC_ENABLED", "0") == "1":
        _sync_handles = getattr(sys.modules[__name__], "_SYNC_HANDLES", None)
        # The module sentinel starts as [] ("no processor yet"), so an
        # `is None` test never fires: it must be falsy, not None, for the
        # first sync-enabled app to actually construct the processor.
        if not _sync_handles:
            # First sync-enabled app in this process becomes the processor's
            # app — the one and only processor, registered as a shutdown hook.
            try:
                from . import kanban_sync as kb_sync
                proc = kb_sync.QueueProcessor(app, poll_interval=5.0)
                # The processor runs on a daemon thread; at interpreter exit
                # daemon threads are killed abruptly and can be torn off
                # mid-append — an fsync'd tail write is no defence against a
                # kill -9-style exit-time teardown. Stop the poller promptly on
                # normal shutdown so it exits its 100 ms tick loop between
                # batches; a blocked processor never delays exit beyond one
                # poll interval (stop() only sets the flag, runs in parallel).
                atexit.register(proc.stop)
                # Run the poller on its own daemon thread. Without the start()
                # the processor is constructed but never polls, so an enabled
                # sync silently drops every queued message.
                threading.Thread(target=proc.run_forever, daemon=True).start()
                _sync_handles = [proc]
                sys.modules[__name__]._SYNC_HANDLES = _sync_handles
            except Exception as exc:  # noqa: BLE001 — don't crash the app on sync failure
                logger.error("Failed to start Kanban sync thread: %s", exc)
            else:
                logger.info("Kanban sync thread started")
        elif _sync_handles:
            # Reuse the process's first processor: it looks the live store up
            # from app.config at run_once() time, so it serves every app in
            # this process — a second poller on the same queue dirs adds
            # nothing but mid-batch kill windows.
            _sync_handles[0].app = app
            logger.info("Reusing the process's sync processor for this app")
        else:
            logger.warning("Kanban sync enabled, but no processor is available to start")

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


# Production entry point: opt into the real on-disk locations explicitly,
# since a bare create_app() is now hermetic (in-memory). Defined after the
# factory so `_SYNC_HANDLES` already exists when the production app calls it.
app: Flask = create_app(
    working_dir=DEFAULT_WORKING_DIR,
    catalog_dir=DEFAULT_CATALOG_DIR,
    registry_path=DEFAULT_REGISTRY_PATH,
    uploads_dir=DEFAULT_UPLOADS_DIR,
)


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
