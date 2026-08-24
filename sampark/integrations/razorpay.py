"""Razorpay test-mode Payment Link integration — Phase 0 exit criterion.

Minimal wrapper around the official `razorpay` SDK. Scope is intentionally
narrow: build a client from environment credentials and create a Standard
Payment Link (POST /v1/payment_links). No other Razorpay operation, and no
FastAPI/persistence/allocator wiring, is implemented here — this module is
not where SAMPARK's mediation logic lives (CLAUDE.md §10).

Credentials come ONLY from RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in the
environment. Never printed, never logged, never embedded in an exception
message (CLAUDE.md §8).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import razorpay
from razorpay.errors import BadRequestError, GatewayError, ServerError


class RazorpayConfigError(RuntimeError):
    """Required Razorpay credentials are missing or are not test-mode."""


class RazorpayRequestError(RuntimeError):
    """The Razorpay API rejected or failed the payment-link request."""


@dataclass(frozen=True)
class RazorpayConfig:
    key_id: str
    key_secret: str

    @classmethod
    def from_env(cls) -> "RazorpayConfig":
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")

        if not key_id or not key_secret:
            raise RazorpayConfigError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must both be set "
                "in the environment."
            )
        if not key_id.startswith("rzp_test_"):
            raise RazorpayConfigError(
                "RAZORPAY_KEY_ID must be a test-mode key (rzp_test_*). "
                "Live-mode keys are not permitted here (CLAUDE.md §8)."
            )
        return cls(key_id=key_id, key_secret=key_secret)


def build_client(config: RazorpayConfig) -> razorpay.Client:
    return razorpay.Client(auth=(config.key_id, config.key_secret))


@dataclass(frozen=True)
class PaymentLinkResult:
    payment_link_id: str
    short_url: str
    status: str
    amount: int
    currency: str


def create_test_payment_link(
    client: razorpay.Client,
    *,
    amount_paise: int,
    description: str,
    reference_id: str,
) -> PaymentLinkResult:
    """Create one Standard Payment Link (POST /v1/payment_links).

    `notify` is explicitly disabled: SAMPARK's channel adapters are mocked
    on purpose (CLAUDE.md §8), and this call must never trigger a real SMS
    or email to a real customer.
    """
    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "reference_id": reference_id,
        "notify": {"sms": False, "email": False},
    }
    try:
        response = client.payment_link.create(payload)
    except (BadRequestError, GatewayError, ServerError) as exc:
        raise RazorpayRequestError(
            f"Razorpay payment link creation failed: {type(exc).__name__}"
        ) from exc

    return _parse_response(response)


def _parse_response(response: dict[str, Any]) -> PaymentLinkResult:
    return PaymentLinkResult(
        payment_link_id=response["id"],
        short_url=response["short_url"],
        status=response["status"],
        amount=response["amount"],
        currency=response["currency"],
    )
