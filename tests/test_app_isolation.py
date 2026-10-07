"""Regression tests: a bare ``create_app()`` must stay hermetic.

``create_app()`` is documented to keep the app in-memory when no
``working_dir`` is passed. A regression (commit 4471fc6) made it resolve
``None`` to the real ``data/`` folder instead, so every bare
``create_app()`` in the test suite hydrated the user's live board and
wrote test frames/cards into it. These tests pin the contract down.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app
from src.agent1.app import DEFAULT_WORKING_DIR

CSRF = {"X-Requested-With": "XMLHttpRequest"}

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA_DIR = REPO_ROOT / "data"


def _snapshot(root: Path) -> dict:
    """Map every file under ``root`` to (mtime_ns, size)."""
    if not root.exists():
        return {}
    snap = {}
    for path in root.rglob("*"):
        if path.is_file():
            st = path.stat()
            snap[str(path.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return snap


def _bare_client():
    app = create_app()
    app.config["TESTING"] = True
    return app, app.test_client()


def test_bare_create_app_does_not_point_at_real_data_dir():
    app, _ = _bare_client()
    assert os.path.abspath(app.config["WORKING_DIR"]) != os.path.abspath(
        DEFAULT_WORKING_DIR
    )


def test_bare_create_app_starts_with_no_live_board():
    app, c = _bare_client()
    # No board is active in the ephemeral dir, so nothing was hydrated.
    assert app.config["LIVE_STORE_HOLDER"]["store"] is None
    assert c.get("/api/frames").get_json() == []


def test_bare_create_app_mutations_do_not_touch_real_data_dir():
    before = _snapshot(REAL_DATA_DIR)
    app, c = _bare_client()
    fid = c.post("/api/frames", json={"title": "Ephemeral"}, headers=CSRF).get_json()[
        "id"
    ]
    c.post(
        "/api/cards",
        json={"title": "Ephemeral card", "frame_id": fid},
        headers=CSRF,
    )
    assert _snapshot(REAL_DATA_DIR) == before


def test_production_entry_point_still_uses_real_data_dir():
    from src.agent1 import app as prod_app

    assert os.path.abspath(prod_app.config["WORKING_DIR"]) == os.path.abspath(
        DEFAULT_WORKING_DIR
    )
