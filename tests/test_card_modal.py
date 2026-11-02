"""Regression tests for the card editor modal markup and sizing.

Two things are pinned here:

* The editor offers a copyable card ID (the reference users quote), and that
  row is genuinely absent for a new card.
* The editor is large enough to be usable and can never push its own Save /
  Cancel buttons off-screen.

The `hidden` attribute is the interesting case: `.modal-id-field` sets
`display: flex`, which otherwise *beats* the `hidden` attribute, so the ID row
leaked onto the "New card" form with an empty input and a dead Copy button.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent1 import create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "agent1" / "static"


def _index_html() -> str:
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client().get("/").data.decode("utf-8")


def _css() -> str:
    return (STATIC / "style.css").read_text(encoding="utf-8")


def _card_modal_markup() -> str:
    """The card editor markup only (not the board picker modal)."""
    html = _index_html()
    start = html.index('id="card-modal"')
    end = html.index('id="frame-template"')
    return html[start:end]


def test_card_editor_exposes_copyable_card_id():
    form = _card_modal_markup()
    # Read-only ID input plus its dedicated, non-submitting copy button.
    assert 'id="card-id"' in form
    assert "readonly" in form
    assert 'id="card-id-copy"' in form
    # type="button" is what stops Copy from submitting the form.
    copy_btn = re.search(r"<button id=\"card-id-copy\"[^>]*>", form).group(0)
    assert 'type="button"' in copy_btn
    assert "Copy" in form


def test_card_id_row_is_hidden_for_new_cards():
    """`hidden` must actually hide the row despite `display: flex`."""
    form = _card_modal_markup()
    assert 'id="card-id-field"' in form
    assert "hidden" in re.search(r'<div id="card-id-field"[^>]*>', form).group(0)

    css = _css()
    assert ".modal-id-field" in css
    # A non-`hidden` display value plus no `[hidden]` override = the row leaks.
    assert re.search(r"\.modal-id-field\[hidden\]\s*\{[^}]*display:\s*none", css), (
        "a .modal-id-field[hidden] { display: none } rule is required, because "
        ".modal-id-field sets display:flex and would defeat the hidden attribute"
    )


def test_card_editor_is_resized_and_can_scroll():
    form = _card_modal_markup()
    assert "card-modal-card" in form, "editor needs its own sizing class"

    css = _css()
    block = re.search(r"\.card-modal-card\s*\{(.*?)\}", css, re.S).group(1)
    width = re.search(r"width:\s*min\((\d+)px", block)
    assert width, "editor width must be an explicit, capped min() value"
    # Roomier than the 440px default panel it inherits from.
    assert int(width.group(1)) > 440
    # Without a max-height + overflow, the form runs off short viewports and
    # takes the ID row and the Save/Cancel buttons with it.
    assert "max-height" in block
    assert "overflow" in block


def test_card_description_textarea_has_room():
    form = _card_modal_markup()
    rows = re.search(r'<textarea id="card-text"[^>]*rows="(\d+)"', form)
    assert rows, "card description textarea must declare its rows"
    assert int(rows.group(1)) >= 8
