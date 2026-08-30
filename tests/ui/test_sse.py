"""SSE transport — ordering, resume, and what `seq` is allowed to mean."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.postgres


def _read_stream(client, path, headers=None):
    ids: list[int] = []
    payloads: list[dict] = []
    with client.stream("GET", path, headers=headers or {}) as stream:
        for line in stream.iter_lines():
            if line.startswith("id: "):
                ids.append(int(line[4:]))
            elif line.startswith("data: ") and payloads is not None:
                try:
                    payloads.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
            elif line.startswith("event: end"):
                break
    return ids, payloads


def test_the_stream_delivers_every_event_in_chain_order(demo_api):
    client, _conn = demo_api
    ids, payloads = _read_stream(client, "/api/stream?after_seq=0")
    assert ids == sorted(ids), "SSE frames were not in seq order"
    assert len(ids) == len(set(ids)), "SSE delivered a duplicate seq"
    stored = client.get("/api/events?limit=5000").json()
    assert ids == [e["seq"] for e in stored]
    assert [p["event_id"] for p in payloads] == [e["event_id"] for e in stored]


def test_the_id_field_is_the_seq_so_the_browser_can_resume(demo_api):
    client, _conn = demo_api
    ids, payloads = _read_stream(client, "/api/stream?after_seq=0")
    assert [p["seq"] for p in payloads] == ids


def test_last_event_id_resumes_exactly_after_that_event(demo_api):
    """This is what makes a dropped connection survivable without a
    server-side per-client buffer."""
    client, _conn = demo_api
    ids, _ = _read_stream(client, "/api/stream?after_seq=0")
    midpoint = ids[len(ids) // 2]
    resumed, _ = _read_stream(client, "/api/stream", headers={"Last-Event-ID": str(midpoint)})
    assert resumed[0] == midpoint + 1
    assert resumed == [i for i in ids if i > midpoint]


def test_a_malformed_resume_header_does_not_break_the_stream(demo_api):
    client, _conn = demo_api
    resumed, _ = _read_stream(client, "/api/stream?after_seq=0", headers={"Last-Event-ID": "garbage"})
    assert resumed and resumed[0] == 1


def test_two_concurrent_readers_each_get_the_whole_stream(demo_api):
    """Readers are independent cursors over one table — no shared fan-out
    buffer, so one slow client cannot starve another."""
    client, _conn = demo_api
    first, _ = _read_stream(client, "/api/stream?after_seq=0")
    second, _ = _read_stream(client, "/api/stream?after_seq=0")
    assert first == second


def test_the_stream_terminates_with_an_end_event_once_the_run_is_over(demo_api):
    client, _conn = demo_api
    saw_end = False
    with client.stream("GET", "/api/stream?after_seq=0") as stream:
        for line in stream.iter_lines():
            if line.startswith("event: end"):
                saw_end = True
                break
    assert saw_end


def test_every_streamed_frame_is_a_real_audit_row(demo_api):
    """The trace-integrity rule at the transport layer: nothing synthesised,
    nothing enriched, nothing invented."""
    client, conn = demo_api
    _ids, payloads = _read_stream(client, "/api/stream?after_seq=0")
    with conn.cursor() as cur:
        cur.execute("SELECT event_id::text FROM audit_events")
        real = {row[0] for row in cur.fetchall()}
    assert {p["event_id"] for p in payloads} == real


def test_frames_carry_prev_hash_and_a_recomputed_hash_that_chain(demo_api):
    client, _conn = demo_api
    _ids, payloads = _read_stream(client, "/api/stream?after_seq=0")
    from sampark.audit.canonical import GENESIS_HASH

    assert payloads[0]["prev_hash"] == GENESIS_HASH
    for previous, current in zip(payloads, payloads[1:]):
        assert current["prev_hash"] == previous["hash"]


def test_the_stream_module_exposes_exactly_one_query(demo_api):
    from ui import sse

    assert "audit_events" in sse.EVENTS_SQL
    assert sse.EVENTS_SQL.lower().count("from") == 1
