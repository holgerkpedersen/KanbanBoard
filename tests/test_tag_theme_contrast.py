"""Regression test: card tag chips must be readable in every theme.

Historical bug: ``.tag`` hard-coded ``color: #b9ccff`` (a pale blue meant for
the dark theme). In the light theme the chip sits on a pale accent wash over a
near-white card, so that text had a contrast ratio of only ~1.4:1 -- effectively
invisible. The colours now come from per-theme ``--tag-bg`` / ``--tag-text``
variables on the card surface.

This test parses the real ``style.css`` (no browser, no CSS engine) and checks
every theme's tag palette against the WCAG 2.1 contrast formula. It fails if a
future edit re-introduces a hard-coded light-on-light tag colour.
"""

import re
from pathlib import Path

import pytest

CSS_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "agent1" / "static" / "style.css"
)

# WCAG 2.1 AA for normal-size body text (tag chips are 11px -> not "large").
AA_NORMAL = 4.5
# 1.4.11 non-text contrast, for the chip border against the card surface.
AA_NON_TEXT = 3.0

THEMES = ["dark", "light", "contrast", "contrast-dark"]


def _read_css() -> str:
    return CSS_PATH.read_text(encoding="utf-8")


def _theme_block(css: str, theme: str) -> str:
    """Return the declaration block for ``:root[data-theme="<theme>"]``.

    The dark theme shares one block with the bare ``:root`` selector, which is
    the first match for ``dark``; every other theme has its own block.
    """
    if theme == "dark":
        pattern = r":root,\s*\n:root\[data-theme=\"dark\"\]\s*\{(.*?)\}"
    else:
        pattern = r":root\[data-theme=\"%s\"\]\s*\{(.*?)\}" % re.escape(theme)
    match = re.search(pattern, css, re.S)
    assert match, f"missing theme block for {theme!r} in style.css"
    return match.group(1)


def _declarations(block: str) -> dict:
    """Map ``--custom-prop`` names to their raw value strings in a block."""
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block)
    }


def _hex_to_rgb(value: str) -> tuple:
    value = value.strip().lstrip("#")
    assert len(value) == 6, f"expected a 6-digit hex colour, got {value!r}"
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _parse_rgba(value: str) -> tuple:
    """Parse ``rgba(r, g, b, a)`` / ``rgb(r, g, b)`` into (rgb, alpha)."""
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value.strip())
    assert match, f"not an rgb()/rgba() colour: {value!r}"
    parts = [p.strip() for p in match.group(1).split(",")]
    assert len(parts) in (3, 4), f"bad rgb() arity: {value!r}"
    rgb = tuple(int(p) for p in parts[:3])
    alpha = float(parts[3]) if len(parts) == 4 else 1.0
    return rgb, alpha


def _parse_color(value: str) -> tuple:
    """Parse any CSS colour used in style.css into (rgb, alpha).

    Handles both the solid hex form (``#000000``, used by the contrast themes)
    and the translucent ``rgba(r, g, b, a)`` form (used by the accent washes).
    """
    value = value.strip()
    if value.startswith("#"):
        return _hex_to_rgb(value), 1.0
    return _parse_rgba(value)


def _composite(fg_rgb: tuple, alpha: float, bg_rgb: tuple) -> tuple:
    """Alpha-composite ``fg_rgb`` over ``bg_rgb`` (source-over)."""
    return tuple(
        round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg_rgb, bg_rgb)
    )


def _channel_luminance(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(rgb: tuple) -> float:
    r, g, b = rgb
    return (
        0.2126 * _channel_luminance(r)
        + 0.7152 * _channel_luminance(g)
        + 0.0722 * _channel_luminance(b)
    )


def contrast_ratio(rgb_a: tuple, rgb_b: tuple) -> float:
    """WCAG 2.1 contrast ratio between two opaque RGB colours."""
    lum_a = _relative_luminance(rgb_a)
    lum_b = _relative_luminance(rgb_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _tag_palette(theme: str) -> dict:
    """Resolve the effective tag colours for ``theme`` from style.css."""
    css = _read_css()
    decls = _declarations(_theme_block(css, theme))
    for required in ("--surface-2", "--tag-bg", "--tag-text", "--tag-border"):
        assert required in decls, f"{theme}: missing {required} (regression!)"
    return decls


# --------------------------------------------------------------------------
# Unit-level checks of the contrast helper itself.
# --------------------------------------------------------------------------
def test_contrast_ratio_bounds():
    """Black on white is 21:1 and a colour against itself is 1:1."""
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio((18, 27, 34), (18, 27, 34)) == pytest.approx(1.0)


def test_contrast_ratio_is_symmetric():
    a, b = (27, 63, 168), (230, 237, 255)
    assert contrast_ratio(a, b) == pytest.approx(contrast_ratio(b, a))


def test_composite_respects_alpha_extremes():
    """alpha=0 yields the backdrop, alpha=1 yields the source colour."""
    fg, bg = (47, 107, 255), (255, 255, 255)
    assert _composite(fg, 0.0, bg) == bg
    assert _composite(fg, 1.0, bg) == fg


# --------------------------------------------------------------------------
# The actual regression: tag text must be readable on the card in every theme.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("theme", THEMES)
def test_tag_text_contrast_meets_wcag_aa(theme):
    """Tag text on the card must reach WCAG AA (4.5:1) in every theme."""
    decls = _tag_palette(theme)
    card = _hex_to_rgb(decls["--surface-2"])
    wash_rgb, wash_alpha = _parse_rgba(decls["--tag-bg"])
    chip = _composite(wash_rgb, wash_alpha, card)
    text = _hex_to_rgb(decls["--tag-text"])

    ratio = contrast_ratio(text, chip)
    assert ratio >= AA_NORMAL, (
        f"{theme}: tag text {decls['--tag-text']} on chip {chip} is only "
        f"{ratio:.2f}:1, below the WCAG AA minimum of {AA_NORMAL}:1"
    )


@pytest.mark.parametrize("theme", THEMES)
def test_tag_border_is_visible_against_the_card(theme):
    """The chip border must be perceivable on the card (WCAG 1.4.11, 3:1)."""
    decls = _tag_palette(theme)
    card = _hex_to_rgb(decls["--surface-2"])
    border_rgb, border_alpha = _parse_color(decls["--tag-border"])
    border = _composite(border_rgb, border_alpha, card)
    ratio = contrast_ratio(border, card)
    assert ratio >= AA_NON_TEXT, (
        f"{theme}: tag border {decls['--tag-border']} on card {card} is only "
        f"{ratio:.2f}:1, below {AA_NON_TEXT}:1"
    )


def test_tag_rule_uses_theme_variables():
    """``.tag`` must not hard-code a colour (that caused the light-theme bug)."""
    css = _read_css()
    match = re.search(r"\n\.tag\s*\{(.*?)\}", css, re.S)
    assert match, "missing .tag rule in style.css"
    rule = match.group(1)
    assert "background: var(--tag-bg)" in rule
    assert "color: var(--tag-text)" in rule
    # No literal hex/rgba colour in the rule: everything comes from a theme.
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", rule), (
        f".tag hard-codes a hex colour: {rule}"
    )
    assert "rgba(" not in rule, f".tag hard-codes an rgba colour: {rule}"


def test_light_theme_no_longer_inherits_dark_tag_text():
    """The old pale-blue body colour must not appear in the light theme block."""
    decls = _declarations(_theme_block(_read_css(), "light"))
    assert decls["--tag-text"].lower() != "#b9ccff", (
        "light theme reverted to the dark theme's pale tag text (1.4:1 contrast)"
    )
