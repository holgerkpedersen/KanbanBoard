"""Board-management endpoints mounted at ``/api/boards``.

Every write route requires the ``X-Requested-With: XMLHttpRequest``
CSRF header (same as the rest of the API) and every response carries
the same security headers via :func:`apply_security_headers`.

The endpoints operate on two pieces of state:

* the :class:`BoardRegistry` (persistent list of known boards + which
  one is active), and
* the **live store** — the per-request ``BoardStore`` instance bound to
  the active board's file. The store is rebound by mutating a per-app
  holder (``app.config["LIVE_STORE"]``) under a lock; the per-request
  ``before_request`` hook reads it onto ``g.board_store``.

This keeps the existing frame/card blueprints unchanged: they call
``current_store()`` and get whichever store is active for that request.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import threading
from typing import Any, Dict, Optional, Tuple

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    make_response,
    request,
    Response,
)

from .board_registry import BoardRegistry
from .security import (
    apply_security_headers,
    has_csrf_header,
    validate_board_name,
    validate_board_path,
)
from .store import BoardStore, atomic_write_json


MAX_OPEN_FILE_BYTES = 5 * 1024 * 1024  # browser upload cap (also Flask MAX_CONTENT_LENGTH)


def _registry() -> BoardRegistry:
    reg = current_app.config.get("BOARD_REGISTRY")
    assert reg is not None, "BoardRegistry not configured for this app"
    return reg


def _set_live_store(store: BoardStore, path: Optional[str]) -> None:
    """Rebind the live store to ``path`` (or in-memory if ``None``).

    Holds the live-store lock for the duration of the swap so a
    concurrent request can't observe a half-swapped state.
    """
    holder = current_app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        holder["store"] = store
        holder["path"] = path


def _get_live() -> Tuple[Optional[BoardStore], Optional[str]]:
    holder = current_app.config["LIVE_STORE_HOLDER"]
    with holder["lock"]:
        return holder["store"], holder["path"]


def _swap_to_path(path: str) -> BoardStore:
    """Atomically rebind the live store to ``path`` and return it."""
    new_store = BoardStore(path=path)
    _set_live_store(new_store, path)
    return new_store


def _empty_board_data() -> Dict[str, Any]:
    return {"frames": [], "cards": []}


def _ensure_path_is_valid_board(path: str) -> None:
    """If ``path`` exists, validate that its JSON parses into a board
    shape (``frames`` + ``cards`` keys). Otherwise raise ``ValueError``.
    """
    if not os.path.exists(path):
        return  # will be created on first write
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read(MAX_OPEN_FILE_BYTES + 1)
    if len(raw) > MAX_OPEN_FILE_BYTES:
        raise ValueError("board file exceeds maximum size")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not {"frames", "cards"} <= set(data.keys()):
        raise ValueError("file is not a Kanban board (missing 'frames' or 'cards')")


def _copy_board_file(src: str, dst: str) -> None:
    """Copy ``src`` to ``dst`` byte-for-byte. Caller is responsible for
    validating the destination path."""
    shutil.copyfile(src, dst)


def create_boards_blueprint() -> Blueprint:
    bp = Blueprint("boards", __name__)

    # ---- list / active -----------------------------------------------------
    @bp.route("", methods=["GET"])
    def list_boards() -> Response:
        reg = _registry()
        active = reg.get_active()
        return apply_security_headers(
            jsonify(
                {
                    "boards": reg.list(),
                    "active": active["id"] if active else None,
                }
            )
        )

    @bp.route("/active", methods=["GET"])
    def get_active_board() -> Response:
        active = _registry().get_active()
        if active is None:
            return apply_security_headers(jsonify({}))
        return apply_security_headers(jsonify(active))

    # ---- create ------------------------------------------------------------
    @bp.route("/create", methods=["POST"])
    def create_board() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        try:
            name = validate_board_name(str(data.get("name", "")))
            folder = str(data.get("folder", ""))
            filename = str(data.get("filename", ""))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not folder or not filename:
            return jsonify({"error": "folder and filename are required"}), 400
        # Build a candidate path and validate it via the same path validator
        # the open-file endpoint uses, so all path rules stay in one place.
        candidate = os.path.join(folder, filename)
        try:
            resolved = validate_board_path(candidate)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if os.path.exists(resolved):
            return jsonify({"error": "file already exists at that path"}), 409
        # Create an empty board file atomically.
        atomic_write_json(resolved, _empty_board_data())
        reg = _registry()
        entry = reg.add(name, resolved)
        reg.set_active(entry["id"])
        _swap_to_path(resolved)
        return apply_security_headers(jsonify(entry)), 201

    # ---- open existing file ------------------------------------------------
    @bp.route("/open-file", methods=["POST"])
    def open_file() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        # Two modes: explicit path, or uploaded content.
        path = data.get("path")
        content = data.get("content")
        name = data.get("name") or ""
        reg = _registry()

        if isinstance(path, str) and path:
            try:
                resolved = validate_board_path(path, require_exists=True)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            try:
                _ensure_path_is_valid_board(resolved)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            display_name = name.strip() or os.path.splitext(os.path.basename(resolved))[0]
            try:
                display_name = validate_board_name(display_name)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            entry = reg.add(display_name, resolved)
            reg.set_active(entry["id"])
            _swap_to_path(resolved)
            return apply_security_headers(jsonify(entry)), 201

        if isinstance(content, str) and content:
            # Browser File-System-Access-API branch: the user picked a file
            # in the browser, we received the raw JSON text, and we save
            # it server-side into a managed uploads directory so atomic
            # write semantics still apply.
            if len(content) > MAX_OPEN_FILE_BYTES:
                return jsonify({"error": "file too large"}), 400
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                return jsonify({"error": f"invalid JSON: {exc}"}), 400
            if not isinstance(parsed, dict) or not {"frames", "cards"} <= set(parsed.keys()):
                return jsonify({"error": "not a Kanban board (missing 'frames' or 'cards')"}), 400
            display_name = (name or "Imported").strip() or "Imported"
            try:
                display_name = validate_board_name(display_name)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            upload_dir = current_app.config.get(
                "UPLOADS_DIR", os.path.join(os.path.dirname(reg._path) or "data", "uploads")
            )
            os.makedirs(upload_dir, exist_ok=True)
            # Random filename so the user's choice doesn't collide with
            # an existing file.
            target = os.path.join(upload_dir, f"upload-{secrets.token_hex(6)}.json")
            atomic_write_json(target, parsed)
            entry = reg.add(display_name, target)
            reg.set_active(entry["id"])
            _swap_to_path(target)
            return apply_security_headers(jsonify(entry)), 201

        return jsonify({"error": "path or content required"}), 400

    # ---- select ------------------------------------------------------------
    @bp.route("/select", methods=["POST"])
    def select_board() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        bid = data.get("id")
        if not isinstance(bid, str):
            return jsonify({"error": "id required"}), 400
        reg = _registry()
        entry = reg.get(bid)
        if entry is None:
            return jsonify({"error": "not found"}), 404
        # Only swap if the file actually exists. (The user can still
        # open it from /open-file once they've chosen a path.)
        if not os.path.exists(entry["path"]):
            return jsonify({"error": "board file is missing on disk"}), 410
        reg.set_active(bid)
        _swap_to_path(entry["path"])
        return apply_security_headers(jsonify(entry))

    # ---- rename ------------------------------------------------------------
    @bp.route("/<board_id>", methods=["PUT"])
    def rename_board(board_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        reg = _registry()
        if reg.get(board_id) is None:
            return jsonify({"error": "not found"}), 404
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        try:
            name = validate_board_name(str(data.get("name", "")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return apply_security_headers(jsonify(reg.rename(board_id, name)))

    # ---- duplicate ---------------------------------------------------------
    @bp.route("/<board_id>/duplicate", methods=["POST"])
    def duplicate_board(board_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        reg = _registry()
        entry = reg.get(board_id)
        if entry is None:
            return jsonify({"error": "not found"}), 404
        if not os.path.exists(entry["path"]):
            return jsonify({"error": "source board file is missing"}), 410
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        dest = data.get("dest_path")
        if not isinstance(dest, str) or not dest:
            return jsonify({"error": "dest_path required"}), 400
        try:
            resolved = validate_board_path(dest)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if os.path.exists(resolved):
            return jsonify({"error": "destination already exists"}), 409
        _copy_board_file(entry["path"], resolved)
        new_name = str(data.get("new_name", "")).strip()
        if new_name:
            try:
                new_name = validate_board_name(new_name)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        else:
            new_name = entry["name"] + " (copy)"
            try:
                new_name = validate_board_name(new_name)
            except ValueError:
                new_name = "Copy"
        new_entry = reg.add(new_name, resolved)
        return apply_security_headers(jsonify(new_entry)), 201

    # ---- delete ------------------------------------------------------------
    @bp.route("/<board_id>", methods=["DELETE"])
    def delete_board(board_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        reg = _registry()
        entry = reg.get(board_id)
        if entry is None:
            return jsonify({"error": "not found"}), 404
        delete_file = request.args.get("delete_file", "false").lower() == "true"
        if delete_file:
            # Safety: only delete files that are still registered
            # (defense against crafted ids that somehow escape validation).
            current_path = entry["path"]
            if os.path.exists(current_path) and os.path.isfile(current_path):
                try:
                    os.remove(current_path)
                except OSError as exc:
                    return jsonify({"error": f"could not delete file: {exc}"}), 500
        reg.remove(board_id)
        # If we just removed the active board, fall back to whatever is
        # now first in the list, or in-memory if nothing is left.
        active = reg.get_active()
        if active is None:
            remaining = reg.list()
            if remaining:
                next_entry = remaining[0]
                reg.set_active(next_entry["id"])
                if os.path.exists(next_entry["path"]):
                    _swap_to_path(next_entry["path"])
                else:
                    _set_live_store(BoardStore(path=None), None)
            else:
                _set_live_store(BoardStore(path=None), None)
        return apply_security_headers(make_response("", 204))

    return bp
