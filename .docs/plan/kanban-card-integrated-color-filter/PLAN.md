# Plan: Card colour + filter for cards integrated with Agent1

Issue location: `kanban:eb3471f1797d4b8f8f6622d6f2fbe689`
Agent1 issue id: `iss-general-kanban_eb3471f1797d4b8f8f6622d6f2fbe689`
Kanban card id:  `0b40fc2517a6406e8957cf533d6470ae` ("Kanban card coloring", board `agent1`)

## Goal (card body, verbatim intent)

When a Kanban card is already integrated with Agent1, render it in a colour
that differs from the default card colour, and provide a filter option that
selects only the integrated cards.

## Verified state of the code (what already exists)

Backend — `src/agent1/kanban_sync.py`:
- `linked_card_ids()` (line 178): returns every card id in the shared
  `.card_id_map.json` whose mapped value starts with `ISSUE_ID_PREFIX`.
  Missing/unreadable/malformed map degrades to an empty set.

Backend — `src/agent1/routes_cards.py`:
- `GET /api/cards/linked` (line 122): read-only, returns
  `{"card_ids": [...]}`; never 500s on a stale map; "nothing linked" when the
  sync module is unavailable.

Frontend:
- `static/main.js`: `api.getLinkedCards` (33), `state.linkedCardIds` (97),
  `refreshLinkedCards()` (~100) with non-fatal error handling, refresh on
  board switch (971), filter hook at 226, integrated badge/colour class in
  `renderCard` (307-315), toggle wiring + `renderIntegratedFilter()` +
  `resetFilters()` (~666-690).
- `static/search.js`: `matchesIntegration(card, filters, linkedIds)` (118)
  and `filterCard(..., linkedIds)` (149) — AND-combined with query/dates/tags.
- `templates/index.html`: `#filter-integrated` toggle button (line ~65-71),
  "Clear" resets it.

Gaps found by verification:
1. **`.card-integrated` has no CSS rule.** `style.css` contains no
   `card-integrated` selector, so the class added in `renderCard` changes
   nothing visually — the card keeps the default colour. This is the core
   unfulfilled part of the card.
2. **No test covers the feature.** `tests/` has no reference to `integrated`
   or `/api/cards/linked`.
3. Docs (README feature bullets, CHANGES.md) do not mention the integration
   colour/filter.

## Design decisions

1. Linkage stays derived from the shared `.card_id_map.json` — single source
   of truth, no schema change to board files, so the sync payload format is
   untouched (integration-safety requirement).
2. Colour must be theme-aware: use existing CSS custom properties
   (`--accent`, `--surface-*`, `--border`) rather than hard-coded hex values,
   consistent with `.sync-toggle:has(input:checked)` styling.
3. The badge is secondary to the colour; keep it as an affordance for
   accessibility (colour alone is not a sufficient signal).
4. Filter semantics unchanged: toggle on = only linked cards; off = all.

## Task list (dependency order)

- T1 [implementer] `static/style.css`: add `.card-integrated` (and
  `.card-integrated-badge`) rules — distinct border/left accent and background
  tint built from theme variables, plus a dark/light-theme-safe variant; the
  rule must be visibly distinguishable from the default card at normal zoom.
- T2 [implementer] Confirm `renderCard` badge element exists in the card
  template (`templates/index.html`, `.card-integrated-badge`); add it hidden
  if absent, so the JS path at main.js:309 never relies on a missing node.
- T3 [tests-first] `tests/test_linked_cards.py` (new): monkeypatch
  `kanban_sync.CARD_ID_MAP_PATH` to a tmp map with mixed entries
  (issue→card, card→issue reverse keys, non-issue pairs); assert
  `GET /api/cards/linked` returns exactly the linked card ids; missing map →
  `{"card_ids": []}`.
- T4 [tests-first] Extend `tests/test_search_filter.js` with
  `matchesIntegration` cases (toggle off = all pass, on = only linked, Set and
  array inputs, unknown id) — harness must print `ALL PASS`; runner
  `tests/test_search_js.py` already exists.
- T5 [tests-first] Add a CSS/template pin test in the style of
  `tests/test_card_modal.py`: assert `.card-integrated` rule exists in
  `static/style.css` and `.card-integrated-badge` exists in the template.
- T6 [documenter] README feature bullet + CHANGES.md entry; state that the
  feature is read-only w.r.t. the sync pipeline (no queue messages, no board
  schema change).

## Verification

Baseline observed while writing this plan (workdir must be `C:\Dev\Kanban`,
since the repo's pytest config resolves from its own rootdir):
`python -m pytest <abs>\tests\test_search_js.py <abs>\tests\test_card_modal.py -q`
→ 13 passed. The JS harness runner and the CSS/template pin style are both
confirmed working, so T4/T5 have a proven harness to extend.

- `node --check` on changed JS; run the runner `tests/test_search_js.py`.
- Targeted: `python -m pytest tests/test_linked_cards.py tests/test_card_modal.py`.
- Integration guard (user habit — Kanban board integration): full suite once
  at the end (`python -m pytest tests`, raised suite timeout to match test
  count), `--lf` for iterations.
- Manual check: open a board with sync enabled, confirm linked cards render in
  the distinct colour and the 🔗 Integrated toggle narrows the view.

## Out of scope

- No change to `.card_id_map.json` format, queue files, or Agent1 side.
- No per-card manual "mark as integrated" (linkage is derived, not authored).
