# Kanban Board

A single-page Kanban board built with **Flask** (Python 3.12) and **vanilla HTML/CSS/JS**
(no external JS/CSS frameworks). Data is stored in-memory, so it works out-of-the-box
with no database.

## Features

- **Frames (columns):** create, rename inline, delete (cascades to its cards), and
  drag-and-drop to reorder horizontally.
- **Cards (tasks):** create, edit (title / description / tags), delete, and drag-and-drop
  vertically within a frame or horizontally across frames.
- **Search:** a global search bar filters cards in real time by title, description, or tags.
- **Security:** CSP + hardening headers on every response, CSRF-style header required on all
  mutating requests, server-side input length validation, and XSS-safe DOM rendering
  (only `textContent` / `createElement`, never `innerHTML`).

## Run it

```bash
# from the repo root (C:\Dev\Kanban)
python -m pip install -r requirements.txt
python -m src.agent1.app
```

Then open <http://127.0.0.1:5000> in a browser.

> Note: `debug` is intentionally `False`. The Flask dev server is single-process; data
> resets when the process stops.

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
