"""Regression tests for GET /api/tree (the tree-view data source).

The card that started this ("unlimited tree structures") is a *grandchild*:
``parent`` -> ``child`` -> the card.  It never appeared in the tree view.

Root cause: the renderer treated a node as a child only by way of the
``isChild`` flag, and recursed with ``if (!isChild && node.children.length)``.
Recursion therefore stopped at depth 2 — a grandchild was rendered as a leaf
with no children of its own, so a card nested two levels deep is invisible.

The fix makes nesting depend on the data (``node.children``), not on how the
node was reached, so any depth renders.  ``tree_view.html`` must stay ES5: it
is built with string concatenation and no arrow functions, template literals
or ``let``/``const``, because IE-mode webviews choke on ES6.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app

from conftest import TEST_CSRF_HEADER, TEST_CSRF_TOKEN

TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "src" / "agent1" / "templates" / "tree_view.html"
)

# The renderer moved out of the template's inline script into a static module
# (``static/treeview.js``) shared with the board's scripts. The assertions
# below are about the *renderer*, so they read the module; the ES5 contract
# and the CSP contract stay anchored to the template (see
# ``test_tree_view_html_stays_es5`` and ``test_served_tree_view_has_no_depth_gate``).
RENDERER = (
    Path(__file__).resolve().parents[1]
    / "src" / "agent1" / "static" / "treeview.js"
)


def _renderer_src() -> str:
    """The renderer source, plus the HTML of the page that loads it — so a
    grep assertion passes only when the served /tree-view really references
    the module containing it. A stale template that dropped the ``<script>``
    tag must fail."""
    html = _client().get("/tree-view").get_data(as_text=True)
    return RENDERER.read_text(encoding="utf-8") + "\n" + html


def _client():
    app = create_app()
    app.config["TESTING"] = True
    app.config["TEST_CSRF_TOKEN"] = TEST_CSRF_TOKEN
    return app.test_client()


class _CsrfClient:
    """Test client that sends the fixed TESTING CSRF token on writes.

    The real double-submit guard runs under TESTING (see ``security.py``), so
    POST/PUT need the header — GETs are exempt as safe methods.
    """

    def __init__(self, inner):
        self._inner = inner

    def get(self, url, **kw):
        return self._inner.get(url, **kw)

    def post(self, url, **kw):
        kw["headers"] = {**kw.get("headers", {}), **TEST_CSRF_HEADER}
        return self._inner.post(url, **kw)

    def put(self, url, **kw):
        kw["headers"] = {**kw.get("headers", {}), **TEST_CSRF_HEADER}
        return self._inner.put(url, **kw)


def _api():
    return _CsrfClient(_client())


def _seed_three_level_tree(client):
    """frame: ``parent`` -> ``child`` -> ``grandchild`` (all in one frame)."""
    resp = client.post("/api/frames", json={"title": "Planned"})
    assert resp.status_code == 201, resp.get_json()
    frame_id = resp.get_json()["id"]

    created = {}
    for title in ("parent", "child", "grandchild"):
        resp = client.post("/api/cards", json={"title": title, "frame_id": frame_id})
        assert resp.status_code == 201, resp.get_json()
        created[title] = resp.get_json()["id"]

    parent = created["parent"]
    child = created["child"]
    grandchild = created["grandchild"]

    r = client.put(f"/api/cards/{child}", json={"parent_id": parent})
    assert r.status_code == 200, r.get_json()
    r = client.put(f"/api/cards/{grandchild}", json={"parent_id": child})
    assert r.status_code == 200, r.get_json()
    return created


def _titles_at_depth(nodes, depth):
    """Titles of every node exactly ``depth`` levels below the given nodes."""
    if depth == 0:
        return sorted(n["title"] for n in nodes)
    found = []
    for node in nodes:
        found.extend(_titles_at_depth(node.get("children") or [], depth - 1))
    return sorted(found)


def _frame_entry(tree, title="Planned"):
    """The tree payload entry for the frame named ``title``."""
    entries = [e for e in tree if e["frame_title"] == title]
    assert len(entries) == 1, f"expected one {title!r} frame, got {tree}"
    return entries[0]


def test_api_tree_returns_grandchild():
    """The API already nests three levels — the bug is not server-side."""
    client = _api()
    _seed_three_level_tree(client)
    tree = client.get("/api/frames/tree").get_json()

    parents = _frame_entry(tree)["parents"]
    assert [n["title"] for n in parents] == ["parent"]
    assert _titles_at_depth(parents, 1) == ["child"]
    assert _titles_at_depth(parents, 2) == ["grandchild"], (
        "a grandchild must be present in the tree payload"
    )


def test_renderer_recurses_on_children_not_on_is_child():
    """Nesting must be driven by ``node.children``, never by ``isChild``.

    The old guard ``if (!isChild && node.children.length)`` stopped at depth 2,
    so a child card that itself has children rendered as a leaf and its whole
    subtree disappeared.
    """
    src = _renderer_src()

    assert "node.children" in src, "renderer must inspect node.children"
    assert "!isChild && node.children && node.children.length" not in src, (
        "recursion gated on !isChild caps the tree at depth 2 — grandchildren "
        "render as leaves and vanish"
    )


def test_renderer_renders_children_of_child_nodes():
    """A child node with children must emit a nested <ul> for them."""
    src = _renderer_src()

    # The recursion call must pass `true` (this node is a child) while being
    # reachable from child nodes too — i.e. it sits in the data-driven branch.
    assert "renderCard(child, true)" in src, "children must render as child nodes"
    # The "No child cards" hint belongs to leaf nodes only; keying it on
    # !isChild hid grandchildren behind a parent-only affordance.
    assert "No child cards" in src, "leaf hint must remain for real leaves"


def test_tree_view_html_stays_es5():
    """The template is string-concatenated ES5 — webviews are IE-mode."""
    src = TEMPLATE.read_text(encoding="utf-8")

    for forbidden in ("=>", "${", "let ", "const ", "`"):
        assert forbidden not in src, (
            f"tree_view.html must stay ES5 (found {forbidden!r})"
        )


def test_deep_tree_renders_all_levels():
    """Five levels deep: every level is present in the payload.

    Guards the 'unlimited' half of the card — the 50-deep recursion cap is a
    corruption guard against circular ``parent_id`` chains, not a feature limit.
    """
    client = _api()
    created = _seed_three_level_tree(client)
    frame_id = client.get("/api/frames").get_json()[0]["id"]

    chain = [created["grandchild"]]
    for i in range(2):
        resp = client.post(
            "/api/cards", json={"title": f"level{i}", "frame_id": frame_id}
        )
        assert resp.status_code == 201, resp.get_json()
        new_id = resp.get_json()["id"]
        r = client.put(f"/api/cards/{new_id}", json={"parent_id": chain[-1]})
        assert r.status_code == 200, r.get_json()
        chain.append(new_id)

    tree = client.get("/api/frames/tree").get_json()
    parents = _frame_entry(tree)["parents"]
    for depth in range(5):
        titles = _titles_at_depth(parents, depth)
        assert len(titles) == 1, (
            f"depth {depth} must hold exactly one node, got {titles}"
        )


def test_served_tree_view_has_no_depth_gate():
    """The page Flask serves must load a renderer free of the depth-2 gate.

    The live board regression: a card two levels deep (``parent`` -> ``child``
    -> card) never appeared in /tree-view because the renderer recursed only
    from top-level nodes.  A source-file grep can miss a stale copy served
    from elsewhere, so assert against the response body itself: the page must
    reference the renderer module, and neither that module nor the page may
    carry the depth-2 gate.
    """
    html = _client().get("/tree-view").get_data(as_text=True)

    # The renderer lives in a static module now; a page that stopped loading
    # it would render nothing at all, so pin the wiring on the served HTML.
    assert "/static/treeview.js" in html, (
        "the served page must load the tree renderer"
    )
    src = _renderer_src()  # module source + served HTML

    assert "node.children" in src, (
        "the renderer must inspect node.children at every depth"
    )
    assert "!isChild && node.children && node.children.length" not in src, (
        "the renderer still carries the depth-2 gate — the card nested "
        "under a child card will not render"
    )


def test_parent_id_survives_reload(tmp_path):
    """The store must persist ``parent_id`` across a reload.

    Root cause of the card: ``BoardStore._load`` rebuilt every ``Card``
    without reading ``parent_id`` from the JSON, so after any restart (or
    any store re-init on the same file) every card became a root and the
    whole tree collapsed — the /tree-view showed a flat list.
    """
    from src.agent1.models import Card, Frame
    from src.agent1.store import BoardStore

    path = str(tmp_path / "board.json")

    store = BoardStore(path=path)
    store.add_frame(Frame(id="f1", title="Planned"))
    store.add_card(
        Card(id="p1", title="parent", text="", frame_id="f1"), "f1"
    )
    store.add_card(
        Card(id="c1", title="child", text="", frame_id="f1", parent_id="p1"),
        "f1",
    )
    store.add_card(
        Card(id="g1", title="grandchild", text="", frame_id="f1",
             parent_id="c1"),
        "f1",
    )

    reloaded = BoardStore(path=path)
    by_id = {c.id: c for c in reloaded.get_all_cards()}
    assert by_id["c1"].parent_id == "p1", (
        "parent_id was dropped on load — the tree flattens to a list"
    )
    assert by_id["g1"].parent_id == "c1"

    # The API view over a fresh app must serve the tree endpoint (200).
    client = _api()
    tree = client.get("/api/frames/tree")
    assert tree.status_code == 200
