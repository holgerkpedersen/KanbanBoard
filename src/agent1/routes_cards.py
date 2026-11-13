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
import uuid

from .store import BoardStore
from .models import Card, now_iso, VALID_STATUSES
from .security import (
    validate_card_title,
    validate_card_text,
    validate_card_system,
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

    1. the process-level master switch ``KANBAN_SYNC_ENABLED``, resolved by
       ``kanban_sync.sync_enabled()`` — process environment first, then the
       repo ``.env`` file; and
    2. the *active board* has opted in to agent1 sync via its on-disk
       ``"sync"`` map — a per-board setting the user toggles in the UI.

    A board without the opt-in never emits card ops, so unlinked boards can't
    pollute Agent1's issue ledger or queue.

    Gate 1 MUST go through ``sync_enabled()`` and not read ``os.environ``
    directly. The app startup (``app.create_app``) and the inbound
    ``QueueProcessor`` both resolve the switch through that resolver, so an
    operator who flips it in ``.env`` gets a running sync pipeline. Reading
    only ``os.environ`` here made the Work button answer 409 "agent1 sync is
    not enabled for this board" while the checkbox was ticked — the message
    blamed gate 2, which was correctly set, and the card never reached Agent1.
    """
    kb = _get_kanban_sync()
    if kb is None:
        return False
    if not kb.sync_enabled():
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
            raw_status = data.get("status", "open")
            if not isinstance(raw_status, str) or raw_status not in VALID_STATUSES:
                return jsonify({"error": f"invalid status: {raw_status!r}"}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        raw_tags = data.get("tags", [])
        tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
        parent_id = data.get("parent_id", "")
        if not isinstance(parent_id, str):
            parent_id = ""
        created = now_iso()
        card = Card(
            id=uuid.uuid4().hex,
            title=title,
            text=text,
            frame_id=frame_id,
            parent_id=parent_id,
            tags=tags,
            system=system,
            created_at=created,
            updated_at=created,
            status=raw_status,
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

    @bp.route("/linked", methods=["GET"])
    def linked_cards() -> Response:
        """Card ids that are integrated with an Agent1 issue.

        Read-only view of the shared ``.card_id_map.json`` — no writes, no
        schema changes, so it cannot affect the sync pipeline.  When the sync
        module is unavailable the answer is "nothing is linked".
        """
        kb = _get_kanban_sync()
        ids: list[str] = []
        if kb is not None:
            try:
                ids = sorted(kb.linked_card_ids())
            except Exception:  # noqa: BLE001 — endpoint must never 500 on a stale map
                ids = []
        return apply_security_headers(jsonify({"card_ids": ids}))

    @bp.route("/<card_id>", methods=["GET"])
    def read_card(card_id: str) -> Response:
        card = current_store().get_card(card_id)
        if card is None:
            return jsonify({"error": "not found"}), 404
        return apply_security_headers(jsonify(card.to_dict()))

    @bp.route("/<card_id>", methods=["PUT"])
    def update_card_endpoint(card_id: str) -> Response:
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
        if "status" in data:
            new_status = str(data["status"])
            if new_status not in VALID_STATUSES:
                return jsonify({"error": f"invalid status: {new_status!r}"}), 400
            card.status = new_status
        if "frame_id" in data:
            fid = str(data["frame_id"])
            if store.get_frame(fid) is None:
                return jsonify({"error": "frame not found"}), 404
            card.frame_id = fid
        if "parent_id" in data:
            pid = str(data["parent_id"])
            # Validate that the parent exists (empty string means no parent).
            if pid and store.get_card(pid) is None:
                return jsonify({"error": f"parent not found: {pid}"}), 404
            card.parent_id = pid
        # Any accepted update refreshes the card's updated_at timestamp.
        card.updated_at = now_iso()
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

    @bp.route("/<card_id>/work", methods=["POST"])
    def work_card(card_id: str) -> Response:
        """Hand a card to Agent1 for execution (the board's Work button).

        Only queues a ``card_work`` request; Agent1 decides whether and how to
        act on it. Fails closed: without the sync opt-in nothing is queued.
        """
        store = current_store()
        card = store.get_card(card_id)
        if card is None:
            return jsonify({"error": "not found"}), 404
        if not _should_sync():
            return apply_security_headers(
                jsonify({"error": "agent1 sync is not enabled for this board"})
            ), 409
        kb = _get_kanban_sync()
        if kb is None:
            return apply_security_headers(
                jsonify({"error": "agent1 sync module unavailable"})
            ), 503
        frame = store.get_frame(card.frame_id)
        kb.enqueue({
            "op": "card_work",
            "source_id": card.id,
            "payload": {
                "card_id": card.id,
                "title": card.title,
                "text": card.text,
                "status": card.status,
                "frame_title": frame.title if frame is not None else "",
            },
        })
        resp = jsonify({"queued": True, "card_id": card.id})
        resp.status_code = 202
        return apply_security_headers(resp)

    @bp.route("/<card_id>", methods=["DELETE"])
    def delete_card_endpoint(card_id: str) -> Response:
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
        # A move counts as a change to the card: refresh updated_at. The
        # store's move_card looks up this same card object and persists it.
        card.updated_at = now_iso()
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
                        "status": card.status,
                    },
                })

        return apply_security_headers(jsonify(card.to_dict()))

    return bp
