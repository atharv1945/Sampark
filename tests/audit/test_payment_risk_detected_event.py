"""`payment.risk_detected` — the one audit event the Razorpay integration adds.

Held to the same standard as every other vocabulary entry: it must be in
EVENT_TYPES, it must be unsigned, it must not be terminal, it must sort where
`sampark.audit.explain` needs it, it must be idempotent on re-ingest, its
payload must pass the canonical/privacy rule, and — the property that makes it
worth adding at all — it must carry the Razorpay provenance so the product
screen's claim is corroborated by the chain rather than asserted beside it.

It must also be INERT with respect to everything Phases 0-9 already prove:
adding a type to a closed vocabulary is only safe if the existing readers do
not change behaviour, and the last group of tests checks exactly that.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.audit import emit
from sampark.audit.canonical import canonical_bytes, hash_event, validate_payload
from sampark.audit.event_types import (
    DECISION_DEFERRED,
    DECISION_DENIED,
    EVENT_TYPES,
    HOLDOUT_ASSIGNED,
    PAYMENT_RISK_DETECTED,
    SIGNED_EVENT_TYPES,
    TERMINAL_EVENT_TYPES,
    TYPE_ORDER,
)
from sampark.integrations.normalize import normalize_payment
from sampark.integrations.provenance import McpCallReceipt, Provenance, RestCallReceipt

AT = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.timezone.utc)
CREATED_AT = 1788000000


def opportunity(transport="mcp", payment_id="pay_EVT00000001", amount=100_000):
    if transport == "mcp":
        provenance = Provenance.from_mcp(
            McpCallReceipt("fetch_payment", "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"),
            observed_at=AT, reference=payment_id,
        )
    else:
        provenance = Provenance.from_rest(
            RestCallReceipt("fetch_payment", "api.razorpay.com"), observed_at=AT, reference=payment_id
        )
    return normalize_payment(
        {
            "id": payment_id, "entity": "payment", "amount": amount, "currency": "INR",
            "status": "failed", "order_id": "order_EVT01", "method": "card",
            "email": "priya@example.com", "contact": "+919876543210",
            "error_code": "GATEWAY_ERROR", "error_reason": "issuer_down",
            "error_source": "bank", "error_step": "payment_authorization",
            "created_at": CREATED_AT,
        },
        provenance,
        payment_link_id="plink_EVT1",
    )


# --- vocabulary -------------------------------------------------------------


def test_the_type_is_in_the_vocabulary_and_is_unsigned():
    assert PAYMENT_RISK_DETECTED in EVENT_TYPES
    assert PAYMENT_RISK_DETECTED not in SIGNED_EVENT_TYPES, (
        "no agent asked for a payment to fail, so there is no signature to attach"
    )
    assert emit.event_for_payment_risk_detected(opportunity()).agent_signature is None


def test_the_type_is_not_terminal():
    """It PRECEDES a request's lifecycle rather than ending one, so treating it
    as terminal would break `sampark.audit.explain`'s validation."""
    assert PAYMENT_RISK_DETECTED not in TERMINAL_EVENT_TYPES


def test_it_sorts_before_every_decision_and_after_holdout_assigned():
    assert TYPE_ORDER[PAYMENT_RISK_DETECTED] < TYPE_ORDER[DECISION_DENIED]
    assert TYPE_ORDER[PAYMENT_RISK_DETECTED] < TYPE_ORDER[DECISION_DEFERRED]
    assert TYPE_ORDER[HOLDOUT_ASSIGNED] < min(
        v for k, v in TYPE_ORDER.items() if k != HOLDOUT_ASSIGNED
    ), "Phase 7 pins holdout.assigned as the unique minimum; this must not weaken it"


# --- the event itself -------------------------------------------------------


def test_it_carries_the_provenance_that_the_product_screen_shows():
    """This is why the type exists. Without it the Razorpay provenance would
    live only in `risk_items` and the UI would be asserting something the
    audit log could not corroborate."""
    event = emit.event_for_payment_risk_detected(opportunity("mcp"))
    assert event.payload["provider"] == "razorpay"
    assert event.payload["environment"] == "test"
    assert event.payload["transport"] == "mcp"
    assert event.payload["operation"] == "fetch_payment"


def test_the_transport_in_the_payload_follows_the_transport_that_ran():
    assert emit.event_for_payment_risk_detected(opportunity("rest")).payload["transport"] == "rest_api"


def test_it_carries_everything_the_hero_card_renders():
    payload = emit.event_for_payment_risk_detected(opportunity()).payload
    for key in ("payment_id", "amount_paise", "root_cause", "failure_code", "method",
                "risk_id", "customer_id", "source", "currency"):
        assert key in payload, "the product page reads " + key + " off this event"
    assert payload["amount_paise"] == 100_000
    assert payload["source"] == "failed_payment"
    assert payload["root_cause"] == "issuer_downtime"


def test_occurred_at_defaults_to_razorpays_own_failure_instant():
    """Not the instant SAMPARK read it. That keeps this event strictly before
    the `request.received` an agent later raises, so the ordering never falls
    through to the TYPE_ORDER tiebreak."""
    opp = opportunity()
    event = emit.event_for_payment_risk_detected(opp)
    assert event.occurred_at == dt.datetime.fromtimestamp(CREATED_AT, tz=dt.timezone.utc)
    assert event.occurred_at == opp.detected_at


def test_the_reason_code_is_a_fixed_literal_not_an_inference():
    """Exactly like ROLLBACK_REASON / EXPIRY_REASON: the adapter only ever
    ingests a payment whose Razorpay status is `failed`, so there is one
    reason and this module does not infer it."""
    assert emit.PAYMENT_FAILED_REASON == "razorpay.payment_failed"
    assert emit.event_for_payment_risk_detected(opportunity()).reason_code == emit.PAYMENT_FAILED_REASON


# --- idempotency ------------------------------------------------------------


def test_re_ingesting_one_payment_derives_the_same_event_id():
    """A webhook retry, a second poll, or a judge pressing the button twice
    must re-derive one id, so `chain.append` returns AlreadyAppended and the
    chain never records one payment twice."""
    a = emit.event_for_payment_risk_detected(opportunity())
    b = emit.event_for_payment_risk_detected(opportunity())
    assert a.event_id == b.event_id
    assert canonical_bytes(a) == canonical_bytes(b)
    assert hash_event(a) == hash_event(b)


def test_two_different_payments_get_different_event_ids():
    a = emit.event_for_payment_risk_detected(opportunity(payment_id="pay_AAAAAAAAAAA1"))
    b = emit.event_for_payment_risk_detected(opportunity(payment_id="pay_BBBBBBBBBBB2"))
    assert a.event_id != b.event_id


# --- canonicalization + privacy ---------------------------------------------


def test_the_payload_passes_the_canonical_and_privacy_rule():
    event = emit.event_for_payment_risk_detected(opportunity())
    validate_payload(event.payload)  # raises on a float, non-ASCII, or free text
    assert len(hash_event(event)) == 64


def test_no_raw_contact_detail_reaches_the_payload():
    blob = repr(emit.event_for_payment_risk_detected(opportunity()).payload)
    for secret in ("priya@example.com", "9876543210", "+91"):
        assert secret not in blob


def test_it_carries_no_request_id_and_no_window_id():
    """Deliberate. `sampark.audit.store.events_for_request` matches on
    `payload.request_id` and `events_for_customer_window` on
    (`customer_id`, `window_id`) — carrying either would pull this event into
    a timeline `sampark.audit.explain` reconstructs, where it has no place."""
    payload = emit.event_for_payment_risk_detected(opportunity()).payload
    assert "request_id" not in payload
    assert "window_id" not in payload


# --- inertness with respect to everything already proved --------------------


def test_explain_request_is_unaffected_by_the_new_type():
    """`explain_request` requires a single-request timeline. A
    payment.risk_detected event carries no request_id, so it can never be
    returned by `events_for_request` — and if one were passed in by hand it
    must not be mistaken for a lifecycle step."""
    from sampark.audit.explain import _GRANT_LIFECYCLE_TYPES

    assert PAYMENT_RISK_DETECTED not in _GRANT_LIFECYCLE_TYPES


def test_the_new_type_does_not_disturb_the_existing_type_order_values():
    """Every pre-existing rank is unchanged. A shifted rank would reorder
    same-instant events in committed Phase 5-9 explanations."""
    expected = {
        "holdout.assigned": -1, "model.degraded": 0, "request.received": 0,
        "request.denied_on_scope": 1, "decision.denied": 1, "decision.deferred": 1,
        "grant.reserved": 2, "grant.executing": 3, "grant.confirmed": 4,
        "grant.rolled_back": 4, "grant.expired": 4, "contact.opt_out": 5,
        "recovery.credited": 5,
    }
    for event_type, rank in expected.items():
        assert TYPE_ORDER[event_type] == rank, event_type + " changed rank"


def test_the_emitter_stays_copy_only_and_imports_no_decision_machinery():
    """`sampark.audit.emit` may only copy fields off objects other layers
    already produced. The new function must not have introduced a runtime
    import of the integration package, or of any scorer or policy module."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent.parent / "sampark/audit/emit.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    runtime_imports: set[str] = set()
    type_checking_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.If):  # `if TYPE_CHECKING:`
            for sub in ast.walk(node):
                if isinstance(sub, ast.ImportFrom) and sub.module:
                    type_checking_imports.add(sub.module)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            runtime_imports.add(node.module)

    assert "sampark.integrations.normalize" in type_checking_imports, (
        "RecoveryOpportunity must be a TYPE_CHECKING-only import"
    )
    assert not any(m.startswith("sampark.integrations") for m in runtime_imports), (
        "emit.py acquired a runtime dependency on the integration package"
    )
    for forbidden in ("sampark.policy", "sampark.allocator.scoring", "sampark.allocator.greedy"):
        assert not any(m.startswith(forbidden) for m in runtime_imports), forbidden
