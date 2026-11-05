"use strict";

// Width synchronisation for the card editor.
//
// The description textarea can be resized by the user (`resize: both`). When it
// grows, the whole editor panel has to grow with it, otherwise the textarea
// pushes past the panel edge and the other fields look broken next to it.
//
// The sizing maths lives here as a pure function so the Node tests in
// tests/test_modalwidth_js.js exercise the real shipped code instead of a copy.
// main.js does the DOM reads/writes and the ResizeObserver wiring.
(function (root) {
  // The editor may shrink to fit a small window, but not below something that
  // still fits a title field and the Save/Cancel row.
  const MIN_CARD_WIDTH = 320;

  // How much of the window the editor may use. This MUST match
  // `.card-modal-card { max-width: 94vw }` in style.css: `max-width` still
  // applies to the inline width this module writes, so if the two disagree the
  // stylesheet silently clamps the panel while the field keeps the wider size
  // this module assigned, and the field pokes out of the panel.
  const VIEWPORT_FRACTION = 0.94;

  function num(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function clamp(value, low, high) {
    if (value < low) return low;
    if (value > high) return high;
    return value;
  }

  // `padding` is the panel's TOTAL horizontal padding (left + right), which is
  // what main.js measures from the computed style.
  //
  // Both the panel and the textarea are border-box, and the field keeps `20px`
  // on each side of the panel's content box. So a field of width W needs a
  // panel of W + padding to align with it.
  function cardWidthForTextarea(textareaWidth, padding) {
    const width = num(textareaWidth, 0);
    const pad = num(padding, 0);
    if (width <= 0) return 0;
    return Math.ceil(width + pad);
  }

  // Decide the final textarea + card widths for one measurement.
  //
  // Returns null when there is nothing to do (the textarea is not laid out yet,
  // e.g. while the modal is still `hidden` and reports a width of 0).
  function planSizes(opts) {
    const o = opts || {};
    const padding = num(o.cardPadding, 0);
    const minCardWidth = num(o.minCardWidth, MIN_CARD_WIDTH);
    const viewportWidth = num(o.viewportWidth, 0);
    const textareaWidth = num(o.textareaWidth, 0);
    if (textareaWidth <= 0) return null;

    // Never let the editor grow past what the stylesheet allows it to occupy.
    // When the window is smaller than the minimum card width, the minimum wins:
    // a card the user can reach the edges of beats a field squeezed to nothing.
    const maxCardWidth = viewportWidth > 0 ? viewportWidth * VIEWPORT_FRACTION : Infinity;
    const maxTextarea = maxCardWidth - padding;
    const minTextarea = minCardWidth - padding;
    const high = Math.max(minTextarea, maxTextarea);

    const width = clamp(textareaWidth, minTextarea, high);
    return {
      textareaWidth: width,
      cardWidth: cardWidthForTextarea(width, padding),
    };
  }

  const api = {
    MIN_CARD_WIDTH: MIN_CARD_WIDTH,
    VIEWPORT_FRACTION: VIEWPORT_FRACTION,
    clamp: clamp,
    cardWidthForTextarea: cardWidthForTextarea,
    planSizes: planSizes,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.ModalWidth = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
