/*
 * Pure drag-and-drop helpers shared by main.js.
 *
 * Kept framework-free and side-effect-free (no fetch / no document globals)
 * so the index math can be unit-tested directly in Node.
 *
 * Works both as a browser global (window.DnD) and a Node module (module.exports)
 * via the UMD-style wrapper below.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.DnD = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /**
   * Position the placeholder so it sits right before the first non-dragging
   * card whose vertical midpoint is below `y` (or at the end if none).
   * Returns the card element to insert before, or null for "append at end".
   */
  function cardAfterPoint(box, y) {
    const cards = Array.prototype.slice.call(
      box.querySelectorAll(".card:not(.dragging)")
    );
    for (const c of cards) {
      const rect = c.getBoundingClientRect();
      if (y < rect.top + rect.height / 2) return c;
    }
    return null;
  }

  /**
   * Compute the insert index for the dragged card given the current
   * placeholder position inside `box`.
   *
   * The index is the number of non-dragging `.card` elements that appear
   * *before* the placeholder in DOM order. This is correct for every case:
   *   - placeholder at the very top  -> 0   (insert at front)
   *   - placeholder in the middle    -> N   (insert between cards)
   *   - placeholder at the end       -> count of cards (append)
   *   - no placeholder (null)        -> count of cards (append fallback)
   *   - cross-frame drops            -> counts target frame's own cards
   *
   * Using the DOM position (not a server-order index derived from a sibling
   * card id) makes same-frame reordering exact and avoids off-by-one errors.
   */
  function computeInsertIndex(box, placeholder) {
    let count = 0;
    const children = box.children;
    for (let i = 0; i < children.length; i++) {
      const n = children[i];
      if (n === placeholder) break;
      if (
        n.classList &&
        n.classList.contains("card") &&
        !n.classList.contains("dragging")
      ) {
        count++;
      }
    }
    return count;
  }

  return { cardAfterPoint, computeInsertIndex };
});
