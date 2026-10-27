/*
 * Pure search helpers shared by main.js.
 *
 * Kept framework-free and side-effect-free (no DOM / no fetch) so the
 * matching rules can be unit-tested directly in Node.
 *
 * Works both as a browser global (window.CardSearch) and a Node module
 * (module.exports) via the UMD-style wrapper below.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.CardSearch = api;
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** Lower-case a value for comparison; non-strings become "". */
  function lower(value) {
    return typeof value === "string" ? value.toLowerCase() : "";
  }

  /**
   * Does `card` match the free-text `query`?
   *
   * A card matches when the (trimmed, case-insensitive) query is a
   * substring of any of its searchable properties:
   *   - title
   *   - text (description)
   *   - system (the card's System property, e.g. "Billing Service")
   *   - any tag
   *
   * An empty/blank query matches every card. Missing properties are
   * treated as "" so partially-filled cards never throw.
   */
  function matchesQuery(card, query) {
    const q = lower(query).trim();
    if (!q) return true;
    if (!card) return false;

    if (lower(card.title).includes(q)) return true;
    if (lower(card.text).includes(q)) return true;
    if (lower(card.system).includes(q)) return true;

    const tags = Array.isArray(card.tags) ? card.tags : [];
    return tags.some((tag) => lower(tag).includes(q));
  }

  return { matchesQuery };
});
