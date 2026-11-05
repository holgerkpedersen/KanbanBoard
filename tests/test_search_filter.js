/*
 * Node test for the Kanban board search filter in static/search.js.
 *
 * Regression 1: the System property of a card was not searched, so a card
 * could only be found by title, text or tag. The search filter must also
 * match the card's system name.
 *
 * Regression 2 (T4): the "integrated with Agent1" toggle. When off every
 * card passes; when on only cards whose id is in the shared linked-id map
 * pass. linkedIds arrives as a Set from the app, but Arrays must work too.
 *
 * No browser/jsdom required: the helpers are pure and side-effect free.
 */
const assert = require("assert");
const {
  matchesQuery,
  localDateOf,
  matchesDates,
  matchesTags,
  matchesIntegration,
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

// --- Integration toggle (Agent1 ↔ Kanban linked-id map) ----------------------
const LINKED = {
  id: "i1",
  title: "Linked card",
  text: "",
  tags: [],
  frame_id: "f1",
  created_at: noon(2026, 3, 10),
  updated_at: noon(2026, 3, 20),
};
const UNLINKED = {
  id: "i2",
  title: "Unlinked card",
  text: "",
  tags: [],
  frame_id: "f1",
  created_at: noon(2026, 3, 10),
  updated_at: noon(2026, 3, 20),
};

// Toggle off (flag absent, false, 0, "") -> filter inactive, every card passes.
assert.strictEqual(matchesIntegration(LINKED, {}, new Set()), true);
assert.strictEqual(matchesIntegration(UNLINKED, {}, new Set()), true);
assert.strictEqual(matchesIntegration(LINKED, { integrated: false }, new Set()), true);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: false }, []), true);
assert.strictEqual(matchesIntegration(LINKED, { integrated: 0 }, null), true);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: "" }, undefined), true);
// No filters object at all -> inactive, passes.
assert.strictEqual(matchesIntegration(LINKED, null, new Set()), true);
assert.strictEqual(matchesIntegration(UNLINKED, undefined, []), true);

// Toggle on -> only cards whose id is in linkedIds pass.
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, new Set(["i1"])), true);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: true }, new Set(["i1"])), false);

// Set input works (the app's native shape).
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, new Set(["i1", "i2"])), true);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: true }, new Set(["i1", "i2"])), true);
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, new Set()), false);
// Array input works.
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, ["i1"]), true);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: true }, ["i1", "i2"]), true);
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, []), false);
// An empty / missing linked map with the toggle on hides every card.
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: true }, null), false);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: true }, undefined), false);

// A non-string id never matches, even when String(id) would collide.
assert.strictEqual(matchesIntegration({ id: 1 }, { integrated: true }, [1]), false);
assert.strictEqual(matchesIntegration({ title: "No id" }, { integrated: true }, new Set()), false);

// Unknown ids in the linked map are harmless (no throw, no side effects).
const BIG = new Set(["i1", "zz-does-not-exist", "ghost-42"]);
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, BIG), true);
assert.strictEqual(matchesIntegration(UNLINKED, { integrated: true }, BIG), false);
assert.strictEqual(matchesIntegration(LINKED, { integrated: true }, ["ghost", "i1", "ghost2"]), true);

// --- Integration AND-combines with the other groups --------------------------
// Toggle on + linked + everything else matches -> visible.
assert.strictEqual(filterCard(LINKED, "linked", { integrated: true }, ["i1"]), true);
// Query fails but integration passes -> hidden (AND).
assert.strictEqual(filterCard(LINKED, "zzz", { integrated: true }, ["i1"]), false);
// Integration fails (not linked) although the query matches -> hidden.
assert.strictEqual(filterCard(UNLINKED, "unlinked", { integrated: true }, ["i1"]), false);
// Toggle on + linked + active date range excludes it -> hidden.
assert.strictEqual(
  filterCard(LINKED, "", { integrated: true, createdFrom: "2026-03-11" }, new Set(["i1"])),
  false
);
// Toggle on + linked + tag selection excludes it -> hidden.
assert.strictEqual(
  filterCard(LINKED, "", { integrated: true, tags: ["kubernetes"] }, new Set(["i1"])),
  false
);
// All groups active and matching (query + date + tags + integration).
assert.strictEqual(
  filterCard(LINKED, "linked", { integrated: true, createdFrom: "2026-03-10", tags: [] }, new Set(["i1"])),
  true
);
// Toggle off with the same filters: linked status is irrelevant -> passes.
assert.strictEqual(filterCard(UNLINKED, "unlinked", { integrated: false }, []), true);

// --- filtersActive helper -----------------------------------------------------
assert.strictEqual(filtersActive({}), false);
assert.strictEqual(
  filtersActive({ createdFrom: "", updatedTo: "   ", tags: [] }),
  false
);
assert.strictEqual(filtersActive({ tags: ["a"] }), true);
assert.strictEqual(filtersActive({ updatedFrom: "2026-03-01" }), true);
// The integration toggle alone makes the filters active.
assert.strictEqual(filtersActive({ integrated: true }), true);
assert.strictEqual(filtersActive({ integrated: false }), false);

console.log("test_search_filter: ALL PASS");
