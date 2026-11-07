# Kanban Board

A single-page Kanban board built with **Flask** (Python 3.12) and **vanilla
HTML/CSS/JS** (no external JS/CSS frameworks). Board state is **persisted to
disk** as JSON, so it survives restarts with no database.

## Features

- **Frames (columns):** create, rename inline, delete (cascades to its cards), and
  drag-and-drop to reorder horizontally.
- **Cards (tasks):** create, edit (title / description / tags / system field),
  delete, and drag-and-drop vertically within a frame or horizontally across frames.
- **Card ID:** every card shows its ID on the board and in a read-only field in the
  edit dialog, each with a Copy button (async Clipboard API, with a legacy fallback),
  so a card can be referenced unambiguously in tickets, commits and chat.
- **Search:** a global search bar filters cards in real time by title, description,
  tags, or system (the card's System property), and the match is case-insensitive.
- **Card timestamps:** every card carries a creation time and a last-update time;
  editing a card refreshes its update timestamp. The edit dialog shows both dates.
- **Filter bar:** calendar date-range pickers for each of the four boundaries
  (created / updated, from / to) plus one toggle chip per tag used on the board.
  All filters combine with AND. A range group matches when its inclusive day span
  (in browser-local time) contains the card's timestamp; open ends are ignored; an
  empty tag selection is inactive. A Clear button resets everything, and switching
  boards always starts unfiltered. Filtering is client-side only: hidden cards stay
  on the board untouched — no data changes.
- **Integrated cards:** a card that is linked to an Agent1 issue renders with a
  distinct accent colour and a 🔗 badge, and the "🔗 Integrated" toggle in the filter
  bar narrows the view to exactly those cards (combined with the other filters).
  Linkage is derived read-only from the shared `.card_id_map.json` — the feature
  adds no queue messages and changes no board schema.
- **Drag sounds:** subtle, fully synthesized (Web Audio) pick-up / move / frame-slide
  cues — no audio asset files. Sounds are **off by default**; click the 🔊/🔇 button in
  the top bar (or the first-run prompt) to enable, and your choice is remembered in
  `localStorage`.
- **Security:** CSP + hardening headers on every response, CSRF-style header required
  on all mutating requests, server-side input length validation, and XSS-safe DOM
  rendering (only `textContent` / `createElement`, never `innerHTML`).
- **Multi-board support:** a board catalog lives in the server's seed folder; pick a
  board from the switcher UI to load it into memory. Upload new boards via the API or
  browse them from the file picker, which pre-fills the "Open" form.
- **Theme switcher:** choose between light, dark, and high-contrast themes.

## Run it

```bash
# from the repo root (C:\Dev\Kanban)
python -m pip install -r requirements.txt
python -m src.agent1.app
```

Then open <http://127.0.0.1:5000> in a browser.

> Note: `debug` is intentionally `False`. You can override the on-disk location with
> the `KANBAN_WORKING_DIR` environment variable (default: `data/`). The active board
> file lives at `<working_dir>/board-<catalog_id>.json`.
> The `data/` directory is gitignored — it holds your local board state and is never
> committed.

## Data storage

- The running app persists frames and cards to a per-board working copy under the
  working directory (e.g. `data/board-default.json`).
- Tests use an in-memory store (no file is written), so they stay deterministic.

## API (JSON)

All mutating requests require the header `X-Requested-With: XMLHttpRequest`.

| Method | Path                  | Description                          |
|--------|-----------------------|--------------------------------------|
| GET    | `/api/frames`         | List frames (id, title, card_ids)    |
| POST   | `/api/frames`         | Create frame `{title}`               |
| GET    | `/api/frames/<id>`    | Read a frame                         |
| PUT    | `/api/frames/<id>`    | Rename frame `{title}`               |
| DELETE | `/api/frames/<id>`     | Delete frame (cascades to cards)     |
| POST   | `/api/frames/reorder` | Reorder frames `{order:[ids]}`       |
| GET    | `/api/frames/board`   | Frames with their cards (for render) |
| GET    | `/api/cards`          | List all cards                       |
| POST   | `/api/cards`          | Create card `{frame_id, title, text?, tags?, system?}` |
| GET    | `/api/cards/<id>`     | Read a card                          |
| PUT    | `/api/cards/<id>`     | Update card `{title,text,tags,system?}` |
| DELETE | `/api/cards/<id>`     | Delete a card                        |
| POST   | `/api/cards/<id>/move`| Move card `{frame_id,index?}`        |
| GET    | `/api/boards`         | List catalog boards + active pointer |
| POST   | `/api/boards/select`  | Open a catalog board `{catalog_id}`  |
| POST   | `/api/boards/reset`   | Re-seed working copy from catalog    |
| POST   | `/api/boards/upload`  | Ingest a board (JSON body or `.json` multipart file) into the catalog; `?activate=1` also selects it |

## Tests

```bash
python -m pytest tests
```
