# Changelog

All notable changes to this board app are documented here, newest first.

## 2026-11-02 — Copyable card IDs + roomier card editor

- **Copyable card ID.** Every card now shows its ID on the board and, for
  existing cards, in a read-only field in the edit dialog — each with its own
  Copy button. Copying uses the async Clipboard API and falls back to a
  hidden-textarea `execCommand` path, so it works outside secure contexts
  (plain-HTTP LAN access). `CardId.copyText` is a pure helper in
  `static/cardid.js` that never raises: it reports success as a boolean, and
  both call sites then flash "Copied" / "Copy failed" feedback.
- **Fix: card ID row leaked onto "New card".** `.modal-id-field` sets
  `display: flex`, which overrides the `hidden` attribute, so a brand-new card
  showed an empty ID input and a dead Copy button. Added
  `.modal-id-field[hidden] { display: none; }` (same pattern already used by
  `.sfx-prompt[hidden]`).
- **Card editor sizing.** The editor inherited the 440px default panel with no
  `max-height` and no `overflow`, so on shorter viewports the form ran past the
  bottom of the screen and took the ID row and the Save/Cancel buttons with it.
  It now uses `.card-modal-card` (`width: min(640px, 94vw)`, `max-height: 88vh`,
  `overflow: auto`) and the description textarea is 8 rows and vertically
  resizable. The Copy button has a fixed min-width so its feedback label does
  not shift the row, and the ID input gets `min-width: 0` so it shrinks instead
  of overflowing.
- **Tests.** New `tests/test_card_modal.py` pins the copyable-ID markup, the
  `[hidden]` rule (verified to fail without the fix), the resized/scrolling
  editor, and the textarea height.

## 2026-11-01 — Card timestamps + date-range and tag filtering

Plan: `.docs/plans/2026-11-01-kard-filters-plan.md` (committed as `58ee11c`).

- **Card timestamps.** Cards now carry `created_at` (set at creation) and
  `updated_at` (refreshed on every content update), stored as ISO 8601 UTC
  strings in the board JSON. The card edit dialog shows both dates for
  existing cards (`#card-dates`).
- **Filter bar.** A new filter bar under the search field offers four
  calendar date-range pickers (created from/to, updated from/to) and one
  toggle chip per tag used on the board. All groups combine with AND; a
  range matches when its inclusive day span in browser-local time contains
  the card's timestamp; open ends are ignored; an empty tag selection is
  inactive. A Clear button resets everything; switching boards always
  starts unfiltered. Filtering is client-side only — hidden cards stay on
  the board untouched.
- **Architecture.** The matching rules moved to `static/search.js` as pure
  functions (`localDateOf`, `daySpan`, `dateRangesActive`, `filtersActive`,
  `matchesQuery`, `filterCard`) so they are unit-testable in Node without a
  browser; `main.js` only wires DOM events and calls `CardSearch.filterCard`.
- **Tests.** New Python suite `tests/test_dates.py` (server timestamps,
  full-journey store/API round-trip, JSON contract) and extended
  `tests/test_search_filter.js` (day-span boundaries, inclusive ranges,
  tag AND semantics, combined query+tag+date filtering). The board-wiring
  delegation test was updated for the new `filterCard` entry point.

## Earlier

- `fix(store): make the shared atomic write unique-named and retrying` (`e9eb508`)
- `fix(search): include card System property in Kanban board search` (`c1532ca`)
