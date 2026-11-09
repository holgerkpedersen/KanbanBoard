/*
 * Node test for the drag-and-drop insert-index math in static/dnd.js.
 * No browser/jsdom required: we build a tiny fake element that implements
 * only what computeInsertIndex (and cardAfterPoint) touch.
 */
const assert = require("assert");
const { computeInsertIndex, cardAfterPoint } = require("../src/agent1/static/dnd.js");

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
