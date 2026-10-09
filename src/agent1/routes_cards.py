from flask import (
    Blueprint,
    current_app,
    g,
    request,
    jsonify,
    make_response,
    Response,
)
from typing import Any, Dict
import os
import uuid

from .store import BoardStore
from .models import Card
from .security import (
    validate_card_title,
    validate_card_text,
    validate_card_system,
    has_csrf_header,
    apply_security_headers,
)


def _get_kanban_sync():
    """Lazy-load kanban_sync to avoid circular imports."""
    try:
        from . import kanban_sync  # type: ignore[import-not-found]
        return kanban_sync
    except ImportError:
        return None


def _should_sync() -> bool:
    """Return True when card operations should be synced to Agent1.

    Two independent gates must both pass (layered control):

    1. the process-level master switch ``KANBAN_SYNC_ENABLED=1`` (env var), and
    2. the *active board* has opted in to agent1 sync via its on-disk
       ``"sync"`` map — a per-board setting the user toggles in the UI.

    A board without the opt-in never emits card ops, so unlinked boards can't
    pollute Agent1's issue ledger or queue.
    """
    if os.environ.get("KANBAN_SYNC_ENABLED", "0") != "1":
        return False
    if _get_kanban_sync() is None:
        return False
    return current_store().sync_allowed("agent1")


def current_store() -> BoardStore:
    """Return the BoardStore for this request (per-request g-bound, with
    bootstrap fallback). See ``routes_frames.current_store``."""
    store = g.get("board_store")
    if store is not None:
        return store
    fallback = current_app.config.get("BOOTSTRAP_STORE")
    assert fallback is not None, "No BoardStore configured for this request"
    return fallback


def create_card_blueprint(store: BoardStore) -> Blueprint:
    bp = Blueprint("cards", __name__)

    @bp.route("", methods=["POST"])
    def create_card() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        frame_id = data.get("frame_id")
        if not isinstance(frame_id, str):
            return jsonify({"error": "frame_id required"}), 400
        store = current_store()
        if store.get_frame(frame_id) is None:
            return jsonify({"error": "frame not found"}), 404
        try:
            title = validate_card_title(str(data.get("title", "")))
            text = validate_card_text(str(data.get("text", "")))
            system = validate_card_system(str(data.get("system", "")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        raw_tags = data.get("tags", [])
        tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
        card = Card(
            id=uuid.uuid4().hex,
            title=title,
            text=text,
            frame_id=frame_id,
            tags=tags,
            system=system,
        )
        store.add_card(card, frame_id)

        # Enqueue card creation to Agent1 if sync is enabled
        if _should_sync():
            kb = _get_kanban_sync()
            if kb:
                kb.enqueue({
                    "op": "card_create",
                    "source_id": card.id,
                    "payload": card.to_dict(),
                })

        resp = jsonify(card.to_dict())
        resp.status_code = 201
        return apply_security_headers(resp)

    @bp.route("", methods=["GET"])
    def list_cards() -> Response:
        cards = current_store().get_all_cards()
        return apply_security_headers(jsonify([c.to_dict() for c in cards]))

    @bp.route("/<card_id>", methods=["GET"])
    def read_card(card_id: str) -> Response:
        card = current_store().get_card(card_id)
        if card is None:
            return jsonify({"error": "not found"}), 404
        return apply_security_headers(jsonify(card.to_dict()))

    @bp.route("/<card_id>", methods=["PUT"])
    def update_card_endpoint(card_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        store = current_store()
        card = store.get_card(card_id)
        if card is None:
            return jsonify({"error": "not found"}), 404
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        if "title" in data:
            try:
                card.title = validate_card_title(str(data["title"]))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        if "text" in data:
            try:
                card.text = validate_card_text(str(data["text"]))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        if "system" in data:
            try:
                card.system = validate_card_system(str(data["system"]))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        if "tags" in data:
            raw_tags = data["tags"]
            card.tags = (
                [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
            )
        if "frame_id" in data:
            fid = str(data["frame_id"])
            if store.get_frame(fid) is None:
                return jsonify({"error": "frame not found"}), 404
            card.frame_id = fid
        store.update_card(card)

        # Enqueue card update to Agent1 if sync is enabled
        if _should_sync():
            kb = _get_kanban_sync()
            if kb:
                kb.enqueue({
                    "op": "card_update",
                    "source_id": card.id,
                    "payload": card.to_dict(),
                })

        return apply_security_headers(jsonify(card.to_dict()))

    @bp.route("/<card_id>", methods=["DELETE"])
    def delete_card_endpoint(card_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        if current_store().get_card(card_id) is None:
            return jsonify({"error": "not found"}), 404
        store = current_store()
        card = store.get_card(card_id)

        store.delete_card(card_id)

        # Enqueue card deletion to Agent1 if sync is enabled
        if _should_sync():
            kb = _get_kanban_sync()
            if kb:
                kb.enqueue({
                    "op": "card_delete",
                    "source_id": card.id,
                    "payload": {"frame_id": card.frame_id},
                })

        resp = make_response("", 204)
        return apply_security_headers(resp)

    @bp.route("/<card_id>/move", methods=["POST"])
    def move_card(card_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        store = current_store()
        card = store.get_card(card_id)
        if card is None:
            return jsonify({"error": "not found"}), 404
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        fid = data.get("frame_id")
        if not isinstance(fid, str) or store.get_frame(fid) is None:
            return jsonify({"error": "invalid frame"}), 400
        index = data.get("index")
        index = index if isinstance(index, int) else None
        if not store.move_card(card_id, fid, index):
            return jsonify({"error": "move failed"}), 400

        # Enqueue card move to Agent1 if sync is enabled.
        # The frame *title* travels with the opaque frame id: Agent1 maps a
        # column to an issue status by name ("Finished" -> resolved), and a
        # uuid can never be looked up in that table.  Sending only the id made
        # every move fall back to "open".
        if _should_sync():
            kb = _get_kanban_sync()
            if kb:
                moved_frame = store.get_frame(fid)
                kb.enqueue({
                    "op": "card_move",
                    "source_id": card.id,
                    "payload": {
                        "frame_id": fid,
                        "frame_title": moved_frame.title if moved_frame else "",
                    },
                })

        return apply_security_headers(jsonify(card.to_dict()))

    return bp
