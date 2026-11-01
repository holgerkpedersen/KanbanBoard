# Plan: Kanban card Created/Updated dates + date-range and tag filtering

Status: in-progress (backend and pure JS filter helpers already landed; frontend
wiring, styles and tests remain)

## Goal (original request)

- Show Created and Updated dates when editing a Kanban card.
- Filter cards by created/updated time periods using calendar controls in the UI.
- Filter among the different tags; only show selected ones once a filter is set.

## Design decisions (already taken, verified in code)

1. Dates are ISO 8601 UTC strings on each card (`created_at`, `updated_at`);
   filtering compares inclusive calendar days in browser-local time.
2. A move counts as an update: the move endpoint refreshes `updated_at`.
3. Tag filter is OR-within-the-selection (card shows if it has >= 1 selected
   tag); empty selection = tag filter inactive; groups combine with AND.
4. Filtering stays client-side like the existing search: no new API endpoints.
5. Cards without dates are excluded when a date group is active ("unknown"
   cannot be proven to be in range).

## Task list (dependency order)

- T1 [done] models.py: `now_iso()` helper; Card gains `created_at`/`updated_at`,
  serialized in `to_dict()`.
- T2 [done] store.py `_load()`: tolerate legacy board files without dates
  (`""` = unknown).
- T3 [done] routes_cards.py: create stamps both timestamps (same value); PUT
  update refreshes `updated_at`; move refreshes `updated_at`; all responses
  carry the fields.
- T4 [done] static/search.js (pure module): `localDateOf`, `matchesDates`
  (inclusive, open-ended ranges), `matchesTags` (case-insensitive exact match,
  empty selection = inactive), `filtersActive`, combined `filterCard`.
- T5 [todo] static/main.js: define the missing `fmtDate` helper (currently
  referenced at openCardModal -> would throw ReferenceError); wire the four
  date inputs and Clear button into `state.filters`; render tag chips from the
  union of board tags into `#tag-filter`; reset filters on board switch.
- T6 [todo] static/style.css: theme-aware styles for `.filter-bar`,
  `.filter-group`, `.date-input`, `.tag-chip[aria-pressed]`, `.modal-dates`.
- T7 [tests-first, todo] tests/test_dates.py (Python API): create stamps equal
  dates; PUT refreshes updated_at only; move refreshes updated_at; board
  round-trip keeps dates; legacy file without dates loads with `""`.
- T8 [tests-first, todo] tests/test_search_filter.js: extend the Node harness
  with matchesDates (inclusive edges, open-ended, missing-date exclusion),
  matchesTags (OR + case-insensitivity + empty = all) and filterCard AND-combination.
- T9 [todo] docs: README feature bullets + CHANGES.md entry.

## Verification

- `node --check` on the changed JS files; run tests/test_search_js.py runner.
- Full suite once at the end: `python -m pytest tests`.
- Integration check (user habit): card payload gains two additive keys;
  verify kanban sync tests still pass (`tests/test_kanban_sync.py`,
  `tests/test_card_move_sync_payload.py`).
