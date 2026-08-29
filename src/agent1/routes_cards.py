from flask import Blueprint, request, jsonify, make_response, Response
from typing import Any, Dict
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
        resp = jsonify(card.to_dict())
        resp.status_code = 201
        return apply_security_headers(resp)

    @bp.route("", methods=["GET"])
    def list_cards() -> Response:
        cards = store.get_all_cards()
        return apply_security_headers(jsonify([c.to_dict() for c in cards]))

    @bp.route("/<card_id>", methods=["GET"])
    def read_card(card_id: str) -> Response:
        card = store.get_card(card_id)
        if card is None:
            return jsonify({"error": "not found"}), 404
        return apply_security_headers(jsonify(card.to_dict()))

    @bp.route("/<card_id>", methods=["PUT"])
    def update_card_endpoint(card_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
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
        return apply_security_headers(jsonify(card.to_dict()))

    @bp.route("/<card_id>", methods=["DELETE"])
    def delete_card_endpoint(card_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        if store.get_card(card_id) is None:
            return jsonify({"error": "not found"}), 404
        store.delete_card(card_id)
        resp = make_response("", 204)
        return apply_security_headers(resp)

    @bp.route("/<card_id>/move", methods=["POST"])
    def move_card(card_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
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
        return apply_security_headers(jsonify(card.to_dict()))

    return bp