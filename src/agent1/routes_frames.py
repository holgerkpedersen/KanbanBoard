from flask import Blueprint, request, jsonify, make_response, Response
from typing import Any, Dict
import uuid

from .store import BoardStore
from .models import Frame
from .security import (
    validate_frame_title,
    has_csrf_header,
    apply_security_headers,
)


def create_frame_blueprint(store: BoardStore) -> Blueprint:
    bp = Blueprint("frames", __name__)

    @bp.route("", methods=["POST"])
    def create_frame() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        try:
            title = validate_frame_title(str(data.get("title", "")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        frame = Frame(id=uuid.uuid4().hex, title=title, card_ids=[])
        store.add_frame(frame)
        resp = jsonify(frame.to_dict())
        resp.status_code = 201
        return apply_security_headers(resp)

    @bp.route("", methods=["GET"])
    def list_frames() -> Response:
        frames = store.get_all_frames()
        return apply_security_headers(
            jsonify([f.to_dict() for f in frames])
        )

    @bp.route("/board", methods=["GET"])
    def get_board() -> Response:
        frames = store.get_all_frames()
        board = []
        for f in frames:
            board.append(
                {
                    "id": f.id,
                    "title": f.title,
                    "cards": [c.to_dict() for c in store.get_cards_in_frame(f.id)],
                }
            )
        return apply_security_headers(jsonify(board))

    @bp.route("/<frame_id>", methods=["GET"])
    def read_frame(frame_id: str) -> Response:
        frame = store.get_frame(frame_id)
        if frame is None:
            return jsonify({"error": "not found"}), 404
        return apply_security_headers(jsonify(frame.to_dict()))

    @bp.route("/<frame_id>", methods=["PUT"])
    def update_frame(frame_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        frame = store.get_frame(frame_id)
        if frame is None:
            return jsonify({"error": "not found"}), 404
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        if "title" in data:
            try:
                frame.title = validate_frame_title(str(data["title"]))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        store.update_frame(frame)
        return apply_security_headers(jsonify(frame.to_dict()))

    @bp.route("/<frame_id>", methods=["DELETE"])
    def delete_frame(frame_id: str) -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        if store.get_frame(frame_id) is None:
            return jsonify({"error": "not found"}), 404
        store.delete_frame(frame_id)
        resp = make_response("", 204)
        return apply_security_headers(resp)

    @bp.route("/reorder", methods=["POST"])
    def reorder_frames() -> Response:
        if not has_csrf_header(request):
            return jsonify({"error": "csrf"}), 403
        data: Dict[str, Any] = request.get_json(silent=True) or {}
        order = data.get("order")
        if not isinstance(order, list) or not all(
            isinstance(fid, str) for fid in order
        ):
            return jsonify({"error": "order required"}), 400
        store.reorder_frames(order)
        return apply_security_headers(jsonify({"ok": True}))

    return bp
