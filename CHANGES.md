# Changelog

All notable changes to this board app are documented here, newest first.

## 2026-11-12 - Fix: pytest exit noise from a wedged pytest-current link

- **Gap.** Every tmp-using pytest run on the machine ended with
  Exception ignored in atexit callback + PermissionError: [WinError 5]
  on .../Temp/pytest-of-Holger/pytest-current - a wedged symlink
  (enumerable, but every open/stat/delete gets error 5: delete-pending
  after a crashed or privileged run). Exit code stayed 0, but the stderr
  noise makes run wrappers report failures and confuses the agent loop.
- **Root cause.** pytest registers cleanup_numbered_dir via texit;
  its cleanup_dead_symlinks(root) sweep does an unguarded
  left_dir.resolve().exists() that re-raises WinError 5 for such an
  entry instead of returning False. The link cannot be removed from
  userspace right now (rmdir/DeleteFileW/RemoveDirectoryW/rename all
  error 5); delete-pending names clear when the OS releases them, and a
  normal (unprivileged) shell cannot even recreate pytest-current
  (os.symlink -> WinError 1314), so this is a one-off artifact.
- **Fix: guard the sweep.** 	ests/conftest.py now arms
  _harden_pytest_temp_cleanup() at import: it wraps
  _pytest.pathlib.cleanup_dead_symlinks so an unstatable entry is
  skipped (OSError only; the wrap is idempotent and a no-op if pytest
  ever renames the function). Regression tests:
  	ests/test_pytest_temp_cleanup_guard.py (4).

## 2026-11-05 — Security: CSRF tokens now expire

- **Gap.** Tokens minted by the 2026-11-05 fix were immortal. The payload was
  pure randomness, so a token that once verified kept verifying forever: a
  stolen `kanban_csrf` cookie stayed usable indefinitely.
- **Fix: signed expiry inside the payload.** The payload is now
  `<entropy><issue-time><lifetime>`, all hex (32 + 16 + 8 chars), and all of
  it sits inside the HMAC region — so neither the issue time nor the lifetime
  can be stretched without breaking the signature, and expiry needs no
  server-side state. `csrf_token_is_current` refuses a token past its window;
  `issue_csrf_token` rotates one out on the next page load. The lifetime is
  baked in per token (read from `KANBAN_CSRF_MAX_AGE`, default 12 hours)
  rather than looked up at validation time, so changing the setting cannot
  reject a page's own still-valid token. Legacy tokens with no readable issue
  time count as expired and get rotated. No API changes; no frontend changes.
- **Tests.** New `tests/test_csrf_token_expiry.py` (13 tests) covers age
  reading, refusal of an old-but-correctly-signed token, tampering with the
  timestamp, legacy-token rotation, safe fallback for a malformed
  `KANBAN_CSRF_MAX_AGE`, and the production path end-to-end (`TESTING=False`):
  a stale token is refused with `"CSRF token expired"`, a fresh one still
  writes. Full suite is 212 passed.

## 2026-11-05 — Fix: every browser write was rejected with 403

- **Bug.** The CSRF guard shipped on 2026-11-03 broke the board in a real
  browser. `before_request` minted a fresh random token on *every* request,
  and validation compared the `X-CSRF-Token` header against that per-request
  value. A page carries the token from the GET that rendered it, so by the
  time the browser posted, the server expected a different number: every
  create/update/delete returned `403 {"error":"CSRF validation failed"}`.
  The suite never saw it — under `TESTING` a fixed token is configured, and
  no test built the app the way production does.
- **Fix: signed double-submit cookie.** The token is now
  `<payload>.<HMAC-SHA256(payload)>`, keyed by a server secret
  (`KANBAN_CSRF_SECRET`, or a random per-process secret). `issue_csrf_token`
  reuses the client's `kanban_csrf` cookie when its signature verifies and
  mints a new one otherwise, so the value is stable across reloads and tabs.
  Validation requires the header to match the cookie (the double-submit
  property) *and* the signature to verify, so an attacker who can set the
  cookie from outside cannot forge a passing pair. The cookie is written with
  `SameSite=Lax`, path `/`, and `Secure` when TLS is terminated upstream
  (`X-Forwarded-Proto`). No API changes; no frontend changes.
- **Tests.** New `tests/test_csrf_production_flow.py` builds the app with
  `TESTING` left off — the production path. It proves the page token is
  accepted, that a missing and a forged token are rejected, that the token
  survives consecutive writes, and that it is stable across requests. The
  three behavioural tests fail on the old code and pass on the new; the full
  suite is 199 passed.

## 2026-11-04 — Integrated cards get a colour and a filter

- **Integrated-card highlight.** A card linked to an Agent1 issue is now
  marked on the board: a left border and background tint (`.card-integrated`)
  plus a 🔗 badge, so it reads as "a card that belongs to the Agent1 system"
  at a glance. The colour adapts per theme (light, dark, high-contrast).
- **Filter.** The filter bar gains an "integrated" toggle chip. Turning it on
  keeps only cards whose id appears in `/api/cards/linked`; off (the default)
  is inactive and shows everything. It combines with search and the other
  filters under AND, and switching boards resets it.
- **API.** `GET /api/cards/linked` returns `{card_ids: [...]}` — the ids of
  cards with a mapping in this board's id map. It is read-only, needs no CSRF
  token, and reports an empty list when there is no id map.
- **Tests.** New `tests/test_linked_cards.py` (endpoint shape, empty-map case,
  no CSRF needed) and `tests/test_card_integrated_style.py` (CSS markers, badge
  markup, theme coverage). The JS filter helper already covers the integrated
  predicate in `tests/test_search_filter.js`.

## 2026-11-03 — CSRF tokens on every API write, and a quiet resolve no-op


- **Security: double-submit CSRF token.** Every mutating `/api/*` request
  (POST / PUT / DELETE) now requires an `X-CSRF-Token` header that matches
  the per-request token handed to the browser through the
  `<meta name="csrf-token">` tag. Requests without a matching token are
  rejected with 403, so a page on another origin can no longer mutate the
  board state of a user who happens to have the app open. Under `TESTING`
  the app uses a fixed token so test clients can send the header; the real
  guard still runs in tests, and requests that omit it keep failing with 403.
- **Tests.** New `tests/conftest.py` exports `TEST_CSRF_TOKEN` /
  `TEST_CSRF_HEADER`, and every test module that issues a mutating request
  merges the header in, so the suite exercises the guarded path rather than
  bypassing it.
- **Fix: untracked `issue_resolve` dead-lettered the queue.** The sync
  resolved an inbound resolve to a card id via `_resolve_card_id_for_message`,
  which reports where the id came from (`target_ref`, `map`, `locations` or
  `unmapped`). A resolve whose source this board never created can never gain
  a card by being retried, so it is now a quiet no-op keyed on that *origin*
  and is consumed as applied. Keying it on the message op instead swallowed
  every unresolved resolve, including a map entry pointing at a deleted card;
  that case stays loud and returns False. `test_kanban_sync.py` pins both
  branches plus the queue-level contract: an untracked resolve is applied once
  and produces no dead letter.

## 2026-11-02 — The card editor grows with its description field

- **Width follow.** Dragging the description field wider now widens the whole
  editor panel, so the other fields stay aligned inside it instead of the field
  poking out of the panel. The field is `resize: both` (it was `vertical`) and
  no longer carries a `max-width`, which would have capped the drag at the
  current panel width and left the feature doing nothing. `.card-modal-card`
  gains `flex: 0 0 auto` so its width is honoured inside the flex overlay
  instead of being pinned to its content-based minimum, plus `min-width: 0`;
  the existing `max-width: 94vw` still keeps the widened panel on screen.
- **Architecture.** The sizing maths lives in `static/modalwidth.js` as pure
  functions (`clamp`, `cardWidthForTextarea`, `planSizes`) so it is testable in
  Node without a browser; `main.js` only reads the DOM, writes the widths and
  owns the `ResizeObserver`. The observer watches the panel as well as the
  field, a `syncingCardWidth` flag stops the two writers from chasing each
  other, and each open resets to the stylesheet width so a width dragged for
  one card does not leak into the next.
- **Fix: the JS and CSS caps disagreed.** `planSizes` capped the panel at the
  full viewport while `style.css` caps it at `94vw`. Since `max-width` still
  applies to the inline width, the browser rendered a narrower panel than the
  field was sized for and the field overflowed — the exact bug the feature
  exists to prevent. Both now use the same 94% figure (`VIEWPORT_FRACTION`),
  and a test asserts the two numbers match.
- **Tests.** New `tests/test_modalwidth_js.py` drives the real shipped helper
  through Node (`tests/test_modalwidth_js.js`); `tests/test_card_modal.py` pins
  the CSS contract (`resize: both`, no `max-width`, the flex/min/max properties,
  full-width fields), the script load order before `main.js`, the `main.js`
  wiring, and the CSS/JS cap agreement.

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
