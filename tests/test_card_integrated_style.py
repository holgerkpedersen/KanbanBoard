"""Pins for the integrated-card colouring (card + badge).

The integration feature marks cards linked to an Agent1 issue with the
``.card-integrated`` class (added in ``main.js`` ``renderCard``) and shows a
``.card-integrated-badge`` pill.  An earlier pass shipped the JS with **no CSS
rule** for the class, so the "colour" silently did nothing — these tests pin
the two halves that were missing:

* ``static/style.css`` defines ``.card-integrated`` and gives it a visible
  accent built from theme variables (not a hard-coded hex), plus a rule for
  the badge and an explicit hidden-state guard;
* the card template ships the badge element (hidden by default) so the JS
  path that reveals it never relies on a missing node.
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


def _css_rules(selector: str) -> list[str]:
    """Declaration bodies of every rule that *starts* with ``selector``.

    Comments are stripped first so prose mentioning a property cannot match
    the declaration check (same trick as tests/test_card_modal.py).
    """
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    return [
        m.group(1)
        for m in re.finditer(
            r"(?m)^" + re.escape(selector) + r"[^{}]*\{(.*?)\}", css, re.S
        )
    ]


def test_card_integrated_rule_exists_and_is_visible():
    """`.card-integrated` must carry a real visual treatment, not an empty rule."""
    rules = _css_rules(".card-integrated")
    assert rules, "static/style.css has no .card-integrated rule"
    body = rules[0]
    declarations = [d.strip() for d in body.split(";") if d.strip()]
    assert declarations, ".card-integrated rule is empty — the class would do nothing"
    # A visible accent: border and/or background must be declared.
    props = ";".join(declarations)
    assert "border" in props or "background" in props, (
        ".card-integrated declares neither a border nor a background"
    )


def test_card_integrated_is_theme_aware():
    """Colour comes from CSS custom properties, so light/dark themes follow."""
    body = _css_rules(".card-integrated")[0]
    assert "var(--" in body, (
        ".card-integrated must build its colour from theme variables"
    )
    # Hard-coded hex colours would not adapt to the contrast themes.
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), (
        ".card-integrated uses a hard-coded hex colour"
    )


def test_card_integrated_badge_rule_and_hidden_guard():
    """The badge needs a rule *and* an explicit hidden guard: the base rule's
    display beats the `hidden` attribute, exactly like .modal-id-field does."""
    assert _css_rules(".card-integrated-badge"), (
        "static/style.css has no .card-integrated-badge rule"
    )
    hidden_rules = _css_rules(".card-integrated-badge[hidden]")
    assert hidden_rules, "no .card-integrated-badge[hidden] guard"
    assert "display: none" in hidden_rules[0].replace("  ", " "), (
        ".card-integrated-badge[hidden] must set display:none"
    )


def test_card_template_ships_the_badge_element():
    """renderCard reveals `.card-integrated-badge`; the node must exist in the
    card template, hidden by default so new renders start clean."""
    html = _index_html()
    badge_re = re.compile(
        r"<[^>]*class=\"[^\"]*card-integrated-badge[^\"]*\"[^>]*>"
    )
    match = badge_re.search(html)
    assert match, "no .card-integrated-badge element in the served page"
    assert "hidden" in match.group(0), (
        "the badge must ship hidden; JS reveals it only for linked cards"
    )


def test_main_js_wires_the_class_and_badge():
    """The JS side of the pin: renderCard adds the class and unhides the badge."""
    main_js = (STATIC / "main.js").read_text(encoding="utf-8")
    assert 'classList.add("card-integrated")' in main_js
    assert '.card-integrated-badge' in main_js
