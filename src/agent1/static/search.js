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

  /**
   * Convert an ISO 8601 timestamp string to its browser-LOCAL calendar day
   * as "YYYY-MM-DD" (lexicographically comparable). Returns null when the
   * value is missing or unparseable, so callers can treat it as "no date".
   */
  function localDateOf(iso) {
    if (typeof iso !== "string" || !iso.trim()) return null;
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    const y = String(d.getFullYear()).padStart(4, "0");
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  /**
   * Does `card` fall inside the active date-range filters?
   *
   * `f` may carry any of createdFrom / createdTo / updatedFrom / updatedTo
   * as "YYYY-MM-DD" strings (from <input type="date">). Empty/missing =
   * open-ended. Ranges are INCLUSIVE on both ends and compared as local
   * calendar days. A card with no (or unparseable) date is excluded when
   * that date's filter is active; a fully empty filter matches everything.
   */
  function matchesDates(card, f) {
    if (!f) return true;
    if (!card) return false;

    const groups = [
      ["created", card.created_at],
      ["updated", card.updated_at],
    ];
    for (const [key, value] of groups) {
      const from = lower(f[`${key}From`]).trim();
      const to = lower(f[`${key}To`]).trim();
      if (!from && !to) continue; // this group inactive
      const day = localDateOf(value);
      if (day === null) return false; // card has no usable date
      if (from && day < from) return false;
      if (to && day > to) return false;
    }
    return true;
  }

  /**
   * Does `card` carry at least one of the selected `selectedTags`?
   * Matching is case-insensitive on trimmed, exact tag names. An empty
   * (or missing) selection means "tag filter inactive" -> every card
   * matches.
   */
  function matchesTags(card, selectedTags) {
    const sel = (Array.isArray(selectedTags) ? selectedTags : [])
      .map((t) => lower(t).trim())
      .filter(Boolean);
    if (!sel.length) return true;
    if (!card) return false;
    const tags = Array.isArray(card.tags) ? card.tags : [];
    return tags.some((tag) => sel.includes(lower(tag).trim()));
  }

  /**
   * Is any filter active? A date group counts when it has a non-blank
   * bound; the tag group counts when at least one tag is selected. Used to
   * show the "filtered" hint and enable the Clear button.
   */
  function filtersActive(f) {
    if (!f) return false;
    const keys = ["createdFrom", "createdTo", "updatedFrom", "updatedTo"];
    if (keys.some((k) => typeof f[k] === "string" && f[k].trim() !== "")) return true;
    return (
      Array.isArray(f.tags) &&
      f.tags.some((t) => typeof t === "string" && t.trim() !== "")
    );
  }

  /**
   * Combined visibility test: free-text query AND tag selection AND active
   * date ranges. All three groups combine with AND; each inactive group is
   * a no-op.
   */
  function filterCard(card, query, filters) {
    return (
      matchesQuery(card, query) &&
      matchesTags(card, filters && filters.tags) &&
      matchesDates(card, filters)
    );
  }

  return {
    localDateOf,
    matchesQuery,
    matchesDates,
    matchesTags,
    filtersActive,
    filterCard,
  };
});
