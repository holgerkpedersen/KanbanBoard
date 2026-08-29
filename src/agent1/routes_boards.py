"""Board-management endpoints mounted at ``/api/boards``.

The app is single-user and single-process. The set of available boards
is determined by the contents of a folder Agent1 maintains (default
``data/boards/``). When the user picks a board, the server copies the
seed file to a working copy (default ``data/board-<catalog_id>.json``)
and loads that into the live ``BoardStore``. Subsequent picks re-use
the same working copy so the user's progress is preserved.

Endpoints
---------
GET  /api/boards        — list catalog + active pointer.
POST /api/boards/select — pick a board (idempotent: copies the seed
                          into a working copy the first time).
POST /api/boards/reset  — re-copy the seed into the working copy,
                          discarding the user's local edits.

Every write requires the ``X-Requested-With: XMLHttpRequest`` CSRF
header and every response carries the standard security headers.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, jsonify, request, Response

from .board_catalog import BoardCatalog
from .security import apply_security_headers, has_csrf_header
from .store import BoardStore, atomic_write_json


def _catalog() -> BoardCatalog:
    cat = current_app.config.get("BOARD_CATALOG")
    assert cat is not None, "BoardCatalog not configured for this app"
    return cat


def _registry_path() -> str:
    return current_app.config["BOARD_REGISTRY"]._path  # noqa: SLF001


def _working_dir() -> str:
    return current_app.config["WORKING_DIR"]


def _uploads_dir() -> str:
    return current_app.config.get("UPLOADS_DIR") or os.path.join(
        _working_dir(), "uploads"
    )


def _working_path(catalog_id: str) -> str:
    return os.path.join(_working_dir(), f"board-{catalog_id}.json")


def _set_live_store(store: BoardStore, path: Optional[str]) -> None:
    """Atomically rebind the live store."""
    holder = current_app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        holder["store"] = store
        holder["path"] = path


def _swap_to_path(path: str) -> BoardStore:
    new_store = BoardStore(path=path)
    _set_live_store(new_store, path)
    return new_store


def _ensure_working_copy(catalog_id: str) -> str:
    """Make sure the working copy for ``catalog_id`` exists and return its path.

    Copies from the catalog seed if (a) the working copy is missing or
    (b) it is older than the seed. Otherwise the existing working copy
    is returned untouched so the user's edits are preserved.
    """
    cat = _catalog()
    seed = cat.path_for(catalog_id)
    if not os.path.exists(seed):
        raise FileNotFoundError(f"catalog seed missing: {seed}")
    working = _working_path(catalog_id)
    os.makedirs(_working_dir(), exist_ok=True)
    if not os.path.exists(working):
        shutil.copyfile(seed, working)
        return working
    # If the seed was updated (Agent1 reset), re-copy. We compare mtimes
    # rather than content hashes so this is cheap on every request.
    try:
        seed_mtime = os.path.getmtime(seed)
        working_mtime = os.path.getmtime(working)
    except OSError:
        return working
    if seed_mtime > working_mtime:
        shutil.copyfile(seed, working)
    return working


def _summary() -> Dict[str, Any]:
    """Build the response body for GET /api/boards."""
    cat = _catalog()
    active_id = current_app.config["BOARD_REGISTRY"].get_active()
    holder = current_app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        current_path = holder["path"]
    return {
        "catalog": cat.list(),
        "active": active_id,
        "current_path": current_path,
        "working_dir": _working_dir(),
        "catalog_dir": cat.folder,
    }


def create_boards_blueprint() -> Blueprint:
    bp = Blueprint("boards", __name__)

    @bp.route("", methods=["GET"])
    def list_boards() -> Response:
        return apply_security_headers(jsonify(_summary()))

    @bp.route("/active", methods=["GET"])
    def get_active_board() -> Response:
        active_id = current_app.config["BOARD_REGISTRY"].get_active()
        if active_id is None:
            return apply_security_headers(jsonify({}))
        cat = _catalog()
        entry = cat.get(active_id)
        if entry is None:
            return apply_security_headers(jsonify({}))
        holder = current_app.config["LIVE_STORE_HOLDER"]
        with holder["lock"]:
            current_path = holder["path"]
        return apply_security_headers(
            jsonify(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "source_path": entry["source_path"],
                    "current_path": current_path,
                }
            )
        )

    @bp.route("/select", methods=["POST"])
    def select_board() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        catalog_id = data.get("catalog_id")
        if not isinstance(catalog_id, str) or not catalog_id:
            return jsonify({"error": "catalog_id required"}), 400
        cat = _catalog()
        entry = cat.get(catalog_id)
        if entry is None:
            return jsonify({"error": f"unknown catalog board: {catalog_id!r}"}), 404
        try:
            working = _ensure_working_copy(catalog_id)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 410
        current_app.config["BOARD_REGISTRY"].set_active(catalog_id)
        _swap_to_path(working)
        return apply_security_headers(
            jsonify(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "source_path": entry["source_path"],
                    "current_path": working,
                }
            )
        )

    @bp.route("/reset", methods=["POST"])
    def reset_board() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        catalog_id = data.get("catalog_id")
        if not isinstance(catalog_id, str) or not catalog_id:
            return jsonify({"error": "catalog_id required"}), 400
        cat = _catalog()
        entry = cat.get(catalog_id)
        if entry is None:
            return jsonify({"error": f"unknown catalog board: {catalog_id!r}"}), 404
        # Force a re-copy by deleting the working copy first.
        working = _working_path(catalog_id)
        if os.path.exists(working):
            try:
                os.remove(working)
            except OSError as exc:
                return jsonify({"error": f"could not remove working copy: {exc}"}), 500
        try:
            working = _ensure_working_copy(catalog_id)
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 410
        current_app.config["BOARD_REGISTRY"].set_active(catalog_id)
        _swap_to_path(working)
        return apply_security_headers(
            jsonify(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "source_path": entry["source_path"],
                    "current_path": working,
                    "reset": True,
                }
            )
        )

    @bp.route("/upload", methods=["POST"])
    def upload_board() -> Response:
        """Ingest a board produced by an external system (e.g. Agent1).

        Accepts either:
          * a multipart upload with a ``file`` field (``.json``), or
          * a JSON request body holding the board object directly
            (``request.get_json()``).

        The board is validated, written atomically into the catalog folder
        (``data/boards/<id>.json``) so it appears in the picker on the next
        ``GET /api/boards``, and is optionally selected as the active board
        when ``activate`` (query param or JSON field) is truthy.

        CSRF + security headers apply, like every other mutating route.
        """
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403

        payload: Optional[Dict[str, Any]] = None
        display_name: Optional[str] = None

        if request.files.get("file") is not None:
            uploaded = request.files["file"]
            raw = uploaded.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return jsonify({"error": "uploaded file is not valid JSON"}), 400
        else:
            payload = request.get_json(silent=True)

        if not isinstance(payload, dict):
            return jsonify({"error": "request must be JSON or a .json file"}), 400

        try:
            _validate_board_payload(payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if isinstance(payload.get("name"), str) and payload["name"].strip():
            display_name = payload["name"].strip()

        catalog_id = secrets.token_hex(6)
        cat = _catalog()
        dest = cat.path_for(catalog_id)
        os.makedirs(cat.folder, exist_ok=True)
        atomic_write_json(dest, payload)

        entry = cat.get(catalog_id)
        if entry is None:  # defensive; should not happen right after write
            return jsonify({"error": "failed to register uploaded board"}), 500
        name = display_name or entry["name"]

        # Optionally make this the active board immediately.
        activate = request.args.get("activate") == "1" or bool(
            isinstance(payload, dict) and payload.get("activate")
        )
        if activate:
            try:
                working = _ensure_working_copy(catalog_id)
            except FileNotFoundError as exc:
                return jsonify({"error": str(exc)}), 410
            current_app.config["BOARD_REGISTRY"].set_active(catalog_id)
            _swap_to_path(working)

        body: Dict[str, Any] = {
            "id": entry["id"],
            "name": name,
            "source_path": entry["source_path"],
            "active": bool(activate),
        }
        if activate:
            body["current_path"] = _working_path(catalog_id)
        resp = jsonify(body)
        resp.status_code = 201
        return apply_security_headers(resp)

    return bp


def _validate_board_payload(payload: Dict[str, Any]) -> None:
    """Light structural validation of an uploaded board object.

    Mirrors the on-disk schema produced by :class:`BoardStore`: an optional
    ``name`` plus ``frames``/``cards`` lists. Raises ``ValueError`` with a
    human-readable message on any structural problem.
    """
    if "frames" not in payload and "cards" not in payload:
        raise ValueError("board must contain 'frames' and/or 'cards'")
    frames = payload.get("frames", [])
    cards = payload.get("cards", [])
    if not isinstance(frames, list):
        raise ValueError("'frames' must be a list")
    if not isinstance(cards, list):
        raise ValueError("'cards' must be a list")
    for i, frame in enumerate(frames):
        if not isinstance(frame, dict) or "id" not in frame:
            raise ValueError(f"frame #{i} must be an object with an 'id'")
    for i, card in enumerate(cards):
        if not isinstance(card, dict) or "id" not in card:
            raise ValueError(f"card #{i} must be an object with an 'id'")
