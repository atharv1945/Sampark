"""GrantRequest — CONTRACTS.md Part 2, plus the signed-payload boundary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sampark.contracts import GrantRequest


def _request(**overrides) -> GrantRequest:
    fields = dict(
        request_id=uuid4(),
        agent_id="agent-1",
        customer_id="cust-1",
        risk_id="risk-1",
        intent="recover_mandate",
        requested_channel="whatsapp",
        requested_max_incentive_bps=500,
        issued_at=datetime(2026, 8, 24, 9, 12, tzinfo=timezone.utc),
        signature="base64-signature-bytes",
    )
    fields.update(overrides)
    return GrantRequest(**fields)


def test_grant_request_valid_construction():
    request = _request()
    assert request.agent_id == "agent-1"


def test_grant_request_rejects_negative_requested_max_incentive_bps():
    with pytest.raises(ValidationError):
        _request(requested_max_incentive_bps=-1)


def test_grant_request_accepts_zero_requested_max_incentive_bps_boundary():
    request = _request(requested_max_incentive_bps=0)
    assert request.requested_max_incentive_bps == 0


def test_grant_request_rejects_unapproved_extra_field():
    with pytest.raises(ValidationError):
        _request(amount_paise=410000)


def test_grant_request_has_no_status_field():
    with pytest.raises(ValidationError):
        _request(status="PENDING")


def test_canonical_payload_excludes_signature():
    request = _request()
    payload = request.canonical_payload()
    assert "signature" not in payload


def test_canonical_payload_includes_exactly_the_signed_fields():
    request = _request()
    payload = request.canonical_payload()
    assert set(payload) == {
        "request_id", "agent_id", "customer_id", "risk_id", "intent",
        "requested_channel", "requested_max_incentive_bps", "issued_at",
    }


def test_canonical_bytes_is_deterministic_for_identical_signed_fields():
    request_id = uuid4()
    issued_at = datetime(2026, 8, 24, 9, 12, tzinfo=timezone.utc)

    a = _request(request_id=request_id, issued_at=issued_at, signature="sig-a")
    b = _request(request_id=request_id, issued_at=issued_at, signature="sig-b")

    # Different signatures, identical signed fields -> identical signable bytes.
    assert a.canonical_bytes() == b.canonical_bytes()


def test_canonical_bytes_changes_when_a_signed_field_changes():
    a = _request(intent="recover_mandate")
    b = _request(intent="recover_cart")
    assert a.canonical_bytes() != b.canonical_bytes()


def test_canonical_bytes_is_valid_json_matching_canonical_payload():
    request = _request()
    decoded = json.loads(request.canonical_bytes().decode("utf-8"))
    assert decoded == request.canonical_payload()
