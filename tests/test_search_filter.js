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
const { matchesQuery } = require("../src/agent1/static/search.js");

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

console.log("test_search_filter: ALL PASS");
