/*
 * Node test for the Kanban board search filter in static/search.js.
 *
 * Regression: the System property of a card was not searched, so a card
 * could only be found by title, text or tag. The search filter must also
 * match the card's system name.
 *
 * No browser/jsdom required: matchesQuery is pure and side-effect free.
 */
const assert = require("assert");
const {
  matchesQuery,
  localDateOf,
  matchesDates,
  matchesTags,
  filtersActive,
  filterCard,
} = require("../src/agent1/static/search.js");

const CARD = {
  id: "c1",
  title: "Wire up the login form",
  text: "Add validation to the login form.",
  frame_id: "f1",
  tags: ["frontend", "auth"],
  system: "Billing Service",
};

// --- System property is searched ------------------------------------------
// A query that only appears in the system name must match.
assert.strictEqual(matchesQuery(CARD, "billing"), true);
// Full system name matches.
assert.strictEqual(matchesQuery(CARD, "Billing Service"), true);
// Case-insensitive and substring matching on the system name.
assert.strictEqual(matchesQuery(CARD, "ERVICE"), true);
assert.strictEqual(matchesQuery(CARD, "  billing  "), true);

// --- Existing behaviour still works ---------------------------------------
assert.strictEqual(matchesQuery(CARD, "login"), true); // title
assert.strictEqual(matchesQuery(CARD, "validation"), true); // text
assert.strictEqual(matchesQuery(CARD, "frontend"), true); // tag
assert.strictEqual(matchesQuery(CARD, "c1"), false); // id is not searched
assert.strictEqual(matchesQuery(CARD, "kubernetes"), false); // no match

// --- Empty query shows everything ------------------------------------------
assert.strictEqual(matchesQuery(CARD, ""), true);
assert.strictEqual(matchesQuery(CARD, "   "), true);
assert.strictEqual(matchesQuery(CARD, null), true);
assert.strictEqual(matchesQuery(CARD, undefined), true);

// --- Missing / partial properties must not throw ----------------------------
assert.strictEqual(matchesQuery({ title: "No system", text: "", tags: [] }, "x"), false);
assert.strictEqual(
  matchesQuery({ title: "Bare", text: "", tags: [], system: "" }, "bare"),
  true
);
assert.strictEqual(matchesQuery({ title: "No text", tags: [], system: null }, "no"), true);

// --- Date-range filtering (calendar controls) --------------------------------
// Fixtures built from the LOCAL Date constructor so the local calendar day
// is exact on any machine's timezone, and created/updated land on clearly
// distinct days.
const noon = (y, m, d) => new Date(y, m - 1, d, 12, 0).toISOString();

const DATED = {
  id: "d1",
  title: "Dated card",
  text: "",
  tags: [],
  frame_id: "f1",
  created_at: noon(2026, 3, 10),
  updated_at: noon(2026, 3, 20),
};

// localDateOf yields the calendar day in browser-local time; missing or
// unparseable values return null ("no date" per its documented contract).
assert.strictEqual(localDateOf(DATED.created_at), "2026-03-10");
assert.strictEqual(localDateOf("not a date"), null);
assert.strictEqual(localDateOf(null), null);
assert.strictEqual(localDateOf(undefined), null);

// Inclusive exact-day ranges (from == to) match on their own day.
assert.strictEqual(matchesDates(DATED, { createdFrom: "2026-03-10" }), true);
assert.strictEqual(matchesDates(DATED, { createdTo: "2026-03-10" }), true);
// Ranges that exclude the card's local day filter it out.
assert.strictEqual(matchesDates(DATED, { createdFrom: "2026-03-11" }), false);
assert.strictEqual(matchesDates(DATED, { createdTo: "2026-03-09" }), false);
// Updated ranges: inclusive at both ends; open-ended groups are no-ops.
assert.strictEqual(matchesDates(DATED, { updatedFrom: "2026-03-20" }), true);
assert.strictEqual(matchesDates(DATED, { updatedTo: "2026-03-19" }), false);
// An empty / whitespace-only range is a no-op (group inactive).
assert.strictEqual(matchesDates(DATED, {}), true);
assert.strictEqual(matchesDates(DATED, null), true);
assert.strictEqual(matchesDates(DATED, { createdFrom: "", createdTo: "  " }), true);
// Cards without dates are excluded when a date group is active.
assert.strictEqual(matchesTags(CARD, []), true); // sanity: empty tag filter = all
const UNDATED = { id: "u1", title: "Undated", text: "", tags: [], frame_id: "f1" };
assert.strictEqual(matchesDates(UNDATED, { createdFrom: "2026-03-01" }), false);

// --- Tag filter (OR within the selection) ------------------------------------
assert.strictEqual(matchesTags(CARD, ["frontend"]), true);
assert.strictEqual(matchesTags(CARD, ["auth"]), true);
// OR semantics: one selected tag present on the card is enough.
assert.strictEqual(matchesTags(CARD, ["auth", "kubernetes"]), true);
// None of the selected tags on the card: filtered out.
assert.strictEqual(matchesTags(CARD, ["kubernetes", "docs"]), false);
// Case-insensitive and whitespace-tolerant matching.
assert.strictEqual(matchesTags(CARD, ["  FRONTEND "]), true);
assert.strictEqual(matchesTags({ tags: ["Frontend"] }, ["frontend"]), true);

// --- Combined filtering (AND across groups) ----------------------------------
const COMBINED = {
  id: "k1",
  title: "Report generator",
  text: "",
  tags: ["reporting"],
  frame_id: "f1",
  created_at: noon(2026, 3, 10),
  updated_at: noon(2026, 3, 20),
};

// Query alone: match and non-match.
assert.strictEqual(filterCard(COMBINED, "report", {}), true);
assert.strictEqual(filterCard(COMBINED, "zzz", {}), false);
// Query passes but active date range excludes the card -> hidden (AND).
assert.strictEqual(
  filterCard(COMBINED, "report", { createdFrom: "2026-03-11" }),
  false
);
// Date range matches; tag selection excludes the card -> hidden.
assert.strictEqual(filterCard(COMBINED, "", { tags: ["kubernetes"] }), false);
// Everything active and matching.
assert.strictEqual(
  filterCard(COMBINED, "report", {
    createdFrom: "2026-03-10",
    updatedTo: "2026-03-20",
    tags: ["reporting", "kubernetes"],
  }),
  true
);

// --- filtersActive helper -----------------------------------------------------
assert.strictEqual(filtersActive({}), false);
assert.strictEqual(
  filtersActive({ createdFrom: "", updatedTo: "   ", tags: [] }),
  false
);
assert.strictEqual(filtersActive({ tags: ["a"] }), true);
assert.strictEqual(filtersActive({ updatedFrom: "2026-03-01" }), true);

console.log("test_search_filter: ALL PASS");
