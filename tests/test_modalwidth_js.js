/*
 * Node test for the card-editor width maths in static/modalwidth.js.
 * No browser/jsdom required: planSizes is pure.
 *
 * The invariant under test: the description textarea can be resized by the
 * user, and the editor panel must follow it, keeping every other field inside
 * the panel and aligned with it.
 */
const assert = require("assert");
const ModalWidth = require("../src/agent1/static/modalwidth.js");

// TOTAL horizontal padding of the panel (20px left + 20px right), which is what
// main.js measures from the computed style.
const PAD = 40;

// --- cardWidthForTextarea --------------------------------------------------
// A border-box panel wrapping a border-box field: panel = field + 2*padding.
assert.strictEqual(ModalWidth.cardWidthForTextarea(400, PAD), 440);
assert.strictEqual(ModalWidth.cardWidthForTextarea(300, 0), 300);
// A not-yet-laid-out field (modal still hidden) reports 0 and must not resize.
assert.strictEqual(ModalWidth.cardWidthForTextarea(0, PAD), 0);
// Fractional layout widths round up rather than truncating a field's room.
assert.strictEqual(ModalWidth.cardWidthForTextarea(400.4, PAD), 441);

// --- planSizes returns null when there is nothing to measure ---------------
assert.strictEqual(
  ModalWidth.planSizes({ textareaWidth: 0, cardPadding: PAD, viewportWidth: 1200 }),
  null,
  "a zero-width (hidden) textarea must not trigger a resize"
);
assert.strictEqual(
  ModalWidth.planSizes({ textareaWidth: NaN, cardPadding: PAD, viewportWidth: 1200 }),
  null
);
assert.strictEqual(ModalWidth.planSizes(), null, "no options at all is a no-op");

// --- widening the description widens the panel ----------------------------
const narrow = ModalWidth.planSizes({
  textareaWidth: 400,
  cardPadding: PAD,
  viewportWidth: 1200,
});
const wide = ModalWidth.planSizes({
  textareaWidth: 600,
  cardPadding: PAD,
  viewportWidth: 1200,
});
assert.strictEqual(narrow.cardWidth, 440);
assert.strictEqual(wide.cardWidth, 640);
assert.ok(
  wide.cardWidth > narrow.cardWidth,
  "a wider description must produce a wider editor panel"
);

// The panel always leaves exactly `padding` on each side of the field, which is
// what keeps the other fields (width: 100%) aligned with the description.
for (const w of [340, 400, 480, 600, 720]) {
  const plan = ModalWidth.planSizes({
    textareaWidth: w,
    cardPadding: PAD,
    viewportWidth: 2000,
  });
  assert.strictEqual(
    plan.cardWidth - plan.textareaWidth,
    PAD,
    `panel (${plan.cardWidth}) must exceed field (${plan.textareaWidth}) by the padding`
  );
}

// --- the panel never runs off the window -----------------------------------
// The cap is the stylesheet's max-width (94vw), not the whole window: `max-width`
// still applies to the inline width this module writes, so assigning the full
// viewport would leave the field wider than the panel the browser renders.
const huge = ModalWidth.planSizes({
  textareaWidth: 2000,
  cardPadding: PAD,
  viewportWidth: 900,
});
assert.strictEqual(huge.cardWidth, Math.floor(900 * ModalWidth.VIEWPORT_FRACTION));
assert.strictEqual(huge.cardWidth, 846);
assert.strictEqual(huge.textareaWidth, 806);
assert.ok(huge.cardWidth <= 900, "the panel must stay inside the window");
// A field wider than the cap is trimmed so it still fits the clamped panel.
assert.strictEqual(huge.cardWidth - huge.textareaWidth, PAD);

// --- the panel never collapses below a usable minimum ----------------------
const tiny = ModalWidth.planSizes({
  textareaWidth: 50,
  cardPadding: PAD,
  viewportWidth: 1200,
});
assert.strictEqual(tiny.cardWidth, ModalWidth.MIN_CARD_WIDTH);
assert.strictEqual(tiny.textareaWidth, ModalWidth.MIN_CARD_WIDTH - PAD);
assert.ok(tiny.cardWidth >= 320);

// A custom minimum is honoured (callers may pass their own floor).
const custom = ModalWidth.planSizes({
  textareaWidth: 50,
  cardPadding: PAD,
  viewportWidth: 1200,
  minCardWidth: 500,
});
assert.strictEqual(custom.cardWidth, 500);

// On a window narrower than the minimum the minimum still wins: a card the user
// can reach the edges of beats a field squeezed to nothing.
const narrowWindow = ModalWidth.planSizes({
  textareaWidth: 200,
  cardPadding: PAD,
  viewportWidth: 300,
});
assert.strictEqual(narrowWindow.cardWidth, ModalWidth.MIN_CARD_WIDTH);
assert.ok(
  narrowWindow.textareaWidth >= 1,
  "the field must keep a positive width even on a tiny window"
);

// --- width is preserved when the viewport cannot be measured ---------------
const noViewport = ModalWidth.planSizes({
  textareaWidth: 500,
  cardPadding: PAD,
  viewportWidth: 0,
});
assert.strictEqual(noViewport.cardWidth, 540);

// --- clamp helper ----------------------------------------------------------
assert.strictEqual(ModalWidth.clamp(5, 10, 20), 10);
assert.strictEqual(ModalWidth.clamp(15, 10, 20), 15);
assert.strictEqual(ModalWidth.clamp(25, 10, 20), 20);

// --- a settled plan is a fixed point ---------------------------------------
// syncModalWidth re-plans from the width it just wrote; that must not change
// anything, otherwise the ResizeObserver would keep firing forever.
const settled = ModalWidth.planSizes({
  textareaWidth: 600,
  cardPadding: PAD,
  viewportWidth: 1200,
});
const again = ModalWidth.planSizes({
  textareaWidth: settled.textareaWidth,
  cardPadding: PAD,
  viewportWidth: 1200,
});
assert.deepStrictEqual(again, settled, "re-planning a settled layout must be a no-op");

console.log("ALL PASS modalwidth");
