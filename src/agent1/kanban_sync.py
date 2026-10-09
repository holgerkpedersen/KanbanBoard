"""Bidirectional Kanban ↔ Agent1 sync — disk-backed JSONL enqueueing (Kanban side).

When a change occurs in Kanban (card created, updated, moved), it is immediately
appended to the target Agent1 queue on disk. A background reader thread processes
queued messages when the Flask app runs.

Queue layout (one directory SHARED with Agent1, so both processes see the same
files)::

    <KANBAN_WORKING_DIR>/
        queue-agent1-to-kanban/   ← Agent1 writes here (Kanban reads)
        │   ├── messages.jsonl
        │   └── offset.txt        ← reader cursor only (see enqueue())
        queue-kanban-to-agent1/   ← Kanban writes here (Agent1 reads)
            ├── messages.jsonl
            └── offset.txt

``KANBAN_WORKING_DIR`` must resolve to the same path on both sides; Agent1
defaults to this checkout's ``data/`` directory.

Message format (one JSON object per line)::

    {seq, ts, op, source_id, target_ref, payload, retry_count, processed}
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

# --- Queue paths ---------------------------------------------------------------

KANBAN_WORKING_DIR = os.environ.get("KANBAN_WORKING_DIR", "data")
DATA_DIR = Path(KANBAN_WORKING_DIR)

# Names describe the DIRECTION of travel, not the side that owns the file.
# The original "in"/"out" naming was a defect in itself: "queue-kanban-out"
# meant Agent1→Kanban on Agent1's side but Kanban→Agent1 here, so even with a
# shared root the two sides read and wrote different queues.
QUEUE_AGENT1_TO_KANBAN = DATA_DIR / "queue-agent1-to-kanban"   # Agent1 writes, we read
QUEUE_KANBAN_TO_AGENT1 = DATA_DIR / "queue-kanban-to-agent1"   # we write, Agent1 reads

# Must match Agent1's CARD_ID_MAP_PATH: one shared issue↔card mapping.
CARD_ID_MAP_PATH = DATA_DIR / ".card_id_map.json"

_OFFSET_LOCKS: Dict[str, threading.Lock] = {}
_offset_locks_lock = threading.Lock()

# A message that fails this many times is moved to <queue_dir>/dead_letters/.
# Patchable at module level so tests can redirect the dead-letter directory.
MAX_RETRIES = 3
DEAD_LETTER_DIR: Path | None = None


def _seq_counter(queue_dir: Path) -> int:
    """Return next monotonically increasing sequence number for this queue."""
    # The writer's sequence counter lives in its OWN file (seq.txt).  It must
    # NOT be offset.txt: that file is the *reader's* cursor, and the reader is a
    # different process.  Sharing one file between "next seq" and "lines
    # consumed" meant every enqueue advanced the counterpart's cursor past the
    # line it had just written, so no message was ever delivered.
    return _read_int(queue_dir / "seq.txt") + 1


def _read_int(path: Path) -> int:
    """Read an integer from a counter file, tolerating a missing/garbled file."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def _seq_lock_for(queue_dir: Path) -> threading.Lock:
    """Get a per-queue lock for offset updates."""
    key = str(queue_dir)
    with _offset_locks_lock:
        if key not in _OFFSET_LOCKS:
            _OFFSET_LOCKS[key] = threading.Lock()
        return _OFFSET_LOCKS[key]


def enqueue(message: dict[str, Any], queue_dir: Path | None = None) -> int:
    """Append a single message to the target queue's messages.jsonl.

    Returns the sequence number assigned to this message.
    """
    if queue_dir is None:
        queue_dir = QUEUE_KANBAN_TO_AGENT1

    queue_dir.mkdir(parents=True, exist_ok=True)
    msg_file = queue_dir / "messages.jsonl"

    message["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    message.setdefault("retry_count", 0)
    message.setdefault("processed", False)

    lock = _seq_lock_for(queue_dir)
    with lock:
        seq = _seq_counter(queue_dir)
        message["seq"] = seq

        line = json.dumps(message, ensure_ascii=False) + "\n"
        with open(msg_file, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        # Only the writer's own counter moves here; offset.txt is left to the
        # reader (read_queue/advance_offset) so a fresh message stays visible.
        (queue_dir / "seq.txt").write_text(str(seq), encoding="utf-8")

    logger.info("Enqueued [%s] seq=%d to %s", message.get("op"), seq, msg_file)
    return seq


# --- Issue ↔ Card ID mapping ---------------------------------------------------

def _load_id_map() -> dict[str, str]:
    """Load the issue↔card bidirectional mapping shared with Agent1.

    The filename must match Agent1's ``CARD_ID_MAP_PATH`` (``.card_id_map.json``)
    and both sides must resolve the same ``DATA_DIR`` — otherwise each side
    keeps a private mapping and issue_update/issue_resolve never match a card.
    """
    id_map_path = CARD_ID_MAP_PATH
    if id_map_path.exists():
        try:
            data = json.loads(id_map_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_id_map(mapping: dict[str, str]) -> None:
    """Save the issue↔card bidirectional mapping (shared with Agent1)."""
    id_map_path = CARD_ID_MAP_PATH
    tmp = id_map_path.with_suffix(id_map_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        tmp.replace(id_map_path)
    except OSError:
        shutil.copy2(str(tmp), str(id_map_path))


def link_ids(source_id: str, target_id: str) -> None:
    """Record a bidirectional ID pair and save it."""
    mapping = _load_id_map()
    mapping[source_id] = target_id
    mapping[target_id] = source_id  # reverse lookup
    _save_id_map(mapping)


def resolve_id(source_id: str) -> str | None:
    """Look up the counterpart ID for a known source ID."""
    mapping = _load_id_map()
    return mapping.get(source_id)


# --- Issue → Card conversion ---------------------------------------------------

# Issue status → board frame *title*.  These are titles, not ids: a board is
# stored keyed by opaque frame ids, so every lookup has to go through
# ``_resolve_frame_id``.  The canonical seed boards start with "Ideas".
ISSUE_TO_FRAME = {
    "open": "Ideas",
    "planned": "Planned",
    "in-progress": "On-going",
    "reviewing": "Quality & Assurance",
    "resolved": "Finished",
    "wontfix": "Finished",
}

# Titles that mean the same frame on other boards, so the sync still lands
# somewhere sensible when a board was seeded with different labels.
FRAME_TITLE_ALIASES: dict[str, tuple[str, ...]] = {
    "Ideas": ("Ideas", "Issues", "Inbox", "Backlog"),
    "Planned": ("Planned", "To Do", "Todo"),
    "On-going": ("On-going", "Ongoing", "In Progress", "In-Progress"),
    "Quality & Assurance": ("Quality & Assurance", "Review", "Reviewing", "QA"),
    "Finished": ("Finished", "Done", "Resolved", "Complete"),
}


def _resolve_frame_id(store: Any, title: str) -> str | None:
    """Resolve a frame *title* (or a raw frame id) to the store's real frame id.

    Cards are stored against frame ids, so a hard-coded title can never be
    used directly.  Tries the exact title, then known aliases, and finally
    falls back to the board's first frame.
    """
    frames = store.get_all_frames()
    if not frames:
        return None
    if any(f.id == title for f in frames):  # caller already passed an id
        return title
    for candidate in FRAME_TITLE_ALIASES.get(title, (title,)):
        for frame in frames:
            if frame.title.strip().lower() == candidate.strip().lower():
                return frame.id
    return frames[0].id


def _status_of(issue: dict[str, Any]) -> str:
    """Extract an issue status from a payload, tolerating tag-only payloads."""
    status = issue.get("status")
    if isinstance(status, str) and status:
        return status
    for tag in issue.get("tags", []) or []:
        if isinstance(tag, str) and tag.startswith("[status:"):
            return tag[len("[status:"):-1]
    return "open"


def issue_to_card_data(issue: dict[str, Any]) -> dict[str, Any]:
    """Convert an Agent1 issue dict into Kanban card payload data."""
    # Combine evidence and suggested_approach with a delimiter
    parts: list[str] = []
    if issue.get("evidence"):
        parts.append(issue["evidence"])
    if issue.get("suggested_approach"):
        parts.append(f"Approach:\n{issue['suggested_approach']}")

    text = "\n\n---\n".join(parts)

    # Build tags from category and severity
    tags: list[str] = [
        f"[{issue.get('category', 'general')}]",
        f"[severity:{issue.get('severity', 'low')}]",
        f"[status:{issue.get('status', 'open')}]",
    ]

    return {
        "title": issue["title"],
        "text": text,
        "tags": tags,
        "system": "harnessfix",
    }


# --- Card → Issue conversion ---------------------------------------------------

def card_to_issue_data(card: dict[str, Any]) -> dict[str, Any]:
    """Convert a Kanban card dict into Agent1 issue payload data."""
    text = card.get("text", "")
    evidence = ""
    suggested_approach = ""
    if "\n\n---\n" in text:
        evidence, _, suggested_approach = text.partition("\n\n---\n")
        suggested_approach = suggested_approach.lstrip()

    category = "general"
    severity = "low"
    status = "open"
    for tag in card.get("tags", []):
        if tag.startswith("[severity:"):
            severity = tag[len("[severity:"):-1]
        elif tag.startswith("[status:"):
            status = tag[len("[status:"):-1]
        elif tag.startswith("[") and tag.endswith("]") and ":" not in tag:
            category = tag[1:-1]

    return {
        "title": card["title"],
        "category": category,
        "severity": severity,
        "evidence": evidence,
        "suggested_approach": suggested_approach,
        "status": status,
        "tags": card.get("tags", []),
    }


# --- Apply functions (Kanban processes Agent1 inbound messages) ----------------

def _payload_of(msg: dict[str, Any], *, require_title: bool = False) -> dict[str, Any] | None:
    """Extract the inner issue payload, or ``None`` when the message is malformed.

    A queue file is external input: a truncated line or a bad producer can
    yield a non-dict payload or a missing title.  Returning ``None`` lets the
    caller reject the message as a failure (which routes it to the
    dead-letter directory) instead of raising out of the processor.
    """
    payload = msg.get("payload", msg)
    if not isinstance(payload, dict):
        return None
    if require_title and not str(payload.get("title", "")).strip():
        return None
    return payload


def _apply_issue_create(msg: dict[str, Any], store: Any) -> bool:
    """Create a Kanban card from an Agent1 issue CREATE message.

    ``msg`` is the full envelope (``{op, source_id, payload}``), not the
    inner payload: the issue id that we have to remember for later
    update/resolve messages lives on the envelope.
    """
    issue = _payload_of(msg, require_title=True)
    if issue is None:
        logger.warning("issue_create: malformed payload, ignoring: %r", msg.get("payload"))
        return False
    data = issue_to_card_data(issue)

    # Idempotency guard.  The id map is bidirectional, and Agent1's card→issue
    # apply path calls ``make_issue()``, which enqueues an ``issue_create``
    # straight back here.  Without this check that echo would add a *second*
    # card for the same issue; the second card maps to the same issue, so the
    # next poll would echo again and the board would grow without bound.
    source_id = msg.get("source_id", "") or issue.get("id", "")
    if source_id:
        existing_card_id = resolve_id(source_id)
        if existing_card_id and store.get_card(existing_card_id) is not None:
            logger.info(
                "issue_create: card %s already exists for issue %s, ignoring echo",
                existing_card_id,
                source_id,
            )
            return True

    status = _status_of(issue)
    target_frame_id = _resolve_frame_id(store, ISSUE_TO_FRAME.get(status, "Ideas"))
    if target_frame_id is None:
        logger.warning("issue_create: board has no frames, cannot place card")
        return False

    # Build card with proper tags
    tags = [t for t in data["tags"] if not t.startswith("[status:")]  # remove old status tag
    from .models import Card
    import uuid
    card = Card(
        id=uuid.uuid4().hex,
        title=data["title"],
        text=data["text"],
        frame_id=target_frame_id,
        tags=tags,
        system="harnessfix",
    )

    store.add_card(card, target_frame_id)

    # Record ID mapping: issue_id → card_id, so a later issue_update /
    # issue_resolve carrying only the issue id can find this card.
    source_id = msg.get("source_id", "") or issue.get("id", "")
    if source_id:
        link_ids(source_id, card.id)

    logger.info("Applied issue_create: card %s for issue %s", card.id, source_id)
    return True


def _apply_issue_update(msg: dict[str, Any], store: Any) -> bool:
    """Update a Kanban card from an Agent1 issue UPDATE message.

    ``msg`` is the full envelope; the issue id used for lookup is on the
    envelope while the field values live in ``msg["payload"]``.
    """
    source_id = msg.get("source_id", "")
    target_ref = msg.get("target_ref", "")

    if not source_id and not target_ref:
        return False

    # Look up the card by either the issue's source_id (via mapping) or direct target_ref
    card_id = None
    if target_ref:
        card_id = target_ref
    elif source_id:
        card_id = resolve_id(source_id)

    if not card_id:
        logger.warning("issue_update: no matching card for source=%s, target=%s", source_id, target_ref)
        return False

    card = store.get_card(card_id)
    if card is None:
        logger.warning("issue_update: card %s not found", card_id)
        return False

    issue = _payload_of(msg)
    if issue is None:
        logger.warning("issue_update: malformed payload for source=%s, ignoring", source_id)
        return False
    data = issue_to_card_data(issue)
    updated = False

    if "title" in data and data["title"]:
        card.title = data["title"]
        updated = True
    if "text" in data:
        card.text = data["text"]
        updated = True

    new_status = _status_of(issue)
    if data.get("tags"):
        # Remove old status tag, add new one
        tags = [t for t in data["tags"] if not t.startswith("[status:")]
        if new_status:
            tags.append(f"[status:{new_status}]")
        card.tags = tags
        updated = True

    if updated:
        store.update_card(card)
        logger.info("Applied issue_update to card %s", card_id)

    return True


def _apply_issue_resolve(msg: dict[str, Any], store: Any) -> bool:
    """Resolve/wontfix an Agent1 issue → move the Kanban card to Finished."""
    source_id = msg.get("source_id", "")
    target_ref = msg.get("target_ref", "")

    if not source_id and not target_ref:
        return False

    card_id = None
    if target_ref:
        card_id = target_ref
    elif source_id:
        card_id = resolve_id(source_id)

    if not card_id:
        logger.warning("issue_resolve: no matching card for source=%s", source_id)
        return False

    card = store.get_card(card_id)
    if card is None:
        logger.warning("issue_resolve: card %s not found", card_id)
        return False

    # Move to Finished. ``get_frame``/``move_card`` take a frame *id*, so the
    # title has to be resolved first — passing "Finished" directly silently
    # did nothing because no frame id equals that string.
    finished_frame_id = _resolve_frame_id(store, "Finished")
    if finished_frame_id is None:
        logger.warning("issue_resolve: board has no frames, cannot move card %s", card_id)
        return False

    if not store.move_card(card_id, finished_frame_id):
        logger.warning("issue_resolve: could not move card %s to Finished", card_id)
        return False

    # Update status tag
    tags = [t for t in card.tags if not t.startswith("[status:")]
    disposition = msg.get("payload", {}).get("disposition", "resolved")
    tags.append(f"[status:{disposition}]")
    card.tags = tags
    store.update_card(card)

    logger.info("Applied issue_resolve: moved card %s to Finished", card_id)
    return True


# --- Inbound processor ---------------------------------------------------------

def _process_single_message(msg: dict[str, Any], store: Any) -> bool:
    """Apply a single inbound message. Returns True on success."""
    op = msg.get("op", "")
    payload = msg.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

    if op == "issue_create":
        return _apply_issue_create(msg, store)
    elif op == "issue_update":
        return _apply_issue_update(msg, store)
    elif op == "issue_resolve":
        return _apply_issue_resolve(msg, store)
    else:
        logger.warning("Unknown inbound op: %s", op)
        return False


def _quarantine_malformed(
    queue_dir: Path, idx: int, line: str, exc: Exception
) -> None:
    """Preserve an unparseable queue line instead of losing it to the log.

    A line that is not JSON can never be retried into success, so re-reading it
    forever is pointless — but silently dropping it destroys the only copy of
    whatever the sender wrote.  Quarantining satisfies both: the offset moves
    past the bad line and the raw bytes survive for inspection.
    """
    try:
        dl_dir = _dead_letter_dir(queue_dir)
        dl_dir.mkdir(parents=True, exist_ok=True)
        dl_file = dl_dir / f"malformed_line_{idx}.jsonl"
        dl_file.write_text(
            json.dumps(
                {"seq": idx + 1, "error": str(exc), "raw": line},
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        logger.error(
            "Quarantined malformed JSON from queue line %d to %s: %s",
            idx, dl_file, exc,
        )
    except OSError as write_exc:  # noqa: BLE001 — never break the read loop
        logger.error(
            "Could not quarantine malformed queue line %d (%s); line dropped",
            idx, write_exc,
        )


def read_queue(queue_dir: Path) -> tuple[list[dict[str, Any]], int]:
    """Read new lines from the queue's messages.jsonl starting at offset.txt.

    Returns (new_messages, next_offset).  A line with invalid JSON can never
    be retried into success, so it is quarantined to the dead-letter
    directory and the offset advances past it.  Re-reading it forever was
    the old behaviour: one bad byte re-logged an error on every poll for
    the life of the queue, and the bytes were lost to nothing but the log.
    """
    queue_dir = queue_dir.resolve()
    msg_file = queue_dir / "messages.jsonl"
    if not msg_file.exists():
        return [], 0

    try:
        current_offset = int(
            (queue_dir / "offset.txt").read_text(encoding="utf-8").strip()
        )
    except (ValueError, OSError):
        current_offset = 0

    lines = []
    try:
        content = msg_file.read_text(encoding="utf-8")
        lines = content.splitlines()
    except OSError as exc:
        logger.error("Failed to read queue file %s: %s", msg_file, exc)
        return [], current_offset

    new_messages: list[dict[str, Any]] = []
    next_offset = current_offset

    for idx, line in enumerate(lines):
        if idx < current_offset:
            continue
        next_offset = idx + 1

        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
            new_messages.append(msg)
        except json.JSONDecodeError as exc:
            _quarantine_malformed(queue_dir, idx, line, exc)

    return new_messages, next_offset


def advance_offset(queue_dir: Path, offset: int) -> None:
    """Update the queue's offset.txt to mark messages as processed.

    Guarded by the per-queue lock: the offset is shared state and an
    unguarded write can lose an update when the poller thread and a manual
    ``--process-in`` run overlap.
    """
    queue_dir = queue_dir.resolve()
    with _seq_lock_for(queue_dir):
        (queue_dir / "offset.txt").write_text(str(offset), encoding="utf-8")


def _dead_letter_dir(queue_dir: Path) -> Path:
    """Directory that quarantines permanently failing messages.

    Defaults to ``<queue_dir>/dead_letters`` but honours the module-level
    ``DEAD_LETTER_DIR`` override (tests redirect it into tmp_path).
    """
    return DEAD_LETTER_DIR if DEAD_LETTER_DIR is not None else queue_dir / "dead_letters"


def _record_failure(queue_dir: Path, msg: dict[str, Any]) -> None:
    """Persist an incremented retry_count so retries can actually accumulate.

    The queue is an append-only JSONL log, so a failed message is rewritten at
    the tail with ``retry_count + 1``; that keeps the failure durable across
    process restarts, which is what makes dead-lettering reachable at all.
    """
    retry_count = int(msg.get("retry_count", 0)) + 1
    msg["retry_count"] = retry_count

    if retry_count >= MAX_RETRIES:
        dl_dir = _dead_letter_dir(queue_dir)
        dl_dir.mkdir(parents=True, exist_ok=True)
        dl_file = dl_dir / f"seq_{msg.get('seq', '?')}.jsonl"
        dl_file.write_text(
            json.dumps(msg, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.error(
            "Dead-lettered seq=%s after %d attempts to %s",
            msg.get("seq"), retry_count, dl_file,
        )
        return

    msg["processed"] = False
    with open(queue_dir / "messages.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def process_inbound(store: Any, queue_dir: Path | None = None) -> int:
    """Read and apply all pending inbound messages. Returns count applied.

    Every message consumed advances the offset — including failures, which are
    re-queued at the tail with a higher ``retry_count``. Without that, a single
    poison message would pin the offset and stall the whole queue forever.
    """
    if queue_dir is None:
        queue_dir = QUEUE_AGENT1_TO_KANBAN

    new_messages, next_offset = read_queue(queue_dir)
    applied = 0

    for msg in new_messages:
        op = msg.get("op", "")

        if msg.get("processed"):
            continue

        try:
            success = _process_single_message(msg, store)
            if not success:
                raise RuntimeError(f"_process_{op} returned False")
            applied += 1
        except Exception:
            logger.exception("Failed to apply %s message: %r", op, msg)
            _record_failure(queue_dir, msg)

    if new_messages:
        advance_offset(queue_dir, next_offset)
        logger.info(
            "Processed %d/%d messages from %s (offset now %d)",
            applied, len(new_messages), queue_dir, next_offset,
        )

    return applied


# --- Background processor thread -----------------------------------------------

class QueueProcessor:
    """Background thread that polls an inbound queue and processes messages."""

    def __init__(self, app: Any, queue_dir: Path | None = None, poll_interval: float = 5.0):
        if queue_dir is None:
            queue_dir = QUEUE_AGENT1_TO_KANBAN
        self.app = app
        self.queue_dir = queue_dir
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        # Set while the per-board gate is holding messages back, so we log
        # the condition once instead of once per poll.
        self._gate_logged = False

    def run_once(self) -> int:
        """Process one batch of pending messages. Returns count applied."""
        try:
            holder = self.app.config.get("LIVE_STORE_HOLDER")
            if holder is None or holder["store"] is None:
                return 0
            store = holder["store"]
            # Per-board opt-in gate: while the active board has not allowed
            # agent1 sync we HOLD messages in the queue (no offset advance)
            # instead of applying them to a board that opted out. They get
            # picked up once a synced board becomes active; duplicate
            # protection for re-applied issue_create lives in _apply_issue_create.
            allows = getattr(store, "sync_allowed", None)
            if callable(allows) and not allows("agent1"):
                if not self._gate_logged:
                    logger.info(
                        "Holding inbound queue %s: active board has agent1 sync disabled",
                        self.queue_dir,
                    )
                    self._gate_logged = True
                return 0
            self._gate_logged = False
            return process_inbound(store, self.queue_dir)
        except Exception as exc:
            logger.error("QueueProcessor error: %s", exc, exc_info=True)
            return 0

    def run_forever(self) -> None:
        """Poll the queue continuously until stopped."""
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                logger.error("QueueProcessor fatal error: %s", exc, exc_info=True)

            for _ in range(int(self.poll_interval * 10)):
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)

    def stop(self) -> None:
        """Signal the processor thread to exit."""
        self._stop_event.set()


# --- CLI entry point -----------------------------------------------------------

def main() -> int:
    """CLI entry point for manual queue processing."""
    import argparse

    parser = argparse.ArgumentParser(description="Process Agent1 sync queues")
    parser.add_argument("--process-in", action="store_true", help="Process inbound (Agent1→Kanban)")
    parser.add_argument("--queue-dir", type=str, help="Queue directory to process")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.process_in:
        qdir = Path(args.queue_dir) if args.queue_dir else QUEUE_AGENT1_TO_KANBAN
        count = process_inbound(None, qdir)
        print(f"Processed {count} messages from {qdir}")
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
