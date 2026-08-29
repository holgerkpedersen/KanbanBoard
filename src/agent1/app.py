import os
import secrets

from flask import Flask, Response, request, render_template, make_response

from .store import BoardStore
from .routes_frames import create_frame_blueprint
from .routes_cards import create_card_blueprint
from .security import apply_security_headers

# Default on-disk location for the running app. Tests call create_app()
# with no path, so they stay purely in-memory and deterministic.
DEFAULT_DATA_PATH = os.environ.get("KANBAN_DATA_PATH", "data/board.json")


def create_app(data_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DEBUG"] = False
    app.config["TESTING"] = False

    store = BoardStore(path=data_path)
    frames_bp = create_frame_blueprint(store)
    cards_bp = create_card_blueprint(store)
    app.register_blueprint(frames_bp, url_prefix="/api/frames")
    app.register_blueprint(cards_bp, url_prefix="/api/cards")

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
