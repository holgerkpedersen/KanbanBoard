/*
 * Node test for the card-ID copy helper in static/cardid.js.
 *
 * No browser/jsdom required: the helper only touches `navigator.clipboard`
 * and `document.createElement`/`execCommand`, so we pass tiny fakes that
 * implement exactly those surfaces. This runs the REAL shipped code.
 *
 * copyText is async, so every case is awaited inside one async main(); an
 * un-awaited assertion would let the process exit before it ran.
 */
const assert = require("assert");
const { normalize, legacyCopy, copyText } = require("../src/agent1/static/cardid.js");

const ID = "0123456789abcdef0123456789abcdef";

// A fake document that records what legacyCopy did to it.
function makeDoc({ execResult = true, throws = false } = {}) {
  const seen = { appended: 0, removed: 0, selected: 0, readonly: false };
  const area = {
    style: {},
    setAttribute(name) {
      if (name === "readonly") seen.readonly = true;
    },
    select() {
      seen.selected += 1;
    },
    value: "",
  };
  const doc = {
    createElement(tag) {
      assert.strictEqual(tag, "textarea");
      return area;
    },
    body: {
      appendChild() {
        seen.appended += 1;
      },
      removeChild() {
        seen.removed += 1;
      },
    },
    execCommand(cmd) {
      assert.strictEqual(cmd, "copy");
      if (throws) throw new Error("boom");
      return execResult;
    },
  };
  return { doc, seen, area };
}

async function main() {
  // --- normalize -----------------------------------------------------------
  assert.strictEqual(normalize(ID), ID);
  // Surrounding whitespace from a copy/paste is trimmed.
  assert.strictEqual(normalize("  " + ID + "\n"), ID);
  // Nothing to copy -> empty string, so callers can bail out.
  assert.strictEqual(normalize(""), "");
  assert.strictEqual(normalize("   "), "");
  assert.strictEqual(normalize(null), "");
  assert.strictEqual(normalize(undefined), "");
  assert.strictEqual(normalize(42), "");

  // --- copyText: Clipboard API path ---------------------------------------
  {
    const written = [];
    const nav = {
      clipboard: {
        writeText: async (t) => {
          written.push(t);
        },
      },
    };
    assert.strictEqual(await copyText(ID, nav, undefined), true);
    assert.deepStrictEqual(written, [ID]);
  }

  // --- copyText: empty input never touches the clipboard ------------------
  {
    let called = 0;
    const nav = {
      clipboard: {
        writeText: async () => {
          called += 1;
        },
      },
    };
    assert.strictEqual(await copyText("", nav, undefined), false);
    assert.strictEqual(called, 0, "must not copy an empty string");
  }

  // --- copyText: clipboard rejected -> legacy fallback --------------------
  {
    const { doc, seen, area } = makeDoc();
    const nav = {
      clipboard: {
        writeText: async () => {
          throw new Error("denied");
        },
      },
    };
    assert.strictEqual(await copyText(" " + ID + " ", nav, doc), true);
    assert.strictEqual(area.value, ID, "the trimmed ID is what gets copied");
    assert.strictEqual(seen.readonly, true);
    assert.strictEqual(seen.selected, 1);
    assert.strictEqual(seen.appended, 1);
    assert.strictEqual(seen.removed, 1, "the scratch textarea must be removed");
  }

  // --- copyText: no clipboard API at all -> legacy fallback ---------------
  {
    const { doc, seen } = makeDoc();
    assert.strictEqual(await copyText(ID, {}, doc), true);
    assert.strictEqual(seen.removed, 1);
  }

  // --- copyText: everything unavailable -> false, never throws ------------
  assert.strictEqual(await copyText(ID, {}, undefined), false);

  // --- legacyCopy: reports execCommand's verdict and cleans up ------------
  {
    const { doc, seen, area } = makeDoc({ execResult: false });
    assert.strictEqual(legacyCopy(doc, ID), false, "execCommand=false means not copied");
    assert.strictEqual(area.value, ID);
    assert.strictEqual(seen.selected, 1);
    assert.strictEqual(seen.appended, 1);
    assert.strictEqual(seen.removed, 1, "the scratch textarea must be removed");
  }

  // --- legacyCopy: a throwing execCommand is a failure, not a crash -------
  {
    const { doc, seen } = makeDoc({ throws: true });
    assert.strictEqual(legacyCopy(doc, ID), false);
    assert.strictEqual(seen.removed, 1, "cleanup must still happen on a throw");
  }

  console.log("ALL PASS");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
