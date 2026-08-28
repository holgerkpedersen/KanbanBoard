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

console.log("test_dnd_index: ALL PASS");
