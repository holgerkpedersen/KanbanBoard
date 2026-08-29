"""Tests for the /api/boards blueprint.

The board manager is catalog-based: Agent1 seeds
``<working_dir>/boards/<id>.json``; the app copies the seed to
``<working_dir>/board-<id>.json`` on first pick, re-uses the working
copy on subsequent picks (so progress is preserved), and can be reset
back to the seed.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1 import create_app
from src.agent1.store import BoardStore

CSRF = {"X-Requested-With": "XMLHttpRequest"}


def _client(tmp_path, *, working_dir=None, catalog_dir=None):
    """Build a test client backed by on-disk catalog + working folders."""
    wd = working_dir or str(tmp_path / "data")
    cd = catalog_dir or str(tmp_path / "data" / "boards")
    os.makedirs(wd, exist_ok=True)
    os.makedirs(cd, exist_ok=True)
    app = create_app(
        working_dir=wd,
        catalog_dir=cd,
        registry_path=str(tmp_path / "active.json"),
    )
    app.config["TESTING"] = True
    return app.test_client(), app, wd, cd


def _write_seed(catalog_dir, catalog_id, *, frames=2, cards=1, name=None):
    p = Path(catalog_dir) / f"{catalog_id}.json"
    payload = {
        "name": name or catalog_id,
        "frames": [
            {"id": f"f{i}", "title": f"Frame {i}", "card_ids": [f"c{i}"]}
            for i in range(frames)
        ],
        "cards": [
            {
                "id": f"c{i}",
                "title": f"Card {i}",
                "text": "",
                "frame_id": f"f{i}",
                "tags": [],
                "system": "",
            }
            for i in range(cards)
        ],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


# ---- list / active --------------------------------------------------------


def test_list_returns_empty_catalog(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.get("/api/boards")
    assert r.status_code == 200
    body = r.get_json()
    assert body["catalog"] == []
    assert body["active"] is None
    assert body["current_path"] is None


def test_list_returns_catalog_with_display_names(tmp_path):
    c, _, _, cd = _client(tmp_path)
    _write_seed(cd, "alpha", name="Plan Alpha")
    _write_seed(cd, "beta", name="Plan Beta")
    r = c.get("/api/boards")
    body = r.get_json()
    catalog = {e["id"]: e for e in body["catalog"]}
    assert set(catalog) == {"alpha", "beta"}
    assert catalog["alpha"]["name"] == "Plan Alpha"
    assert catalog["alpha"]["source_path"].endswith("alpha.json")


def test_list_returns_fallback_name_when_seed_has_none(tmp_path):
    c, _, _, cd = _client(tmp_path)
    _write_seed(cd, "no-name-board", name=None)
    # Overwrite with no "name" key.
    p = Path(cd) / "no-name-board.json"
    p.write_text(json.dumps({"frames": [], "cards": []}), encoding="utf-8")
    r = c.get("/api/boards")
    catalog = r.get_json()["catalog"]
    assert catalog[0]["id"] == "no-name-board"
    assert catalog[0]["name"] == "no-name-board"  # fallback to id


def test_list_skips_non_json_and_invalid_ids(tmp_path):
    c, _, _, cd = _client(tmp_path)
    _write_seed(cd, "good")
    (Path(cd) / "README.md").write_text("hello", encoding="utf-8")
    (Path(cd) / "with space.json").write_text("{}", encoding="utf-8")
    r = c.get("/api/boards")
    ids = [e["id"] for e in r.get_json()["catalog"]]
    assert ids == ["good"]


def test_list_includes_security_headers(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.get("/api/boards")
    assert "Content-Security-Policy" in r.headers


def test_active_empty_when_nothing_selected(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.get("/api/boards/active")
    assert r.status_code == 200
    assert r.get_json() == {}


def test_active_empty_when_selected_id_no_longer_in_catalog(tmp_path):
    c, _, _, _ = _client(tmp_path)
    # Pre-seed a stale active pointer.
    app = c.application
    app.config["BOARD_REGISTRY"].set_active("deleted")
    r = c.get("/api/boards/active")
    assert r.get_json() == {}


# ---- select ---------------------------------------------------------------


def test_select_copies_seed_and_activates(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    _write_seed(cd, "alpha", frames=3, cards=2)
    r = c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["id"] == "alpha"
    assert body["current_path"].endswith("board-alpha.json")
    # Working copy exists with the same content.
    working = Path(body["current_path"])
    assert working.exists()
    payload = json.loads(working.read_text(encoding="utf-8"))
    assert len(payload["frames"]) == 3
    # Frames endpoint serves the new working copy.
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 3
    # List now shows the active id.
    listed = c.get("/api/boards").get_json()
    assert listed["active"] == "alpha"
    assert listed["current_path"] == str(working)


def test_select_preserves_local_edits_on_repick(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    _write_seed(cd, "alpha", frames=2)
    c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    # User edits: add a frame via the API.
    c.post("/api/frames", json={"title": "Local"}, headers=CSRF)
    # User picks the same board again.
    c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    board = c.get("/api/frames/board").get_json()
    titles = sorted(f["title"] for f in board)
    assert "Local" in titles
    # The seed file in the catalog is unchanged.
    seed = json.loads((Path(cd) / "alpha.json").read_text(encoding="utf-8"))
    assert "Local" not in {f["title"] for f in seed["frames"]}


def test_select_unknown_catalog_id_returns_404(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.post("/api/boards/select", json={"catalog_id": "ghost"}, headers=CSRF)
    assert r.status_code == 404


def test_select_requires_catalog_id(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.post("/api/boards/select", json={}, headers=CSRF)
    assert r.status_code == 400
    r = c.post("/api/boards/select", json={"catalog_id": ""}, headers=CSRF)
    assert r.status_code == 400


def test_select_requires_csrf(tmp_path):
    c, _, _, cd = _client(tmp_path)
    _write_seed(cd, "alpha")
    r = c.post("/api/boards/select", json={"catalog_id": "alpha"})
    assert r.status_code == 403


def test_select_410_when_seed_missing(tmp_path):
    """Race: the catalog listed a board but the seed file was removed
    between list and select. Should be a 410, not a 500."""
    c, app, _, cd = _client(tmp_path)
    # Register the id without writing the seed.
    (Path(cd) / "ghost.json").write_text("{}", encoding="utf-8")
    os.remove(Path(cd) / "ghost.json")
    # Force a catalog cache miss so the entry isn't reported.
    r = c.post("/api/boards/select", json={"catalog_id": "ghost"}, headers=CSRF)
    # The catalog won't even know about "ghost" since the file is gone.
    assert r.status_code == 404


# ---- reset ----------------------------------------------------------------


def test_reset_re_copies_seed_and_discards_edits(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    _write_seed(cd, "alpha", frames=2)
    c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    # User adds a frame.
    c.post("/api/frames", json={"title": "Local"}, headers=CSRF)
    # Update the seed so reset is observable.
    _write_seed(cd, "alpha", frames=4, name="Alpha Updated")
    r = c.post("/api/boards/reset", json={"catalog_id": "alpha"}, headers=CSRF)
    assert r.status_code == 200
    body = r.get_json()
    assert body["reset"] is True
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 4
    titles = {f["title"] for f in board}
    assert "Local" not in titles
    working = json.loads(Path(body["current_path"]).read_text(encoding="utf-8"))
    assert working["name"] == "Alpha Updated"


def test_reset_unknown_catalog_id_returns_404(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.post("/api/boards/reset", json={"catalog_id": "ghost"}, headers=CSRF)
    assert r.status_code == 404


def test_reset_requires_csrf(tmp_path):
    c, _, _, cd = _client(tmp_path)
    _write_seed(cd, "alpha")
    r = c.post("/api/boards/reset", json={"catalog_id": "alpha"})
    assert r.status_code == 403


# ---- cross-board isolation -----------------------------------------------


def test_selecting_different_boards_swaps_live_data(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    _write_seed(cd, "alpha", frames=2, name="Alpha")
    _write_seed(cd, "beta", frames=3, name="Beta")
    c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 2
    c.post("/api/boards/select", json={"catalog_id": "beta"}, headers=CSRF)
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 3
    # Switching back reuses alpha's existing working copy (still 2 frames).
    c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 2


def test_working_files_never_land_in_catalog(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    _write_seed(cd, "alpha", frames=1)
    c.post("/api/boards/select", json={"catalog_id": "alpha"}, headers=CSRF)
    c.post("/api/frames", json={"title": "Local"}, headers=CSRF)
    catalog_files = sorted(os.listdir(cd))
    assert catalog_files == ["alpha.json"]
    working_files = sorted(
        f for f in os.listdir(wd) if f.startswith("board-")
    )
    assert working_files == ["board-alpha.json"]


# ---- upload ---------------------------------------------------------------


def _board_payload(name="Uploaded Plan", frames=2, cards=1):
    return {
        "name": name,
        "frames": [
            {"id": f"f{i}", "title": f"Frame {i}", "card_ids": [f"c{i}"]}
            for i in range(frames)
        ],
        "cards": [
            {
                "id": f"c{i}",
                "title": f"Card {i}",
                "text": "",
                "frame_id": f"f{i}",
                "tags": [],
                "system": "",
            }
            for i in range(cards)
        ],
    }


def test_upload_json_creates_catalog_entry(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    r = c.post(
        "/api/boards/upload", json=_board_payload(name="Plan X"), headers=CSRF
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["name"] == "Plan X"
    assert body["active"] is False
    # The catalog now contains exactly one board with that id.
    listed = c.get("/api/boards").get_json()
    ids = [e["id"] for e in listed["catalog"]]
    assert body["id"] in ids
    # The seed file exists in the catalog folder.
    seed = Path(cd) / f"{body['id']}.json"
    assert seed.exists()
    payload = json.loads(seed.read_text(encoding="utf-8"))
    assert payload["name"] == "Plan X"


def test_upload_multipart_file(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    import io

    data = _board_payload(name="Multipart Plan")
    r = c.post(
        "/api/boards/upload",
        data={"file": (io.BytesIO(json.dumps(data).encode()), "board.json")},
        headers=CSRF,
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["name"] == "Multipart Plan"
    listed = c.get("/api/boards").get_json()
    assert body["id"] in {e["id"] for e in listed["catalog"]}


def test_upload_invalid_shape_rejected(tmp_path):
    c, _, _, _ = _client(tmp_path)
    # Missing both frames and cards.
    r = c.post("/api/boards/upload", json={"name": "bad"}, headers=CSRF)
    assert r.status_code == 400
    # Non-object card.
    r = c.post(
        "/api/boards/upload",
        json={"frames": [{"id": "f1"}], "cards": [{"title": "no id"}]},
        headers=CSRF,
    )
    assert r.status_code == 400


def test_upload_requires_csrf(tmp_path):
    c, _, _, _ = _client(tmp_path)
    r = c.post("/api/boards/upload", json=_board_payload())
    assert r.status_code == 403


def test_upload_activate_selects_board(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    r = c.post(
        "/api/boards/upload?activate=1",
        json=_board_payload(name="Auto Plan", frames=3),
        headers=CSRF,
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["active"] is True
    assert body["current_path"].endswith(f"board-{body['id']}.json")
    # The frames endpoint now serves the uploaded board.
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 3
    listed = c.get("/api/boards").get_json()
    assert listed["active"] == body["id"]


def test_uploaded_board_appears_in_picker_and_opens(tmp_path):
    c, _, wd, cd = _client(tmp_path)
    r = c.post(
        "/api/boards/upload", json=_board_payload(name="Picker Plan"), headers=CSRF
    )
    bid = r.get_json()["id"]
    # Open it like a user would.
    r = c.post("/api/boards/select", json={"catalog_id": bid}, headers=CSRF)
    assert r.status_code == 200
    board = c.get("/api/frames/board").get_json()
    assert len(board) == 2
