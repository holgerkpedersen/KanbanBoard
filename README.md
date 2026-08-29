# Kanban Board

A single-page Kanban board built with **Flask** (Python 3.12) and **vanilla
HTML/CSS/JS** (no external JS/CSS frameworks). Board state is **persisted to
disk** as JSON, so it survives restarts with no database.

## Features

- **Frames (columns):** create, rename inline, delete (cascades to its cards), and
  drag-and-drop to reorder horizontally.
- **Cards (tasks):** create, edit (title / description / tags), delete, and drag-and-drop
  vertically within a frame or horizontally across frames.
- **Search:** a global search bar filters cards in real time by title, description, or tags.
- **Drag sounds:** subtle, fully synthesized (Web Audio) pick-up / move / frame-slide
  cues — no audio asset files. Sounds are **off by default**; click the 🔊/🔇 button in
  the top bar (or the first-run prompt) to enable, and your choice is remembered in
  `localStorage`.
- **Security:** CSP + hardening headers on every response, CSRF-style header required
  on all mutating requests, server-side input length validation, and XSS-safe DOM
  rendering (only `textContent` / `createElement`, never `innerHTML`).

## Run it

```bash
# from the repo root (C:\Dev\Kanban)
python -m pip install -r requirements.txt
python -m src.agent1.app
```

Then open <http://127.0.0.1:5000> in a browser.

> Note: `debug` is intentionally `False`. You can override the on-disk location with
> the `KANBAN_DATA_PATH` environment variable (default: `data/board.json`).
> The `data/` directory is gitignored — it holds your local board state and is never
> committed.

## Data storage

- The running app persists frames and cards to `data/board.json` (created on first run).
- Tests use an in-memory store (no file is written), so they stay deterministic.

## API (JSON)

All mutating requests require the header `X-Requested-With: XMLHttpRequest`.

| Method | Path                  | Description                          |
|--------|-----------------------|--------------------------------------|
| GET    | `/api/frames`         | List frames (id, title, card_ids)    |
| POST   | `/api/frames`         | Create frame `{title}`               |
| GET    | `/api/frames/<id>`    | Read a frame                         |
| PUT    | `/api/frames/<id>`    | Rename frame `{title}`               |
| DELETE | `/api/frames/<id>`    | Delete frame (cascades to cards)     |
| POST   | `/api/frames/reorder` | Reorder frames `{order:[ids]}`       |
| GET    | `/api/frames/board`   | Frames with their cards (for render) |
| GET    | `/api/cards`          | List all cards                       |
| POST   | `/api/cards`          | Create card `{frame_id,title,...}`   |
| GET    | `/api/cards/<id>`     | Read a card                          |
| PUT    | `/api/cards/<id>`     | Update card `{title,text,tags,...}`  |
| DELETE | `/api/cards/<id>`     | Delete a card                        |
| POST   | `/api/cards/<id>/move`| Move card `{frame_id,index?}`        |

## Tests

```bash
python -m pytest tests
```
