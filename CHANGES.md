# Changelog

All notable changes to this board app are documented here, newest first.

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
