"""Regression tests for the card editor modal markup and sizing.

Three things are pinned here:

* The editor offers a copyable card ID (the reference users quote), and that
  row is genuinely absent for a new card.
* The editor is large enough to be usable and can never push its own Save /
  Cancel buttons off-screen.
* Resizing the description field resizes the whole editor with it, and every
  other field follows the new panel width.

The `hidden` attribute is the interesting case: `.modal-id-field` sets
`display: flex`, which otherwise *beats* the `hidden` attribute, so the ID row
leaked onto the "New card" form with an empty input and a dead Copy button.

The width-follow feature has two traps that are easy to reintroduce, both
guarded below: `resize: vertical` (or a `max-width` on the field) lets the
field grow taller but never wider, so there is nothing for the panel to follow.
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


def _css_block(selector: str) -> str:
    """The declarations of one CSS rule, with comments removed.

    The comments have to go first: several of them *mention* the property a
    test asserts is absent (`max-width`), and a plain substring check would
    match the prose instead of the declaration.
    """
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    for match in re.finditer(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S):
        # The first rule for the selector is the one that defines the box.
        return match.group(1)
    raise AssertionError(f"no CSS rule found for {selector!r}")


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


def test_description_field_can_be_resized_in_both_directions():
    """The description must be draggable wider, not just taller."""
    block = _css_block(".card-modal-card textarea")
    resize = re.search(r"resize:\s*([a-z]+)", block)
    assert resize, "the description field must declare its resize axis"
    assert resize.group(1) == "both", (
        "resize: vertical would let the field grow taller but never wider, so "
        "the editor could never follow it"
    )


def test_description_field_is_not_width_capped():
    """A max-width would cap the drag at the panel width and freeze the growth.

    With `max-width: 100%` the field can never become wider than the panel's
    content box, so the panel would never need to grow -- the feature silently
    does nothing.
    """
    block = _css_block(".card-modal-card textarea")
    assert not re.search(r"max-width", block), (
        "the card editor textarea must not set max-width: it caps the user's "
        "drag at the current panel width, so the panel never grows with it"
    )


def test_editor_panel_can_follow_its_own_width():
    """The panel must not be pinned by a flex automatic minimum width."""
    block = _css_block(".card-modal-card")
    # `width:` alone is overridden inside a flex container unless the panel is
    # prevented from shrinking to its content-based minimum.
    assert re.search(r"flex:\s*0 0 auto", block), (
        "the editor panel needs flex: 0 0 auto so a set width is honoured "
        "instead of being pinned to its content minimum"
    )
    assert re.search(r"min-width:\s*0", block)
    assert re.search(r"max-width:\s*94vw", block), (
        "the widened panel still has to stay inside the viewport"
    )


def test_card_fields_stretch_to_the_panel_width():
    """All fields follow the panel width, so they stay aligned with it."""
    css = _css()
    assert re.search(
        r"\.card-modal-card label > input,\s*\.card-modal-card label > textarea"
        r"\s*\{[^}]*width:\s*100%",
        css,
        re.S,
    ), "card editor inputs/textarea must be width: 100% to match the panel"


def test_modal_width_helper_is_loaded_before_main_js():
    """main.js calls ModalWidth, so the helper script must be defined first."""
    html = _index_html()
    helper = html.index("modalwidth.js")
    main = html.index("main.js")
    assert helper < main, "modalwidth.js must load before main.js"


def test_main_js_wires_the_width_follow():
    """The CSS and the helper are useless unless main.js actually drives them."""
    js = (STATIC / "main.js").read_text(encoding="utf-8")
    # The observer is what reports the user's drag.
    assert "new ResizeObserver(syncModalWidth)" in js
    # It has to watch the panel as well as the field, so a squeezed viewport
    # and the field's own drag are both reported.
    assert "widthObserver.observe(cardForm)" in js
    assert "widthObserver.observe(textField)" in js
    # Each open starts from the stylesheet width, or a width dragged out for a
    # previous card leaks into the next one.
    assert "resetCardModalWidth()" in js
    # Writing a width re-triggers the observer; without the re-entrancy guard
    # the two writers can chase each other.
    assert "syncingCardWidth" in js
    # The panel is sized from the field, not the other way round.
    assert "ModalWidth.planSizes(" in js
    assert "cardForm.style.width = plan.cardWidth" in js


def test_js_viewport_cap_matches_the_stylesheet_max_width():
    """The width maths and the stylesheet must cap the panel the same way.

    `max-width: 94vw` still applies to the inline width main.js writes. If the
    JS lets the panel reach the full viewport while CSS clamps it to 94vw, the
    browser renders a narrower panel than the field was sized for, and the field
    pokes out of the panel -- exactly the bug the width-follow feature prevents.
    """
    css_vw = re.search(r"max-width:\s*(\d+)vw", _css_block(".card-modal-card"))
    assert css_vw, "the editor panel must cap itself relative to the viewport"

    js = (STATIC / "modalwidth.js").read_text(encoding="utf-8")
    js_fraction = re.search(r"VIEWPORT_FRACTION\s*=\s*([0-9.]+)", js)
    assert js_fraction, "modalwidth.js must declare its viewport cap"

    assert float(js_fraction.group(1)) * 100 == int(css_vw.group(1)), (
        f"modalwidth.js caps at {js_fraction.group(1)} of the viewport but "
        f"style.css caps at {css_vw.group(1)}vw -- the two must agree"
    )
