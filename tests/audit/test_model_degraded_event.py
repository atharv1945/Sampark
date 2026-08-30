"""`model.degraded` — the ONE new event type Phase 8 adds (spec §12.3).

Everything else Phase 8 renders reuses an existing type. This one exists
because spec §12.3 requires the allocator to "log a degradation event" and no
existing type carries that fact.

These tests hold it to exactly the same rules as the twelve types before it:
deterministic id, canonical payload, no free text, no floats, correct
signed/terminal/order membership.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.audit.canonical import PayloadValidationError, canonical_bytes, hash_event, validate_payload
from sampark.audit.emit import (
    MODEL_DEGRADED_ARTIFACT_UNAVAILABLE,
    MODEL_DEGRADED_KILLED_BY_OPERATOR,
    event_for_model_degraded,
)
from sampark.audit.event_types import (
    EVENT_TYPES,
    HOLDOUT_ASSIGNED,
    MODEL_DEGRADED,
    SIGNED_EVENT_TYPES,
    TERMINAL_EVENT_TYPES,
    TYPE_ORDER,
)

WINDOW = dt.date(2025, 9, 13)
AT = dt.datetime(2025, 9, 13, 3, 30, tzinfo=dt.timezone.utc)


def _event(reason=MODEL_DEGRADED_KILLED_BY_OPERATOR):
    return event_for_model_degraded(reason, "ModelBackedScorer", "HeuristicScorer", WINDOW, AT)


def test_it_joined_the_closed_vocabulary():
    assert MODEL_DEGRADED in EVENT_TYPES
    assert MODEL_DEGRADED == "model.degraded"


def test_it_is_unsigned_like_every_other_system_initiated_event():
    """No agent requested a scorer failure, so there is no signature to
    attach — same as agent.registered and holdout.assigned."""
    assert MODEL_DEGRADED not in SIGNED_EVENT_TYPES
    assert _event().agent_signature is None


def test_it_is_not_part_of_any_grant_or_request_lifecycle():
    assert MODEL_DEGRADED not in TERMINAL_EVENT_TYPES


def test_it_sorts_before_the_decisions_it_explains():
    """It is stamped with the window's decision_at — the same instant every
    decision.* event for that window carries — and it explains them, so it
    must sort strictly first."""
    from sampark.audit.event_types import DECISION_DEFERRED, DECISION_DENIED

    assert TYPE_ORDER[MODEL_DEGRADED] < TYPE_ORDER[DECISION_DENIED]
    assert TYPE_ORDER[MODEL_DEGRADED] < TYPE_ORDER[DECISION_DEFERRED]


def test_it_did_not_displace_the_phase_7_holdout_ordering_invariant():
    """tests/audit/test_emit_phase7.py asserts holdout.assigned is the unique
    minimum of TYPE_ORDER. Phase 8 must not weaken a Phase 7 invariant."""
    assert TYPE_ORDER[HOLDOUT_ASSIGNED] < min(
        v for k, v in TYPE_ORDER.items() if k != HOLDOUT_ASSIGNED
    )


def test_the_event_id_is_deterministic_and_idempotent_per_window_and_reason():
    a = _event()
    b = _event()
    assert a.event_id == b.event_id, "a repeated kill in one window is ONE fact"
    other_window = event_for_model_degraded(
        MODEL_DEGRADED_KILLED_BY_OPERATOR, "ModelBackedScorer", "HeuristicScorer",
        dt.date(2025, 9, 14), AT,
    )
    assert a.event_id != other_window.event_id
    other_reason = _event(MODEL_DEGRADED_ARTIFACT_UNAVAILABLE)
    assert a.event_id != other_reason.event_id


def test_the_payload_satisfies_the_canonicalisation_rules():
    event = _event()
    validate_payload(event.payload)  # raises on a float, non-ASCII, or free text
    assert event.payload == {
        "window_id": "2025-09-13",
        "scorer_before": "ModelBackedScorer",
        "scorer_after": "HeuristicScorer",
        "v": 1,
    }
    assert event.reason_code == MODEL_DEGRADED_KILLED_BY_OPERATOR


def test_it_canonicalises_and_hashes_stably():
    a, b = _event(), _event()
    assert canonical_bytes(a) == canonical_bytes(b)
    assert hash_event(a) == hash_event(b)
    assert len(hash_event(a)) == 64


def test_free_text_in_the_payload_is_rejected_by_the_existing_rules():
    """The exact defect that once forced Agent.publisher out of
    event_for_agent_registered. Proving the guard still bites here means a
    future change cannot smuggle a log message into the chain."""
    bad = _event().model_copy(
        update={"payload": {"window_id": "2025-09-13", "note": "model died :(", "v": 1}}
    )
    with pytest.raises(PayloadValidationError):
        canonical_bytes(bad)


def test_both_reason_codes_are_controlled_identifiers():
    for reason in (MODEL_DEGRADED_ARTIFACT_UNAVAILABLE, MODEL_DEGRADED_KILLED_BY_OPERATOR):
        validate_payload({"reason_code": reason, "v": 1})
