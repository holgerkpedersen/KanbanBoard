/*
 * Node test for the drag-and-drop insert-index math in static/dnd.js.
 * No browser/jsdom required: we build a tiny fake element that implements
 * only what computeInsertIndex (and cardAfterPoint) touch.
 */
const assert = require("assert");
const { computeInsertIndex, cardAfterPoint } = require("../src/agent1/static/dnd.js");
const CardSearch = require("../src/agent1/static/search.js");

// Build a fake box containing given child specs:
//   { cls: "card" | "placeholder" | "empty-hint", dragging: bool }
function makeBox(children) {
  const nodes = children.map((c) => ({
    classList: {
      _set: new Set(c.cls.split(" ").filter(Boolean)),
      contains(x) {
        return this._set.has(x);
      },
    },
    getBoundingClientRect() {
      // each card 10px tall, stacked from top=0
      const i = nodes.indexOf(this);
      return { top: i * 10, height: 10 };
    },
  }));
  return {
    children: nodes,
    querySelectorAll() {
      return nodes;
    },
  };
}

// --- computeInsertIndex ----------------------------------------------------
// 1. placeholder at the very top -> insert at front (0)
{
  const box = makeBox([
    { cls: "placeholder" },
    { cls: "card" },
    { cls: "card" },
  ]);
  assert.strictEqual(computeInsertIndex(box, box.children[0]), 0);
}

// 2. placeholder in the middle (after one card) -> 1
{
  const box = makeBox([
    { cls: "card" },
    { cls: "placeholder" },
    { cls: "card" },
  ]);
  assert.strictEqual(computeInsertIndex(box, box.children[1]), 1);
}

// 3. placeholder at the end -> count of cards (2)
{
  const box = makeBox([
    { cls: "card" },
    { cls: "card" },
    { cls: "placeholder" },
    { cls: "empty-hint" },
  ]);
  assert.strictEqual(computeInsertIndex(box, box.children[2]), 2);
}

// 4. dragging card before placeholder must NOT be counted
{
  const box = makeBox([
    { cls: "card dragging" },
    { cls: "placeholder" },
    { cls: "card" },
  ]);
  assert.strictEqual(computeInsertIndex(box, box.children[1]), 0);
}

// 5. null placeholder -> append (count of cards)
assert.strictEqual(
  computeInsertIndex(makeBox([{ cls: "card" }, { cls: "card" }]), null),
  2
);

// --- cardAfterPoint --------------------------------------------------------
// y below the midpoint of the first card returns that card (insert before it)
{
  const box = makeBox([{ cls: "card" }, { cls: "card" }]);
  assert.strictEqual(cardAfterPoint(box, 4), box.children[0]);
}
// y below the last card's midpoint returns null (append at end)
{
  const box = makeBox([{ cls: "card" }, { cls: "card" }]);
  assert.strictEqual(cardAfterPoint(box, 25), null);
}

// --- filtered boards --------------------------------------------------------
// With a filter active, hidden cards are not rendered, so the DOM count is
// a count of VISIBLE cards. computeInsertIndex's mapper translates it into
// the frame's real (all-cards) index. These mirror the scenario:
//   all cards  = [A, B, C, D]  with B hidden by the tag filter
//   visible    = [A, C, D]
const mapVisibleToReal = (allIds, draggedId) => (visibleCount) => {
  // Same algorithm as main.js visibleCountToRealIndex, over ids.
  let real = 0;
  for (const id of allIds) {
    if (id === draggedId) continue;
    const isVisible = id !== "B"; // B is the hidden card
    if (visibleCount === 0 && isVisible) break; // gap sits before this card
    real++;
    if (isVisible) visibleCount--;
  }
  return real;
};
{
  const mapper = mapVisibleToReal(["A", "B", "C", "D"], "D");
  // placeholder before the first visible card -> real index 0
  {
    const box = makeBox([{ cls: "placeholder" }, { cls: "card" }, { cls: "card" }]);
    assert.strictEqual(computeInsertIndex(box, box.children[0], mapper), 0);
  }
  // placeholder after visible card A (count 1) -> real index 2 (A, B precede)
  {
    const box = makeBox([{ cls: "card" }, { cls: "placeholder" }, { cls: "card" }]);
    assert.strictEqual(computeInsertIndex(box, box.children[1], mapper), 2);
  }
  // placeholder after visible cards A, C (count 2) -> real index 3 (A, B, C precede)
  {
    const box = makeBox([{ cls: "card" }, { cls: "card" }, { cls: "placeholder" }]);
    assert.strictEqual(computeInsertIndex(box, box.children[2], mapper), 3);
  }
}

// --- real tag filter (CardSearch.filterCard, the production predicate) -----
// Regression for the reported bug: "the card can be moved even with the
// kanban tag filter" active. Cards [A,B,C,D] live in one frame; B and C
// carry the 'kanban' tag, the user filters to that tag, so only B and C
// are visible. Dragging C to the gap after B (visibleCount=1) must insert
// at the REAL index: A and B precede the drop point -> index 2. A naive
// DOM count would send 1 and the card would land in the wrong server slot.
{
  const cards = [
    { id: "A", title: "a", text: "", tags: [] },
    { id: "B", title: "b", text: "", tags: ["kanban"] },
    { id: "C", title: "c", text: "", tags: ["kanban"] },
    { id: "D", title: "d", text: "", tags: [] },
  ];
  const filters = { tags: ["kanban"], from: null, to: null };
  const isVisible = (card) => CardSearch.filterCard(card, "", filters, null);
  assert.ok(isVisible(cards[1]) && isVisible(cards[2]));
  assert.ok(!isVisible(cards[0]) && !isVisible(cards[3]));

  // Same algorithm as main.js visibleCountToRealIndex, over real cards.
  const mapper = (visibleCount) => {
    let real = 0;
    for (const card of cards) {
      if (visibleCount === 0 && isVisible(card)) break; // gap sits before this card
      if (card.id === "C") continue; // dragged card excluded
      real++;
      if (isVisible(card)) visibleCount--;
    }
    return real;
  };
  // gap before first visible card (B) -> real index 1 (A precedes)
  assert.strictEqual(mapper(0), 1);
  // gap after B (one visible card above) -> real index 2 (A, B precede)
  assert.strictEqual(mapper(1), 2);
  // gap at the very bottom of the filtered column -> real index 3:
  // the server removes the dragged card from its old slot before
  // inserting, and C's old slot (2) sits above the gap, so everything
  // below shifts up by one.
  assert.strictEqual(mapper(2), 3);

  // Wire it through computeInsertIndex: placeholder after visible card B.
  const box = makeBox([{ cls: "card" }, { cls: "placeholder" }]);
  assert.strictEqual(computeInsertIndex(box, box.children[1], mapper), 2);
}

// --- drop-zone semantics ----------------------------------------------------
// The placeholder is a gap BETWEEN cards. A pointer inside card Y's body
// belongs to the gap ABOVE Y (insert before Y), and once it crosses Y's
// vertical midpoint it belongs to the gap BELOW Y (insert after Y).
// Midpoint threshold, so dropping in the bottom half of a card inserts
// AFTER it - that is what makes "move down" land where users expect.
{
  const box = makeBox([{ cls: "card" }, { cls: "card" }]);
  // Pointer at y=6 (below card0's midpoint at 5) -> gap below card0,
  // which is before card1 in DOM order.
  assert.strictEqual(cardAfterPoint(box, 6), box.children[1]);
  // Pointer inside card1's top half (y=12 < midpoint 15) -> gap above
  // card1 = after card0.
  assert.strictEqual(cardAfterPoint(box, 12), box.children[1]);
}

console.log("test_dnd_index: ALL PASS");
