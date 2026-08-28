import os

from flask import Flask, Response, request, render_template

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
        return render_template("index.html")

    @app.after_request
    def _secure(response: Response) -> Response:
        return apply_security_headers(response)

    return app


app: Flask = create_app(DEFAULT_DATA_PATH)

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
