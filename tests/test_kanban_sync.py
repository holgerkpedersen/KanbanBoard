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


def test_issue_resolve_unknown_source_returns_false(store):
    assert ks._apply_issue_resolve(
        {"op": "issue_resolve", "source_id": "iss-missing", "payload": {}}, store
    ) is False


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
