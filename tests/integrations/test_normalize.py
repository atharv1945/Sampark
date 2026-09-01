"""Razorpay payment entity -> RecoveryOpportunity.

Covers normal behaviour, invalid input, boundary conditions and the two
properties this adapter is actually load-bearing for: that a non-failed
payment can never become a recovery opportunity, and that no raw contact
detail survives normalisation.
"""

from __future__ import annotations

import datetime as dt

import pytest

from sampark.integrations.normalize import (
    RISK_ID_PREFIX,
    RISK_SOURCE,
    NotAPaymentError,
    UnsupportedPaymentStateError,
    context_code_for,
    is_recoverable,
    normalize_payment,
)
from sampark.integrations.provenance import Provenance, RestCallReceipt

AT = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.timezone.utc)
CREATED_AT = 1788000000  # a fixed unix instant, so `detected_at` is deterministic

RAW_EMAIL = "Priya.Sharma@Example.com"
RAW_PHONE = "+91 98765 43210"


def provenance() -> Provenance:
    return Provenance.from_rest(
        RestCallReceipt(operation="fetch_payment", endpoint_host="api.razorpay.com"),
        observed_at=AT, reference="pay_TEST",
    )


def payment(**overrides) -> dict:
    base = {
        "id": "pay_TEST0000000001", "entity": "payment", "amount": 100_000, "currency": "INR",
        "status": "failed", "order_id": "order_TEST01", "method": "card",
        "email": RAW_EMAIL, "contact": RAW_PHONE,
        "error_code": "BAD_REQUEST_ERROR", "error_description": "Payment processing failed",
        "error_source": "customer", "error_step": "payment_authentication",
        "error_reason": "payment_failed", "created_at": CREATED_AT,
    }
    base.update(overrides)
    return base


# --- normal behaviour -------------------------------------------------------


def test_a_failed_payment_becomes_a_risk_item_of_the_existing_source():
    opp = normalize_payment(payment(), provenance(), payment_link_id="plink_X")
    assert opp.risk_item.source == RISK_SOURCE == "failed_payment"
    assert opp.risk_item.risk_id == RISK_ID_PREFIX + "pay_TEST0000000001"
    assert opp.risk_item.amount_paise == 100_000
    assert opp.amount_inr == "1,000.00"
    assert opp.payment_link_id == "plink_X"
    assert opp.detected_at == dt.datetime.fromtimestamp(CREATED_AT, tz=dt.timezone.utc)


def test_the_risk_source_is_one_the_whole_downstream_stack_already_speaks():
    """Nothing was widened to admit a Razorpay payment: `failed_payment` is
    already the source `agents/payment_retry.py` handles, `sim/arm_b.py`'s
    `payment_retry_agent` scope allows, and `calibrated.py` has priors for."""
    from agents.payment_retry import SOURCE
    from sampark.allocator.calibrated import P_BASE_SOURCE_FALLBACK
    from sim.arm_b import _AGENT_SCOPES

    assert SOURCE == RISK_SOURCE
    assert RISK_SOURCE in P_BASE_SOURCE_FALLBACK
    assert RISK_SOURCE in _AGENT_SCOPES["payment_retry_agent"].allowed_risk_sources


def test_detected_at_falls_back_to_the_observation_instant_when_created_at_is_absent():
    opp = normalize_payment(payment(created_at=None), provenance())
    assert opp.detected_at == AT


# --- root cause: a deterministic lookup, never a model ----------------------


@pytest.mark.parametrize(
    "error_reason,error_code,expected_context,expected_root",
    [
        # error_reason wins when the committed taxonomy maps it.
        ("insufficient_funds", "BAD_REQUEST_ERROR", "INSUFFICIENT_FUNDS", "insufficient_funds"),
        # error_reason present but unmapped -> fall through to error_code.
        ("payment_failed", "BAD_REQUEST_ERROR", "BAD_REQUEST_ERROR", "authentication_drop"),
        ("issuer_down_temporarily", "GATEWAY_ERROR", "GATEWAY_ERROR", "issuer_downtime"),
        ("something_new", "SERVER_ERROR", "SERVER_ERROR", "issuer_downtime"),
        # Neither maps -> `unknown`, which is a REAL calibrated bucket.
        (None, "SOME_FUTURE_CODE", "SOME_FUTURE_CODE", "unknown"),
        (None, None, "unknown", "unknown"),
    ],
)
def test_context_code_and_root_cause_follow_the_documented_preference_order(
    error_reason, error_code, expected_context, expected_root
):
    p = payment(error_reason=error_reason, error_code=error_code)
    assert context_code_for(p) == expected_context
    assert normalize_payment(p, provenance()).root_cause == expected_root


def test_an_unmapped_code_resolves_to_a_calibrated_bucket_not_an_error():
    """`unknown` is not a failure mode: ("failed_payment", "unknown") has a
    real p_base in the frozen calibration, so an unrecognised Razorpay code
    still scores honestly rather than crashing or being guessed at."""
    from sampark.allocator.calibrated import p_base

    opp = normalize_payment(payment(error_reason=None, error_code="WHOLLY_NEW"), provenance())
    assert opp.root_cause == "unknown"
    assert p_base("failed_payment", "unknown") > 0


def test_the_committed_taxonomy_file_is_read_and_never_extended_here():
    """Extending `sampark/rootcause/taxonomy.yaml` would change a file the
    committed Phase 1 dataset was generated through. This module must only
    read it."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent.parent
        / "sampark/integrations/normalize.py"
    ).read_text(encoding="utf-8")
    assert "taxonomy.yaml" not in source.replace("`sampark/rootcause/taxonomy.yaml`", "")
    assert "write_text" not in source and "yaml.dump" not in source


# --- privacy ----------------------------------------------------------------


def test_no_raw_contact_detail_survives_normalisation():
    opp = normalize_payment(payment(), provenance())
    blob = repr(opp) + repr(opp.as_public_dict())
    for secret in (RAW_EMAIL, RAW_EMAIL.lower(), RAW_PHONE, "9876543210"):
        assert secret not in blob, "raw contact detail leaked: " + secret
    assert opp.customer.phone_hash and len(opp.customer.phone_hash) == 64
    assert opp.customer.email_hash and len(opp.customer.email_hash) == 64


def test_the_same_person_across_two_payments_resolves_to_one_customer():
    """"One human is one row" (spec §8.2). Two different payments carrying the
    same phone number must land on the same customer_id, which is what makes
    the at-risk ledger unified rather than per-payment."""
    a = normalize_payment(payment(id="pay_AAAAAAAAAAAA01"), provenance())
    b = normalize_payment(payment(id="pay_BBBBBBBBBBBB02"), provenance())
    assert a.customer_id == b.customer_id
    assert a.risk_id != b.risk_id


def test_a_payment_with_no_contact_details_still_gets_a_stable_customer_id():
    opp = normalize_payment(payment(email=None, contact=None), provenance())
    assert opp.customer_id.startswith("cust_")
    assert opp.customer.phone_hash is None and opp.customer.email_hash is None


# --- invalid input and unsupported states -----------------------------------


@pytest.mark.parametrize("status", ["captured", "authorized", "created", "refunded", "pending"])
def test_a_non_failed_payment_is_never_a_recovery_opportunity(status):
    p = payment(status=status)
    assert not is_recoverable(p)
    with pytest.raises(UnsupportedPaymentStateError):
        normalize_payment(p, provenance())


@pytest.mark.parametrize(
    "broken",
    [
        {"id": "not_a_payment_id"},
        {"id": None},
        {"status": None},
        {"amount": 0},
        {"amount": -100},
        {"amount": "1000"},
        {"amount": None},
    ],
)
def test_a_malformed_payment_is_refused_rather_than_coerced(broken):
    with pytest.raises((NotAPaymentError, UnsupportedPaymentStateError)):
        normalize_payment(payment(**broken), provenance())


@pytest.mark.parametrize("not_a_dict", [None, [], "pay_X", 42])
def test_a_non_object_is_refused(not_a_dict):
    with pytest.raises(NotAPaymentError):
        normalize_payment(not_a_dict, provenance())


def test_normalisation_is_deterministic():
    """Same payment in, byte-identical opportunity out — the property the
    audit chain's deterministic event_id depends on."""
    a = normalize_payment(payment(), provenance())
    b = normalize_payment(payment(), provenance())
    assert a.as_public_dict() == b.as_public_dict()
