"""Regression tests for the Kanban side of the Agent1 ↔ Kanban sync.

These cover three frame-resolution defects that made every synced card land
in the wrong column and made ``issue_resolve`` a silent no-op:

1. ``ISSUE_TO_FRAME`` held a mix of titles and the non-existent title
   "Issues"; combined with comparing a *title* against *frame ids* the
   lookup always fell through to ``all_frames[0]`` (the first column).
2. ``_apply_issue_create`` received the envelope but read ``source_id`` off
   the payload, so the issue→card mapping was never recorded — later
   ``issue_update`` / ``issue_resolve`` messages could never match a card.
3. ``_apply_issue_resolve`` called ``store.get_frame("Finished")`` /
   ``store.move_card(card_id, "Finished")`` with a title where the store
   requires a frame id, so resolving an issue never moved its card.
"""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from src.agent1 import kanban_sync as ks
from src.agent1.models import Card, Frame
from src.agent1.store import BoardStore

# Frame titles as the real seed boards define them (opaque ids, real titles).
FRAME_TITLES = ["Ideas", "Planned", "On-going", "Quality & Assurance", "Finished"]


@pytest.fixture(autouse=True)
def _isolate_id_map(monkeypatch, tmp_path):
    """Keep the shared queue/id-map paths inside ``tmp_path``.

    ``DATA_DIR`` and the derived module-level paths are fixed at import time,
    so without this the tests would read and write the real ``data/`` queue
    and the shared ``.card_id_map.json``.
    """
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(ks, "DATA_DIR", data)
    monkeypatch.setattr(ks, "QUEUE_AGENT1_TO_KANBAN", data / "queue-agent1-to-kanban")
    monkeypatch.setattr(ks, "QUEUE_KANBAN_TO_AGENT1", data / "queue-kanban-to-agent1")
    monkeypatch.setattr(ks, "CARD_ID_MAP_PATH", data / ".card_id_map.json")


@pytest.fixture()
def store(tmp_path):
    """A BoardStore with the canonical columns, stored by opaque frame id."""
    s = BoardStore(path=str(tmp_path / "board.json"))
    for title in FRAME_TITLES:
        s.add_frame(Frame(id=uuid.uuid4().hex, title=title))
    return s


def _frame_title(store, card):
    """Title of the frame currently holding ``card``."""
    card = store.get_card(card.id)
    return {f.id: f.title for f in store.get_all_frames()}.get(card.frame_id)


def _only_card(store):
    cards = store.get_all_cards()
    assert len(cards) == 1, f"expected exactly one card, got {len(cards)}"
    return cards[0]


# ---------------------------------------------------------------------------
# Bug 1 — status must route the card to the matching column
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status,expected",
    [
        ("open", "Ideas"),
        ("planned", "Planned"),
        ("in-progress", "On-going"),
        ("reviewing", "Quality & Assurance"),
    ],
)
def test_issue_create_routes_to_frame_matching_status(store, status, expected):
    """Every status lands in its own column, not in the first one."""
    ok = ks._apply_issue_create(
        {
            "op": "issue_create",
            "source_id": "iss-1",
            "payload": {
                "title": f"issue {status}",
                "status": status,
                "category": "bug",
                "severity": "high",
            },
        },
        store,
    )
    assert ok is True
    assert _frame_title(store, _only_card(store)) == expected


def test_issue_create_defaults_to_ideas_for_unknown_status(store):
    ks._apply_issue_create(
        {"op": "issue_create", "source_id": "iss-x", "payload": {"title": "t", "status": "bogus"}},
        store,
    )
    assert _frame_title(store, _only_card(store)) == "Ideas"


def test_resolve_frame_id_accepts_alias_and_falls_back(store):
    """A board seeded with a different label for the first column still works."""
    other = BoardStore(path=None)
    other.add_frame(Frame(id="f-inbox", title="Inbox"))
    other.add_frame(Frame(id="f-done", title="Done"))

    # Alias: "Ideas" is absent, "Inbox" is the equivalent column.
    assert ks._resolve_frame_id(other, "Ideas") == "f-inbox"
    assert ks._resolve_frame_id(other, "Finished") == "f-done"
    # An id/title that exists on no board falls back to the first frame.
    assert ks._resolve_frame_id(store, "f-inbox") == store.get_all_frames()[0].id
    # A raw frame id belonging to this board is passed straight through.
    own_id = store.get_all_frames()[2].id
    assert ks._resolve_frame_id(store, own_id) == own_id
    # Empty board -> no frame to resolve to.
    empty = BoardStore(path=None)
    assert ks._resolve_frame_id(empty, "Ideas") is None


def test_issue_create_returns_false_on_board_without_frames(tmp_path):
    empty = BoardStore(path=str(tmp_path / "empty.json"))
    assert ks._apply_issue_create(
        {"op": "issue_create", "source_id": "iss-1", "payload": {"title": "t"}}, empty
    ) is False
    assert empty.get_all_cards() == []


# ---------------------------------------------------------------------------
# Bug 2 — the issue→card id mapping must be recorded from the envelope
# ---------------------------------------------------------------------------

def test_issue_create_records_id_mapping_from_envelope(store):
    ks._apply_issue_create(
        {
            "op": "issue_create",
            "source_id": "iss-abc",
            "payload": {"title": "mapped", "status": "open"},
        },
        store,
    )
    card = _only_card(store)
    assert ks.resolve_id("iss-abc") == card.id
    assert ks.resolve_id(card.id) == "iss-abc"  # reverse lookup too


def test_issue_update_finds_card_created_by_envelope(store):
    """The mapping written at create time makes a later update resolve."""
    ks._apply_issue_create(
        {"op": "issue_create", "source_id": "iss-up", "payload": {"title": "before", "status": "planned"}},
        store,
    )
    ok = ks._apply_issue_update(
        {"op": "issue_update", "source_id": "iss-up", "payload": {"title": "after", "status": "planned"}},
        store,
    )
    assert ok is True
    assert _only_card(store).title == "after"


# ---------------------------------------------------------------------------
# Bug 3 — resolve must actually move the card to Finished
# ---------------------------------------------------------------------------

def test_issue_resolve_moves_card_to_finished(store):
    ks._apply_issue_create(
        {"op": "issue_create", "source_id": "iss-9", "payload": {"title": "to resolve", "status": "open"}},
        store,
    )
    card = _only_card(store)
    assert _frame_title(store, card) == "Ideas"

    ok = ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "iss-9", "payload": {"disposition": "resolved"}},
        store,
    )
    assert ok is True
    assert _frame_title(store, card) == "Finished"
    assert "[status:resolved]" in store.get_card(card.id).tags
    # The old status tag must not linger.
    assert not any(
        t.startswith("[status:") and t != "[status:resolved]"
        for t in store.get_card(card.id).tags
    )


def test_issue_resolve_honours_wontfix_disposition(store):
    ks._apply_issue_create(
        {"op": "issue_create", "source_id": "iss-w", "payload": {"title": "wontfix me", "status": "open"}},
        store,
    )
    ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "iss-w", "payload": {"disposition": "wontfix"}},
        store,
    )
    card = _only_card(store)
    assert _frame_title(store, card) == "Finished"
    assert "[status:wontfix]" in card.tags


def test_issue_resolve_unmapped_source_is_quiet_noop(store):
    """A resolve for a source this board never created is a no-op, not a failure.

    This test asserted ``is False`` before ``ab35f84`` introduced the quiet
    no-op.  The queue contract settled on ``True`` in the same commit — see
    ``test_process_single_message_dispatches_all_ops``, which asserts an
    untracked resolve dispatches to ``True`` — so this is the test that was
    left behind.  A *mapped* id pointing at a missing card still returns
    False; see ``test_issue_resolve_stale_map_entry_returns_false``.
    """
    assert ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "iss-missing", "payload": {}}, store
    ) is True


def test_issue_resolve_untracked_source_is_quiet_noop(store):
    """A resolve for a source this board never created must not fail.

    ``ab35f84`` added the quiet no-op, then keyed it on ``msg["op"]`` inside
    ``_apply_issue_resolve`` — which is always ``"issue_resolve"`` there, so
    the branch swallowed *every* unresolved resolve, including ones where the
    id map points at a card that does not exist.  The no-op must be keyed on
    the resolution *origin*: only a source with no mapping at all is an
    untracked echo, and it can never gain a card by being retried.
    """
    assert ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "iss-never-created", "payload": {}}, store
    ) is True


def test_issue_resolve_stale_map_entry_returns_false(store):
    """A map entry pointing at a deleted card is an anomaly, not a no-op."""
    ks.link_ids("iss-stale", "card-gone")
    assert ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "iss-stale", "payload": {}}, store
    ) is False


def test_untracked_resolve_does_not_dead_letter_the_queue(store, tmp_path, monkeypatch):
    """The quiet no-op must not churn the queue into dead letters.

    ``process_inbound`` re-queues every message that returns False, and after
    ``MAX_RETRIES`` dead-letters it.  An untracked resolve is permanent — it
    can never succeed — so treating it as a failure filled ``dead_letters/``
    with late echoes of issues that were never cards on this board.
    """
    monkeypatch.setattr(ks, "DEAD_LETTER_DIR", tmp_path / "dead-letter")
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_resolve", "source_id": "iss-never-created", '
        '"payload": {"disposition": "resolved"}}\n',
        encoding="utf-8",
    )

    # The no-op counts as applied, so the message is consumed and never
    # re-queued.  Under the old False return this returned 0 and appended a
    # retry line instead.
    assert ks.process_inbound(store, qdir) == 1
    lines = (qdir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "no-op must not re-queue the message"

    # Polling for MAX_RETRIES rounds must still produce no dead letter.
    for _ in range(ks.MAX_RETRIES):
        ks.process_inbound(store, qdir)

    assert not list((tmp_path / "dead-letter").glob("seq_*.jsonl")), (
        "untracked issue_resolve must be a no-op, not a retried failure"
    )


def test_resolve_uses_target_ref_directly(store):
    """An explicit target_ref bypasses the id map."""
    ks._apply_issue_create(
        {"op": "issue_create", "source_id": "iss-r", "payload": {"title": "by ref", "status": "open"}},
        store,
    )
    card = _only_card(store)
    ok = ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "ignored", "target_ref": card.id, "payload": {}},
        store,
    )
    assert ok is True
    assert _frame_title(store, card) == "Finished"


# ---------------------------------------------------------------------------
# Dispatcher contract
# ---------------------------------------------------------------------------

def test_process_single_message_dispatches_all_ops(store):
    assert ks._process_single_message(
        {"op": "issue_create", "source_id": "s1", "payload": {"title": "disp", "status": "planned"}},
        store,
    ) is True
    assert ks._process_single_message(
        {"op": "issue_update", "source_id": "s1", "payload": {"title": "disp2", "status": "planned"}},
        store,
    ) is True
    assert ks._process_single_message(
        {"op": "issue_resolve", "source_id": "s1", "payload": {}}, store
    ) is True
    assert ks._process_single_message({"op": "nope"}, store) is False


def test_card_claimed_is_consumed_not_dead_lettered(store, tmp_path, monkeypatch):
    """Regression: Agent1 ``card_claimed`` was an unknown op and dead-lettered.

    The claim is advisory; the board must accept it (True) so the queue
    consumes it instead of retrying it into ``dead_letters/``.
    """
    msg = {
        "op": "card_claimed",
        "source_id": "",
        "payload": {"card_id": "abc123", "owner": "agent-x", "claimed_at": "t"},
    }
    assert ks._process_single_message(msg, store) is True

    monkeypatch.setattr(ks, "DEAD_LETTER_DIR", tmp_path / "dead-letter")
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "card_claimed", "payload": {"card_id": "abc123"}}\n',
        encoding="utf-8",
    )
    assert ks.process_inbound(store, qdir) == 1
    for _ in range(ks.MAX_RETRIES):
        ks.process_inbound(store, qdir)
    assert not list((tmp_path / "dead-letter").glob("seq_*.jsonl"))


def test_card_claimed_malformed_payload_is_rejected(store):
    assert ks._process_single_message(
        {"op": "card_claimed", "payload": "not-a-dict"}, store
    ) is False


def test_process_single_message_rejects_malformed_payload(store):
    """A malformed payload is rejected cleanly, not raised out of the processor.

    Returning False is what routes the message to the dead-letter directory;
    an exception here would abort the whole batch.
    """
    assert ks._process_single_message(
        {"op": "issue_create", "source_id": "s2", "payload": "not-a-dict"}, store
    ) is False
    assert ks._process_single_message(
        {"op": "issue_update", "source_id": "s2", "payload": ["nope"]}, store
    ) is False
    # A payload with no title cannot produce a card.
    assert ks._process_single_message(
        {"op": "issue_create", "source_id": "s3", "payload": {"status": "open"}}, store
    ) is False
    assert store.get_all_cards() == []


def test_malformed_message_is_dead_lettered(store, tmp_path, monkeypatch):
    """A malformed line must not stop the following good message being applied."""
    monkeypatch.setattr(ks, "DEAD_LETTER_DIR", tmp_path / "dead-letter")
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_create", "source_id": "bad", "payload": "not-a-dict"}\n'
        '{"seq": 2, "op": "issue_create", "source_id": "good", '
        '"payload": {"title": "survivor", "status": "planned"}}\n',
        encoding="utf-8",
    )

    ks.process_inbound(store, qdir)
    # The good message still landed.
    assert [c.title for c in store.get_all_cards()] == ["survivor"]


# ---------------------------------------------------------------------------
# Queue round-trip: offset advancing and idempotency
# ---------------------------------------------------------------------------

def test_process_inbound_applies_and_advances_offset(store, tmp_path):
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_create", "source_id": "q1", '
        '"payload": {"title": "queued", "status": "on-going"}, "processed": false}\n'
        '{"seq": 2, "op": "issue_create", "source_id": "q2", '
        '"payload": {"title": "queued2", "status": "planned"}, "processed": false}\n',
        encoding="utf-8",
    )

    assert ks.process_inbound(store, qdir) == 2
    assert (qdir / "offset.txt").read_text(encoding="utf-8") == "2"
    assert len(store.get_all_cards()) == 2

    # A second pass reads nothing new.
    assert ks.process_inbound(store, qdir) == 0
    assert len(store.get_all_cards()) == 2


def test_read_queue_skips_blank_and_malformed_lines(store, tmp_path):
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_create", "source_id": "ok", "payload": {"title": "t"}}\n'
        "\n"
        "{not valid json}\n",
        encoding="utf-8",
    )
    messages, offset = ks.read_queue(qdir)
    assert len(messages) == 1
    assert messages[0]["source_id"] == "ok"
    assert offset == 3


def test_read_queue_quarantines_malformed_line(store, tmp_path):
    """A bad JSONL line must be preserved, not dropped into the log.

    This side used to log ``Skipping malformed JSON`` and lose the raw bytes.
    The line can never be retried into success, so the offset still advances
    past it — but the only copy of what the sender wrote must survive under
    ``dead_letters/``.  Pins the port of Agent1's ``_quarantine_malformed``.
    """
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    good = json.dumps({"seq": 1, "op": "issue_create", "source_id": "ok"})
    (qdir / "messages.jsonl").write_text(
        good + "\n{not valid json}\n",
        encoding="utf-8",
    )

    messages, offset = ks.read_queue(qdir)

    # The good line still comes through and the offset clears the bad one.
    assert len(messages) == 1
    assert messages[0]["source_id"] == "ok"
    assert offset == 2

    quarantined = list((qdir / "dead_letters").glob("malformed_line_*.jsonl"))
    assert len(quarantined) == 1, f"expected one quarantined line, got {quarantined}"
    record = json.loads(quarantined[0].read_text(encoding="utf-8").splitlines()[0])
    assert record["raw"] == "{not valid json}"
    assert "error" in record


def test_advance_offset_is_serialised_per_queue(tmp_path):
    """The offset write must hold the per-queue lock.

    ``offset.txt`` is shared state: an unguarded write can lose an update when
    the poller thread and a manual ``--process-in`` run overlap.
    """
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()

    held = []
    real_lock = ks._seq_lock_for(qdir)

    class _SpyLock:
        def __enter__(self):
            held.append(True)
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    original = ks._seq_lock_for
    try:
        ks._seq_lock_for = lambda d: _SpyLock()
        ks.advance_offset(qdir, 7)
    finally:
        ks._seq_lock_for = original

    assert held == [True], "advance_offset must write the offset under the lock"
    assert (qdir / "offset.txt").read_text(encoding="utf-8") == "7"


def test_read_queue_missing_file_is_empty(tmp_path):
    messages, offset = ks.read_queue(tmp_path / "nope")
    assert messages == []
    assert offset == 0


# ---------------------------------------------------------------------------
# Dead-lettering: durable retries + offset always advances
# ---------------------------------------------------------------------------

def test_offset_advances_even_when_every_message_fails(store, tmp_path):
    """A poison message must not pin the offset and stall the whole queue.

    Pre-fix the offset only moved ``if applied > 0``, so a message that always
    failed was re-read on every poll and blocked every later message forever.
    """
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_create", "source_id": "bad", "payload": "not-a-dict"}\n',
        encoding="utf-8",
    )

    assert ks.process_inbound(store, qdir) == 0
    assert (qdir / "offset.txt").read_text(encoding="utf-8") == "1"


def test_failed_message_is_requeued_with_incremented_retry_count(store, tmp_path):
    """retry_count must be persisted, or MAX_RETRIES is unreachable."""
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_create", "source_id": "bad", "payload": "not-a-dict"}\n',
        encoding="utf-8",
    )

    ks.process_inbound(store, qdir)

    lines = (qdir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "failed message must be re-queued at the tail"
    requeued = json.loads(lines[-1])
    assert requeued["retry_count"] == 1
    assert requeued["processed"] is False


def test_poison_message_is_dead_lettered_after_max_retries(store, tmp_path, monkeypatch):
    monkeypatch.setattr(ks, "DEAD_LETTER_DIR", tmp_path / "dead-letter")
    qdir = tmp_path / "queue-agent1-in"
    qdir.mkdir()
    (qdir / "messages.jsonl").write_text(
        '{"seq": 1, "op": "issue_create", "source_id": "bad", "payload": "not-a-dict"}\n',
        encoding="utf-8",
    )

    for _ in range(ks.MAX_RETRIES):
        ks.process_inbound(store, qdir)

    dead = list((tmp_path / "dead-letter").glob("seq_*.jsonl"))
    assert len(dead) == 1, f"expected one dead-letter file, got {dead}"
    assert json.loads(dead[0].read_text(encoding="utf-8").splitlines()[0])["seq"] == 1


# ---------------------------------------------------------------------------
# enqueue(): writer counter must not consume the reader's cursor
# ---------------------------------------------------------------------------

def test_enqueue_does_not_advance_reader_offset(tmp_path):
    """Regression: enqueue() wrote the seq counter into offset.txt.

    ``offset.txt`` is the *reader's* cursor and the reader is a different
    process, so sharing one file between "next seq" and "lines consumed" made
    every write advance the counterpart's cursor past the line just written —
    no message was ever delivered.  The writer's counter is ``seq.txt``.
    """
    qdir = tmp_path / "queue-kanban-to-agent1"
    qdir.mkdir()

    seq = ks.enqueue({"op": "issue_update", "source_id": "iss-1"}, qdir)
    assert seq == 1

    # Writer's counter moved...
    assert (qdir / "seq.txt").read_text(encoding="utf-8") == "1"
    # ...but the reader's cursor did not, so the line is still readable.
    assert not (qdir / "offset.txt").exists()
    messages, next_offset = ks.read_queue(qdir)
    assert [m["source_id"] for m in messages] == ["iss-1"]
    assert next_offset == 1


def test_enqueue_sequences_are_contiguous_and_unique(tmp_path):
    qdir = tmp_path / "queue-kanban-to-agent1"
    qdir.mkdir()

    seqs = [ks.enqueue({"op": "issue_create", "source_id": f"s{i}"}, qdir) for i in range(5)]
    assert seqs == [1, 2, 3, 4, 5]

    lines = (qdir / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [1, 2, 3, 4, 5]


def test_enqueue_message_is_readable_by_reader_in_one_pass(tmp_path):
    """A message enqueued then read must be applied exactly once (no loss)."""
    qdir = tmp_path / "queue-kanban-to-agent1"
    qdir.mkdir()

    ks.enqueue({"op": "issue_create", "source_id": "iss-x"}, qdir)

    messages, _ = ks.read_queue(qdir)
    assert len(messages) == 1
    ks.advance_offset(qdir, 1)

    # Already consumed — a second read yields nothing.
    messages2, _ = ks.read_queue(qdir)
    assert messages2 == []


# ---------------------------------------------------------------------------
# Shared state with Agent1: one data dir, one id-map filename
# ---------------------------------------------------------------------------

def test_id_map_filename_matches_agent1(tmp_path):
    """The issue↔card map must be one file both sides agree on.

    Agent1 derives ``CARD_ID_MAP_PATH`` as ``DATA_DIR / ".card_id_map.json"``.
    Kanban used ``.agent1_card_id_map.json``, so each side kept a private map
    and issue_update/issue_resolve could never match a card.
    """
    assert ks.CARD_ID_MAP_PATH.name == ".card_id_map.json"
    assert ks.CARD_ID_MAP_PATH.parent == ks.DATA_DIR


def test_id_map_round_trips_through_shared_file(tmp_path, monkeypatch):
    """link_ids/resolve_id must use the shared path, not a local copy."""
    monkeypatch.setattr(ks, "CARD_ID_MAP_PATH", tmp_path / ".card_id_map.json")

    ks.link_ids("iss-1", "card-1")
    assert (tmp_path / ".card_id_map.json").exists()
    assert ks.resolve_id("iss-1") == "card-1"
    # Reverse lookup, so Agent1 can map a card back to its issue.
    assert ks.resolve_id("card-1") == "iss-1"


def test_queue_names_describe_direction_not_side():
    """Both repos must agree on which queue carries which direction.

    The old "in"/"out" names meant opposite things on the two sides, so even
    with a shared root the peers read and wrote different directories.  These
    paths are derived at import time, so assert their shape rather than
    re-deriving them from a patched DATA_DIR.
    """
    assert ks.QUEUE_AGENT1_TO_KANBAN.parent == ks.DATA_DIR
    assert ks.QUEUE_KANBAN_TO_AGENT1.parent == ks.DATA_DIR
    assert ks.QUEUE_AGENT1_TO_KANBAN.name == "queue-agent1-to-kanban"
    assert ks.QUEUE_KANBAN_TO_AGENT1.name == "queue-kanban-to-agent1"


# ---------------------------------------------------------------------------
# Echo suppression — an issue_create that Agent1 already has a card for must
# not add a second card, or the two sides ping-pong forever.
# ---------------------------------------------------------------------------

def _envelope(issue_id, title="Echoed issue"):
    return {
        "op": "issue_create",
        "source_id": issue_id,
        "payload": {"id": issue_id, "title": title, "status": "open"},
    }


def _seed_card(store, card_id, title="Original card"):
    """Put a real card on the board and return it (mirrors the Agent1-side card)."""
    frame_id = _resolve_first_frame(store)
    card = Card(id=card_id, title=title, text="", frame_id=frame_id, tags=[], system="harnessfix")
    store.add_card(card, frame_id)
    return card


def _resolve_first_frame(store):
    return store.get_all_frames()[0].id


def test_issue_create_echo_for_known_issue_does_not_add_second_card(store):
    """The exact loop: Agent1 mirrors a card to an issue, then echoes back.

    Agent1's ``_apply_card_create`` records issue→card and calls
    ``make_issue()``, which enqueues ``issue_create`` for that same issue.
    Kanban must recognise it already has the card.
    """
    # Agent1 side of the round trip: the card exists and is mapped to the issue.
    _seed_card(store, "card-orig")
    ks.link_ids("iss-echo", "card-orig")

    assert ks._apply_issue_create(_envelope("iss-echo"), store) is True

    assert _only_card(store).id == "card-orig", "echo must not create a second card"
    assert ks.resolve_id("iss-echo") == "card-orig", "mapping must be untouched"


def test_issue_create_echo_keeps_board_stable_across_repeated_polls(store):
    """Repeated echoes must not grow the board — no unbounded ping-pong."""
    _seed_card(store, "card-orig")
    ks.link_ids("iss-echo", "card-orig")
    msg = _envelope("iss-echo")

    for _ in range(5):
        assert ks._apply_issue_create(msg, store) is True

    assert len(store.get_all_cards()) == 1
    assert _only_card(store).id == "card-orig"
    assert ks.resolve_id("iss-echo") == "card-orig"


def test_issue_create_still_creates_card_for_new_issue(store):
    """The guard must not over-suppress: a genuinely new issue still lands."""
    assert ks._apply_issue_create(_envelope("iss-new", title="Fresh"), store) is True

    card = _only_card(store)
    assert card.title == "Fresh"
    assert ks.resolve_id("iss-new") == card.id


def test_issue_create_ignores_stale_mapping_to_deleted_card(store):
    """A mapping pointing at a card that no longer exists must not block create.

    If the card was deleted from the board, the stale entry must not stop the
    issue from being materialised again.
    """
    ks.link_ids("iss-gone", "card-deleted")  # no such card on the board

    assert ks._apply_issue_create(_envelope("iss-gone", title="Recreated"), store) is True

    card = _only_card(store)
    assert card.title == "Recreated"
    assert ks.resolve_id("iss-gone") == card.id


# ---------------------------------------------------------------------------
# Child-card emission — the Kanban-side half of the work-visibility bridge.
# Agent1 emits a ``child_card_create`` op (parent_id + work summary) and the
# reader must nest the resulting card under the parent via parent_id.
# ---------------------------------------------------------------------------


def _child_card_message(parent_id, title="Work summary", text="Work body", issue_id="iss-work"):
    """Build an Agent1 child-card CREATE envelope for the reader test."""
    return {
        "op": "child_card_create",
        "source_id": issue_id,
        "payload": {
            "parent_id": parent_id,
            "title": title,
            "text": text,
            "issue_id": issue_id,
            "status": "open",
        },
    }


def test_child_card_create_nests_under_parent(store):
    """A child_card_create with a valid parent creates a child with parent_id set."""
    parent = _seed_card(store, "parent-card", title="Parent card")

    ok = ks._apply_child_card_create(_child_card_message("parent-card", title="Work on card"), store)
    assert ok is True

    cards = store.get_all_cards()
    assert len(cards) == 2
    child = next(c for c in cards if c.id != parent.id)
    assert child.parent_id == "parent-card"
    assert child.frame_id == parent.frame_id, "child must land in the parent's own frame"
    assert child.title == "Work on card"
    assert child.system == "harnessfix"


def test_child_card_create_without_parent_id_is_noop(store):
    """A child_card_create with no parent_id must not create a card (best-effort)."""
    before = len(store.get_all_cards())
    ok = ks._apply_child_card_create(
        {
            "op": "child_card_create",
            "source_id": "iss-x",
            "payload": {"title": "orphan", "text": "orphan body"},
        },
        store,
    )
    assert ok is False
    assert len(store.get_all_cards()) == before


def test_child_card_create_unknown_parent_is_noop(store):
    """A child whose parent card does not exist is a best-effort no-op, not an error."""
    before = len(store.get_all_cards())
    ok = ks._apply_child_card_create(_child_card_message("no-such-parent"), store)
    assert ok is False
    assert len(store.get_all_cards()) == before


def test_child_card_create_malformed_payload_is_noop(store):
    """A non-dict payload is rejected at the dispatcher; the handler stays safe."""
    before = len(store.get_all_cards())
    ok = ks._apply_child_card_create(
        {"op": "child_card_create", "source_id": "iss-x", "payload": "not-a-dict"}, store
    )
    assert ok is False
    assert len(store.get_all_cards()) == before


def test_child_card_create_dispatches_via_processor(store):
    """The message dispatcher routes child_card_create to the handler."""
    parent = _seed_card(store, "parent-card", title="Parent card")
    msg = _child_card_message("parent-card", title="Work summary")

    ok = ks._process_single_message(msg, store)
    assert ok is True

    cards = store.get_all_cards()
    assert len(cards) == 2
    child = next(c for c in cards if c.id != parent.id)
    assert child.parent_id == "parent-card"


# ---------------------------------------------------------------------------
# CLI entry point: ``main()`` used to call ``process_inbound(None, qdir)``, so
# every op dereferenced ``store.get_card`` on ``None`` and manual queue
# draining crashed.  The CLI must bind a real store — explicit --board first,
# the registry's active board second — and refuse cleanly when neither exists.
# ---------------------------------------------------------------------------


def _seeded_board(path):
    """A board file with the canonical columns (a card needs a frame to land in)."""
    s = BoardStore(path=str(path))
    for title in FRAME_TITLES:
        s.add_frame(Frame(id=uuid.uuid4().hex, title=title))
    return s


def _write_inbound(qdir, *source_ids):
    """Write one issue_create message per source id into ``qdir``."""
    qdir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "seq": i + 1,
                "op": "issue_create",
                "source_id": sid,
                "payload": {"id": sid, "title": f"queued {sid}", "status": "open"},
                "processed": False,
            }
        )
        for i, sid in enumerate(source_ids)
    ]
    (qdir / "messages.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_main_applies_queue_to_explicit_board(tmp_path, monkeypatch, capsys):
    """``--process-in --board <file>`` drains the queue into that board file."""
    board_path = tmp_path / "board-cli.json"
    _seeded_board(board_path)
    qdir = tmp_path / "queue-agent1-in"
    _write_inbound(qdir, "iss-cli-1", "iss-cli-2")

    monkeypatch.setattr(
        sys,
        "argv",
        ["kanban_sync", "--process-in", "--board", str(board_path), "--queue-dir", str(qdir)],
    )
    assert ks.main() == 0

    saved = json.loads(board_path.read_text(encoding="utf-8"))
    titles = sorted(c["title"] for c in saved["cards"])
    assert titles == ["queued iss-cli-1", "queued iss-cli-2"], (
        "the CLI must apply queued messages to the board it was pointed at"
    )
    out = capsys.readouterr().out
    assert "Processed 2 messages" in out


def test_cli_main_uses_active_board_from_registry(tmp_path, monkeypatch, capsys):
    """Without --board, the CLI applies to the registry's active board file.

    This is the same resolution the web app uses (``data/active.json`` →
    ``data/board-<id>.json``), so a manual drain mutates the live board
    instead of crashing on ``store=None``.
    """
    from src.agent1.board_registry import BoardRegistry

    # The autouse fixture already created tmp_path/data and pointed DATA_DIR
    # there; reuse it so the registry and board files land where _cli_store
    # looks.
    data_dir = tmp_path / "data"
    monkeypatch.setattr(ks, "DATA_DIR", data_dir)

    board_path = data_dir / "board-live.json"
    _seeded_board(board_path)
    BoardRegistry(path=str(data_dir / "active.json")).set_active("live")

    qdir = tmp_path / "queue-agent1-in"
    _write_inbound(qdir, "iss-cli-3")

    monkeypatch.setattr(sys, "argv", ["kanban_sync", "--process-in", "--queue-dir", str(qdir)])
    assert ks.main() == 0

    saved = json.loads(board_path.read_text(encoding="utf-8"))
    assert [c["title"] for c in saved["cards"]] == ["queued iss-cli-3"]


def test_cli_main_refuses_without_any_board(tmp_path, monkeypatch, capsys):
    """No --board and no active board: exit non-zero with a clear message.

    The old behaviour was to pass ``None`` and crash inside the first op;
    refusing up-front is the contract.
    """
    # tmp_path/data exists (autouse fixture) but holds no active.json.
    data_dir = tmp_path / "data"
    monkeypatch.setattr(ks, "DATA_DIR", data_dir)  # registry file absent

    qdir = tmp_path / "queue-agent1-in"
    _write_inbound(qdir, "iss-cli-4")

    monkeypatch.setattr(sys, "argv", ["kanban_sync", "--process-in", "--queue-dir", str(qdir)])
    assert ks.main() == 2

    err = capsys.readouterr().err
    assert "No board to apply messages to" in err
    # The queue must be untouched — nothing was applied, nothing consumed.
    assert not (qdir / "offset.txt").exists()


def test_cli_main_without_flags_prints_help(tmp_path, monkeypatch):
    """Bare invocation shows usage and exits non-zero (unchanged behaviour)."""
    monkeypatch.setattr(sys, "argv", ["kanban_sync"])
    assert ks.main() == 1


def test_cli_store_explicit_path_beats_registry(tmp_path, monkeypatch):
    """--board wins over the active board, so operators can drain to a copy."""
    from src.agent1.board_registry import BoardRegistry

    data_dir = tmp_path / "data"  # created by the autouse fixture
    monkeypatch.setattr(ks, "DATA_DIR", data_dir)

    live = data_dir / "board-live.json"
    _seeded_board(live)
    BoardRegistry(path=str(data_dir / "active.json")).set_active("live")

    explicit = tmp_path / "copy.json"
    BoardStore(path=str(explicit)).add_frame(Frame(id="f-ideas", title="Ideas"))

    store = ks._cli_store(str(explicit))
    assert store is not None
    assert Path(store.path) == explicit


def test_cli_store_registry_pointing_at_missing_file_is_none(tmp_path, monkeypatch):
    """An active id whose board file is gone yields None (caller refuses)."""
    from src.agent1.board_registry import BoardRegistry

    data_dir = tmp_path / "data"  # created by the autouse fixture
    monkeypatch.setattr(ks, "DATA_DIR", data_dir)
    BoardRegistry(path=str(data_dir / "active.json")).set_active("ghost")

    assert ks._cli_store(None) is None
