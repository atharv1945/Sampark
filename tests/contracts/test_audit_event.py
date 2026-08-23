"""AuditEvent — CONTRACTS.md Part 1, extended.

See the module docstring in sampark/contracts/audit_event.py for the
event_type/occurred_at gap between CONTRACTS.md's Part 1 table and
sampark/schema.sql that this implementation resolves and flags.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sampark.contracts import AuditEvent


def _event(**overrides) -> AuditEvent:
    fields = dict(
        event_id=uuid4(),
        event_type="grant_request.denied",
        occurred_at=datetime(2026, 8, 24, 9, 12, tzinfo=timezone.utc),
        prev_hash="0" * 64,
        payload={"request_id": str(uuid4())},
    )
    fields.update(overrides)
    return AuditEvent(**fields)


def test_audit_event_valid_construction():
    event = _event()
    assert event.event_type == "grant_request.denied"


def test_audit_event_agent_signature_is_optional():
    event = _event()
    assert event.agent_signature is None


def test_audit_event_reason_code_is_optional():
    event = _event()
    assert event.reason_code is None


def test_audit_event_accepts_explicit_agent_signature_and_reason_code():
    event = _event(agent_signature="sig-bytes", reason_code="QUIET_HOURS")
    assert event.agent_signature == "sig-bytes"
    assert event.reason_code == "QUIET_HOURS"


def test_audit_event_requires_payload():
    with pytest.raises(ValidationError):
        AuditEvent(
            event_id=uuid4(), event_type="x",
            occurred_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
            prev_hash="0" * 64,
        )


def test_audit_event_rejects_unapproved_extra_field():
    with pytest.raises(ValidationError):
        _event(grant_id=str(uuid4()))
