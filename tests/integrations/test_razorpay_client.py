"""Razorpay integration wrapper — configuration, client construction, and
payment-link request/response shaping.

These tests mock the `razorpay` SDK call. They must never reach the real
Razorpay API — the real test-mode call is a separate, explicitly opt-in
script (scripts/verify_razorpay_payment_link.py), not part of this suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import razorpay
from razorpay.errors import BadRequestError

from sampark.integrations.razorpay import (
    RazorpayConfig,
    RazorpayConfigError,
    RazorpayRequestError,
    build_client,
    create_test_payment_link,
)


# --- RazorpayConfig.from_env ------------------------------------------------

def test_config_from_env_reads_valid_test_credentials(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "shh")

    config = RazorpayConfig.from_env()

    assert config.key_id == "rzp_test_abc123"
    assert config.key_secret == "shh"


def test_config_from_env_rejects_missing_key_id(monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "shh")

    with pytest.raises(RazorpayConfigError):
        RazorpayConfig.from_env()


def test_config_from_env_rejects_missing_key_secret(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc123")
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)

    with pytest.raises(RazorpayConfigError):
        RazorpayConfig.from_env()


def test_config_from_env_rejects_live_mode_key(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "shh")

    with pytest.raises(RazorpayConfigError):
        RazorpayConfig.from_env()


def test_config_error_message_never_contains_the_secret(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_abc123")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "super-secret-value")

    with pytest.raises(RazorpayConfigError) as excinfo:
        RazorpayConfig.from_env()

    assert "super-secret-value" not in str(excinfo.value)


# --- build_client ------------------------------------------------------------

def test_build_client_constructs_razorpay_client():
    config = RazorpayConfig(key_id="rzp_test_abc123", key_secret="shh")

    client = build_client(config)

    assert isinstance(client, razorpay.Client)


# --- create_test_payment_link -------------------------------------------------

def _fake_response(**overrides):
    response = {
        "id": "plink_TESTID123",
        "short_url": "https://rzp.io/i/testlink",
        "status": "created",
        "amount": 100,
        "currency": "INR",
    }
    response.update(overrides)
    return response


def test_create_test_payment_link_sends_expected_request_shape():
    client = MagicMock()
    client.payment_link.create.return_value = _fake_response()

    create_test_payment_link(
        client,
        amount_paise=100,
        description="SAMPARK Phase 0 test",
        reference_id="sampark-phase0-ref",
    )

    client.payment_link.create.assert_called_once_with({
        "amount": 100,
        "currency": "INR",
        "description": "SAMPARK Phase 0 test",
        "reference_id": "sampark-phase0-ref",
        "notify": {"sms": False, "email": False},
    })


def test_create_test_payment_link_disables_notifications():
    client = MagicMock()
    client.payment_link.create.return_value = _fake_response()

    create_test_payment_link(
        client,
        amount_paise=100,
        description="SAMPARK Phase 0 test",
        reference_id="sampark-phase0-ref",
    )

    sent_payload = client.payment_link.create.call_args[0][0]
    assert sent_payload["notify"] == {"sms": False, "email": False}


def test_create_test_payment_link_parses_non_secret_response_fields():
    client = MagicMock()
    client.payment_link.create.return_value = _fake_response(
        id="plink_ABC", short_url="https://rzp.io/i/abc",
        status="created", amount=500, currency="INR",
    )

    result = create_test_payment_link(
        client, amount_paise=500, description="d", reference_id="r",
    )

    assert result.payment_link_id == "plink_ABC"
    assert result.short_url == "https://rzp.io/i/abc"
    assert result.status == "created"
    assert result.amount == 500
    assert result.currency == "INR"


def test_create_test_payment_link_wraps_sdk_errors():
    client = MagicMock()
    client.payment_link.create.side_effect = BadRequestError("bad request")

    with pytest.raises(RazorpayRequestError):
        create_test_payment_link(
            client, amount_paise=100, description="d", reference_id="r",
        )


def test_create_test_payment_link_error_never_contains_the_secret():
    client = MagicMock()
    client.payment_link.create.side_effect = BadRequestError(
        "Authorization failed for key rzp_test_abc123:super-secret-value"
    )

    with pytest.raises(RazorpayRequestError) as excinfo:
        create_test_payment_link(
            client, amount_paise=100, description="d", reference_id="r",
        )

    assert "super-secret-value" not in str(excinfo.value)
