"""Razorpay webhook verification and envelope parsing.

The security property is narrow and worth stating precisely, because the
tests assert exactly it and nothing more: a verified body was produced by
someone holding the webhook secret and was not altered in transit. It does
NOT prove who sent it or when, so the replay case is covered here by the
idempotency key rather than by a timestamp check Razorpay's scheme does not
carry.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from sampark.integrations import webhook
from sampark.integrations.provenance import Transport

SECRET = "a-local-demo-webhook-secret"


def envelope(event="payment.failed", status="failed", payment_id="pay_HOOK00000001") -> dict:
    return {
        "entity": "event",
        "account_id": "acc_TEST",
        "event": event,
        "contains": ["payment"],
        "created_at": 1788000000,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id, "entity": "payment", "amount": 100_000, "currency": "INR",
                    "status": status, "order_id": "order_HOOK01", "method": "upi",
                    "email": "hook@example.com", "contact": "+919999900000",
                    "error_code": "BAD_REQUEST_ERROR", "error_reason": "payment_failed",
                    "error_source": "customer", "error_step": "payment_authentication",
                    "created_at": 1788000000,
                }
            }
        },
    }


def body_of(env: dict) -> bytes:
    return json.dumps(env).encode("utf-8")


def signed_headers(raw: bytes, secret: str = SECRET, event_id: str | None = "evt_TEST01") -> dict:
    headers = {webhook.SIGNATURE_HEADER: webhook.expected_signature(raw, secret)}
    if event_id:
        headers[webhook.EVENT_ID_HEADER] = event_id
    return headers


# --- the signature scheme itself --------------------------------------------


def test_expected_signature_is_razorpays_scheme_verbatim():
    """Independently recomputed here, so this test would still catch a change
    to `expected_signature` that broke compatibility with Razorpay."""
    raw = body_of(envelope())
    assert webhook.expected_signature(raw, SECRET) == hmac.new(
        SECRET.encode("utf-8"), raw, hashlib.sha256
    ).hexdigest()


def test_the_signature_covers_the_raw_body_byte_for_byte():
    raw = body_of(envelope())
    tampered = raw.replace(b'"amount": 100000', b'"amount": 999999')
    assert tampered != raw
    with pytest.raises(webhook.WebhookVerificationError):
        webhook.verify_and_parse(tampered, signed_headers(raw), SECRET)


def test_verification_uses_a_constant_time_compare():
    """The comparison is against a value an attacker controls; a
    short-circuiting `==` leaks digest bytes through timing."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent.parent / "sampark/integrations/webhook.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "compare_digest" in calls


# --- accepted, rejected, malformed ------------------------------------------


def test_a_correctly_signed_payment_failed_event_is_accepted():
    env = envelope()
    raw = body_of(env)
    parsed = webhook.verify_and_parse(raw, signed_headers(raw), SECRET)
    assert parsed.event == "payment.failed"
    assert parsed.is_recoverable
    assert parsed.entity["id"] == "pay_HOOK00000001"
    assert parsed.razorpay_event_id == "evt_TEST01"
    assert parsed.provenance().transport is Transport.WEBHOOK


def test_header_names_are_matched_case_insensitively():
    """HTTP header names are case-insensitive and real clients vary."""
    raw = body_of(envelope())
    headers = {
        "X-Razorpay-Signature": webhook.expected_signature(raw, SECRET),
        "X-RAZORPAY-EVENT-ID": "evt_UPPER",
    }
    assert webhook.verify_and_parse(raw, headers, SECRET).razorpay_event_id == "evt_UPPER"


def test_a_missing_signature_header_is_refused():
    raw = body_of(envelope())
    with pytest.raises(webhook.WebhookVerificationError):
        webhook.verify_and_parse(raw, {}, SECRET)


def test_a_wrong_secret_is_refused():
    raw = body_of(envelope())
    with pytest.raises(webhook.WebhookVerificationError):
        webhook.verify_and_parse(raw, signed_headers(raw, "the-wrong-secret"), SECRET)


@pytest.mark.parametrize("bad", ["", "not-hex", "ab" * 31, "AB" * 32])
def test_a_malformed_signature_is_refused_without_raising_anything_else(bad):
    raw = body_of(envelope())
    with pytest.raises(webhook.WebhookVerificationError):
        webhook.verify_and_parse(raw, {webhook.SIGNATURE_HEADER: bad}, SECRET)


def test_an_unconfigured_secret_refuses_rather_than_trusting_the_body(monkeypatch):
    """`secret=None` means "read RAZORPAY_WEBHOOK_SECRET from the
    environment". With nothing there, the body is REFUSED — never accepted
    unverified, which would be the one genuinely dangerous default."""
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    assert webhook.webhook_configured() is False
    raw = body_of(envelope())
    with pytest.raises(webhook.WebhookConfigError):
        webhook.verify_signature(raw, "anything")
    with pytest.raises(webhook.WebhookConfigError):
        webhook.verify_and_parse(raw, signed_headers(raw))


@pytest.mark.parametrize(
    "raw",
    [
        b"not json at all",
        b"[]",
        b'{"payload": {}}',
        b'{"event": "payment.failed"}',
        b'{"event": "payment.failed", "payload": {"payment": {}}}',
        b'{"event": "", "payload": {"payment": {"entity": {}}}}',
        b"\xff\xfe\x00",
    ],
)
def test_a_verified_but_malformed_body_is_reported_as_malformed(raw):
    """It verified, so it really came from Razorpay's secret holder — but it
    is not an event envelope, and that is a different failure from a forged
    body. The two must not collapse into one status code."""
    with pytest.raises(webhook.WebhookMalformedError):
        webhook.verify_and_parse(raw, signed_headers(raw), SECRET)


def test_an_event_this_adapter_does_not_act_on_parses_but_is_not_recoverable():
    env = envelope(event="payment.captured", status="captured")
    raw = body_of(env)
    parsed = webhook.verify_and_parse(raw, signed_headers(raw), SECRET)
    assert parsed.event == "payment.captured"
    assert not parsed.is_recoverable


# --- idempotency ------------------------------------------------------------


def test_the_idempotency_key_is_razorpays_event_id_when_present():
    raw = body_of(envelope())
    parsed = webhook.verify_and_parse(raw, signed_headers(raw, event_id="evt_ABC"), SECRET)
    assert parsed.idempotency_key == "evt_ABC"


def test_the_idempotency_key_falls_back_to_event_plus_entity_id():
    raw = body_of(envelope())
    parsed = webhook.verify_and_parse(raw, signed_headers(raw, event_id=None), SECRET)
    assert parsed.idempotency_key == "payment.failed:pay_HOOK00000001"


def test_two_deliveries_of_the_same_event_share_one_idempotency_key():
    """This is what collapses a Razorpay retry. The two bodies are byte
    identical and carry the same event id, so the key must match."""
    raw = body_of(envelope())
    a = webhook.verify_and_parse(raw, signed_headers(raw), SECRET)
    b = webhook.verify_and_parse(raw, signed_headers(raw), SECRET)
    assert a.idempotency_key == b.idempotency_key


def test_two_different_payments_do_not_share_an_idempotency_key():
    for event_id in (None,):
        a_raw = body_of(envelope(payment_id="pay_AAAAAAAAAAA1"))
        b_raw = body_of(envelope(payment_id="pay_BBBBBBBBBBB2"))
        a = webhook.verify_and_parse(a_raw, signed_headers(a_raw, event_id=event_id), SECRET)
        b = webhook.verify_and_parse(b_raw, signed_headers(b_raw, event_id=event_id), SECRET)
        assert a.idempotency_key != b.idempotency_key


# --- entity extraction ------------------------------------------------------


def test_the_entity_is_read_from_the_payload_shape_not_assumed():
    """`contains` is followed when usable; when it names something absent the
    payload keys are used directly. Neither shape is assumed."""
    env = envelope()
    env["contains"] = ["something_else"]
    raw = body_of(env)
    parsed = webhook.verify_and_parse(raw, signed_headers(raw), SECRET)
    assert parsed.entity["id"] == "pay_HOOK00000001"
