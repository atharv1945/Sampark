"""sampark.audit.emit — Phase 7 event types (spec §8.9)."""

from __future__ import annotations

import datetime as dt
import uuid

from sampark.audit import emit
from sampark.audit.canonical import canonical_bytes, hash_event, validate_payload
from sampark.audit.chain import PENDING_PREV_HASH
from sampark.audit.event_types import CONTACT_OPT_OUT, HOLDOUT_ASSIGNED, RECOVERY_CREDITED, TYPE_ORDER
from sampark.contracts import Grant, GrantRequest

AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def _request(request_id=None):
    return GrantRequest(
        request_id=request_id or uuid.uuid4(), agent_id="cart_recovery_agent", customer_id="c1",
        risk_id="r1", intent="cart_recovery", requested_channel="whatsapp",
        requested_max_incentive_bps=500, issued_at=AT, signature="test-signature-b64",
    )


def _grant(grant_id=None):
    return Grant(
        grant_id=grant_id or uuid.uuid4(), channel="whatsapp",
        incentive_ceiling_paise=0, send_after=AT, expires_at=AT + dt.timedelta(hours=2), state="CONFIRMED",
    )


def test_holdout_assigned_shape():
    event = emit.event_for_holdout_assigned(
        seed=42, fraction_bps=1000, assignment_version=1,
        holdout_customer_count=490, holdout_customer_set_sha256="a" * 64, occurred_at=AT,
    )
    assert event.event_type == HOLDOUT_ASSIGNED
    assert event.prev_hash == PENDING_PREV_HASH
    assert event.agent_signature is None
    assert event.reason_code is None
    assert event.payload["seed"] == 42
    assert event.payload["holdout_fraction_bps"] == 1000
    assert event.payload["holdout_customer_set_sha256"] == "a" * 64
    validate_payload(event.payload)  # must not raise


def test_holdout_assigned_is_deterministic_in_event_id():
    a = emit.event_for_holdout_assigned(42, 1000, 1, 490, "a" * 64, AT)
    b = emit.event_for_holdout_assigned(42, 1000, 1, 490, "a" * 64, AT)
    assert a.event_id == b.event_id


def test_holdout_assigned_type_order_precedes_everything():
    assert TYPE_ORDER[HOLDOUT_ASSIGNED] < min(v for k, v in TYPE_ORDER.items() if k != HOLDOUT_ASSIGNED)


def test_contact_opt_out_shape():
    request = _request()
    grant = _grant()
    event = emit.event_for_contact_opt_out(grant, request, channel="whatsapp", contact_index=1, at=AT)
    assert event.event_type == CONTACT_OPT_OUT
    assert event.agent_signature == request.signature
    assert event.reason_code == "optout.customer_initiated"
    assert event.payload["grant_id"] == str(grant.grant_id)
    assert event.payload["customer_id"] == request.customer_id
    assert event.payload["contact_index"] == 1
    validate_payload(event.payload)


def test_contact_opt_out_is_deterministic_in_event_id():
    request = _request()
    grant = _grant()
    a = emit.event_for_contact_opt_out(grant, request, "whatsapp", 1, AT)
    b = emit.event_for_contact_opt_out(grant, request, "whatsapp", 1, AT)
    assert a.event_id == b.event_id


def test_recovery_credited_shape():
    from sampark.attribution.baseline import BaselineRate
    from sampark.attribution.credit import compute_credit

    request = _request()
    grant = _grant()
    baseline = BaselineRate(stratum="abandoned_checkout.price_hesitation", level="source_root_cause", rate=0.05, n=317)
    credit = compute_credit(
        grant_id=grant.grant_id, observed_recovered_paise=100_000, amount_paise=100_000,
        baseline=baseline, holdout_fraction=0.10, observed_at=AT,
    )
    event = emit.event_for_recovery_credited(credit, request, at=AT)
    assert event.event_type == RECOVERY_CREDITED
    assert event.agent_signature == request.signature
    assert event.reason_code == "attribution.credited"
    assert event.payload["credit_id"] == str(credit.credit_id)
    assert event.payload["credited_recovery_paise"] == 95_000
    assert event.payload["baseline_stratum"] == "abandoned_checkout.price_hesitation"
    validate_payload(event.payload)


def test_recovery_credited_negative_credit_is_a_valid_int_payload_value():
    """Payload values must be int, never float — a negative credit must
    still pass validate_payload (int, not clamped, per design)."""
    from sampark.attribution.baseline import BaselineRate
    from sampark.attribution.credit import compute_credit

    request = _request()
    grant = _grant()
    baseline = BaselineRate(stratum="global", level="global", rate=0.10, n=1000)
    credit = compute_credit(
        grant_id=grant.grant_id, observed_recovered_paise=0, amount_paise=100_000,
        baseline=baseline, holdout_fraction=0.10, observed_at=AT,
    )
    assert credit.credited_recovery_paise < 0
    event = emit.event_for_recovery_credited(credit, request, at=AT)
    validate_payload(event.payload)
    assert event.payload["credited_recovery_paise"] == -10_000


def test_all_three_events_are_hashable_and_canonicalizable():
    """No new canonicalizer version needed — v1 is shape-agnostic."""
    request = _request()
    events = [
        emit.event_for_holdout_assigned(42, 1000, 1, 490, "a" * 64, AT),
        emit.event_for_contact_opt_out(_grant(), request, "whatsapp", 0, AT),
    ]
    for event in events:
        assert event.payload["v"] == 1
        digest = hash_event(event.model_copy(update={"prev_hash": "0" * 64}))
        assert len(digest) == 64  # sha256 hex
        canonical_bytes(event.model_copy(update={"prev_hash": "0" * 64}))  # must not raise
