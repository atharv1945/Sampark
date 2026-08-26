"""T-1..T-5 — canonical serialization determinism (Phase 5A §4).

No database. Pure functions of sampark.audit.canonical.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from sampark.audit.canonical import (
    NaiveDatetimeError,
    PayloadValidationError,
    canonical_bytes,
    hash_event,
    iso_utc_micros,
)
from sampark.contracts import AuditEvent

EVENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _event(**overrides) -> AuditEvent:
    fields = dict(
        event_id=EVENT_ID,
        event_type="request.received",
        occurred_at=dt.datetime(2025, 9, 10, 9, 0, 0, tzinfo=dt.timezone.utc),
        prev_hash="0" * 64,
        agent_signature="sigbytes",
        reason_code=None,
        payload={"v": 1, "request_id": "req-1", "nested": {"b": 2, "a": 1}},
    )
    fields.update(overrides)
    return AuditEvent(**fields)


def test_canonical_bytes_are_deterministic():
    # T-1: same logical event, constructed via a different dict insertion
    # order (Python dicts preserve insertion order, but json.dumps with
    # sort_keys=True must erase that difference) -> identical bytes.
    e1 = _event(payload={"v": 1, "request_id": "req-1", "nested": {"a": 1, "b": 2}})
    e2 = _event(payload={"nested": {"b": 2, "a": 1}, "request_id": "req-1", "v": 1})
    assert canonical_bytes(e1) == canonical_bytes(e2)


def test_canonical_bytes_are_deterministic_across_repeated_calls():
    e = _event()
    assert canonical_bytes(e) == canonical_bytes(e)
    assert hash_event(e) == hash_event(e)


def test_canonical_bytes_are_deterministic_across_equivalent_timezones():
    # T-1 continued: the same instant expressed in UTC vs +05:30 must
    # produce identical bytes (Phase 5A §4.3 rule 5).
    utc_event = _event(occurred_at=dt.datetime(2025, 9, 10, 9, 0, 0, tzinfo=dt.timezone.utc))
    ist_event = _event(
        occurred_at=dt.datetime(2025, 9, 10, 14, 30, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    )
    assert canonical_bytes(utc_event) == canonical_bytes(ist_event)


def test_canonical_bytes_handle_zero_microseconds_without_dropping_precision():
    # The specific isoformat() hazard (Phase 5A §4.3 rule 5): zero
    # microseconds must still render all six digits, not be omitted.
    e = _event(occurred_at=dt.datetime(2025, 9, 10, 9, 0, 0, 0, tzinfo=dt.timezone.utc))
    assert b'"occurred_at":"2025-09-10T09:00:00.000000Z"' in canonical_bytes(e)


def test_canonical_bytes_differ_on_event_id_change():
    # T-2
    base = _event()
    other = _event(event_id=uuid.uuid4())
    assert canonical_bytes(base) != canonical_bytes(other)


def test_canonical_bytes_differ_on_event_type_change():
    base = _event()
    other = _event(event_type="decision.denied", reason_code="allocation.negative_expected_net")
    assert canonical_bytes(base) != canonical_bytes(other)


def test_canonical_bytes_differ_on_occurred_at_change():
    base = _event()
    other = _event(occurred_at=dt.datetime(2025, 9, 10, 9, 0, 1, tzinfo=dt.timezone.utc))
    assert canonical_bytes(base) != canonical_bytes(other)


def test_canonical_bytes_differ_on_prev_hash_change():
    base = _event()
    other = _event(prev_hash="1" + "0" * 63)
    assert canonical_bytes(base) != canonical_bytes(other)


def test_canonical_bytes_differ_on_agent_signature_change():
    base = _event()
    other = _event(agent_signature="different-sig")
    assert canonical_bytes(base) != canonical_bytes(other)


def test_canonical_bytes_differ_on_reason_code_change():
    base = _event(event_type="decision.denied", reason_code="policy.opt_out_active")
    other = _event(event_type="decision.denied", reason_code="policy.quiet_hours")
    assert canonical_bytes(base) != canonical_bytes(other)


def test_canonical_bytes_differ_on_nested_payload_key_change():
    base = _event(payload={"v": 1, "request_id": "req-1", "nested": {"a": 1}})
    other = _event(payload={"v": 1, "request_id": "req-1", "nested": {"a": 2}})
    assert canonical_bytes(base) != canonical_bytes(other)


def test_hash_event_differs_when_bytes_differ():
    base = _event()
    other = _event(reason_code="policy.opt_out_active", event_type="decision.denied")
    assert hash_event(base) != hash_event(other)
    assert len(hash_event(base)) == 64
    int(hash_event(base), 16)  # valid hex


def test_naive_datetime_is_rejected_by_iso_utc_micros():
    # T-3
    with pytest.raises(NaiveDatetimeError):
        iso_utc_micros(dt.datetime(2025, 9, 10, 9, 0, 0))


def test_naive_occurred_at_is_rejected_when_canonicalizing():
    naive_event = AuditEvent(
        event_id=EVENT_ID, event_type="request.received", occurred_at=dt.datetime(2025, 9, 10, 9, 0, 0),
        prev_hash="0" * 64, agent_signature=None, reason_code=None, payload={"v": 1},
    )
    with pytest.raises(NaiveDatetimeError):
        canonical_bytes(naive_event)


def test_payload_rejects_float_values():
    # T-4
    e = _event(payload={"v": 1, "expected_net_paise": 12.5})
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)


def test_payload_rejects_float_nested_in_dict():
    e = _event(payload={"v": 1, "nested": {"score": 3.14}})
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)


def test_payload_rejects_float_nested_in_list():
    e = _event(payload={"v": 1, "scores": [1, 2.0, 3]})
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)


def test_payload_rejects_non_ascii_identifier_string():
    e = _event(payload={"v": 1, "note": "héllo"})
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)


def test_payload_rejects_free_text_with_spaces():
    e = _event(payload={"v": 1, "message": "hello world"})
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)


def test_payload_accepts_controlled_ascii_identifiers():
    e = _event(
        payload={
            "v": 1,
            "request_id": str(uuid.uuid4()),
            "window_id": "2025-09-10",
            "occurred_at": "2025-09-10T09:00:00.000000Z",
            "reason_code": "policy.quiet_hours",
        }
    )
    canonical_bytes(e)  # must not raise


def test_payload_requires_version_key():
    e = _event(payload={"request_id": "req-1"})  # no "v"
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)


def test_canonical_version_dispatch_is_stable_for_v1():
    # T-5: v=1 hashes the same way regardless of what future versions exist.
    e = _event(payload={"v": 1, "request_id": "req-1"})
    expected = canonical_bytes(e)
    # Re-derive independently to prove the v=1 shape is exactly the seven
    # top-level AuditEvent fields, nothing more, nothing less.
    import json

    manual = json.dumps(
        {
            "event_id": str(e.event_id), "event_type": e.event_type,
            "occurred_at": "2025-09-10T09:00:00.000000Z", "prev_hash": e.prev_hash,
            "agent_signature": e.agent_signature, "reason_code": e.reason_code, "payload": e.payload,
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    assert expected == manual


def test_unknown_payload_version_is_rejected():
    e = _event(payload={"v": 99, "request_id": "req-1"})
    with pytest.raises(PayloadValidationError):
        canonical_bytes(e)
