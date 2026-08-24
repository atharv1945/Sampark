"""sampark/registry/signing.py — Ed25519 signature verification.

LOCKED DECISIONS: verify_signature must return False for every failure
mode (malformed base64, wrong-length key, wrong-length signature,
tampered message, wrong key) rather than raising.
"""

from __future__ import annotations

import base64
import datetime as dt
from uuid import uuid4

from sampark.contracts import GrantRequest
from sampark.registry import generate_keypair
from sampark.registry.signing import verify_signature


def test_valid_signature_verifies():
    kp = generate_keypair()
    message = b"payload bytes"
    signature = kp.sign(message)
    assert verify_signature(kp.public_key_b64, message, signature) is True


def test_tampered_message_is_rejected():
    kp = generate_keypair()
    signature = kp.sign(b"original")
    assert verify_signature(kp.public_key_b64, b"tampered", signature) is False


def test_wrong_public_key_is_rejected():
    signer = generate_keypair()
    other = generate_keypair()
    message = b"payload"
    signature = signer.sign(message)
    assert verify_signature(other.public_key_b64, message, signature) is False


def test_malformed_base64_public_key_returns_false_not_raise():
    kp = generate_keypair()
    signature = kp.sign(b"payload")
    assert verify_signature("not-valid-base64!!!", b"payload", signature) is False


def test_malformed_base64_signature_returns_false_not_raise():
    kp = generate_keypair()
    assert verify_signature(kp.public_key_b64, b"payload", "not-valid-base64!!!") is False


def test_wrong_length_public_key_returns_false_not_raise():
    short_key = base64.b64encode(b"too-short").decode("ascii")
    kp = generate_keypair()
    signature = kp.sign(b"payload")
    assert verify_signature(short_key, b"payload", signature) is False


def test_wrong_length_signature_returns_false_not_raise():
    kp = generate_keypair()
    short_sig = base64.b64encode(b"too-short").decode("ascii")
    assert verify_signature(kp.public_key_b64, b"payload", short_sig) is False


def test_none_argument_returns_false_not_raise():
    """base64.b64decode(None, ...) and VerifyKey(None) both raise
    TypeError, not ValueError/binascii.Error — a None (or otherwise
    non-string) argument must still return False, not escape as an
    uncaught exception, for any future caller that doesn't guarantee a
    Pydantic-validated str (e.g. raw untrusted input ahead of
    validation). Read-only security review, Phase 3 hardening finding."""
    kp = generate_keypair()
    signature = kp.sign(b"payload")

    assert verify_signature(None, b"payload", signature) is False  # type: ignore[arg-type]
    assert verify_signature(kp.public_key_b64, b"payload", None) is False  # type: ignore[arg-type]


def test_signature_verification_stable_across_equivalent_construction_order():
    """Two GrantRequests built from identical field values via different
    kwarg order must produce identical canonical_bytes(), so a signature
    from one verifies against the other — proving canonicalization, not
    object identity or construction order, is what's actually signed."""
    kp = generate_keypair()
    request_id = uuid4()
    issued_at = dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc)

    a = GrantRequest(
        request_id=request_id, agent_id="agent-1", customer_id="cust-1", risk_id="risk-1",
        intent="recover_cart", requested_channel="sms", requested_max_incentive_bps=100,
        issued_at=issued_at, signature="placeholder-a",
    )
    b = GrantRequest(
        issued_at=issued_at, signature="placeholder-b", requested_max_incentive_bps=100,
        requested_channel="sms", intent="recover_cart", risk_id="risk-1", customer_id="cust-1",
        agent_id="agent-1", request_id=request_id,
    )
    assert a.canonical_bytes() == b.canonical_bytes()

    signature = kp.sign(a.canonical_bytes())
    assert verify_signature(kp.public_key_b64, b.canonical_bytes(), signature) is True


def test_signature_changes_when_a_signed_field_changes():
    kp = generate_keypair()
    issued_at = dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc)
    a = GrantRequest(
        request_id=uuid4(), agent_id="agent-1", customer_id="cust-1", risk_id="risk-1",
        intent="recover_cart", requested_channel="sms", requested_max_incentive_bps=100,
        issued_at=issued_at, signature="placeholder",
    )
    b = a.model_copy(update={"requested_max_incentive_bps": 999})

    signature_a = kp.sign(a.canonical_bytes())
    assert verify_signature(kp.public_key_b64, a.canonical_bytes(), signature_a) is True
    assert verify_signature(kp.public_key_b64, b.canonical_bytes(), signature_a) is False
