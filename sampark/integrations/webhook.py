"""Razorpay webhook receiver — signature verification and envelope parsing.

--- What is actually validated, stated exactly (CLAUDE.md §14) ---

Razorpay signs a webhook by computing `HMAC-SHA256(raw_request_body,
webhook_secret)` and sending the lowercase hex digest in the
`X-Razorpay-Signature` header. That is the whole mechanism Razorpay offers,
and it is the whole mechanism implemented here. Specifically:

  VERIFIED    the body was produced by someone holding the webhook secret,
              and has not been altered in transit.
  NOT VERIFIED  who sent it (there is no client certificate), when it was
              sent (Razorpay's signature covers no timestamp, so this scheme
              is replay-able by anyone who captured a valid body — which is
              why `idempotency_key` below exists and why the product layer
              refuses a repeat), or that the merchant account matches.

No security mechanism the real Razorpay webhook format does not support is
invented here.

--- Failure modes, all explicit ---

Missing secret, missing header, wrong length, bad hex, digest mismatch,
undecodable body, non-JSON body, unknown event name, and an envelope with no
payment entity are each their own exception. None is silently downgraded to
"accepted", and none produces a `WebhookReceipt` — so an unverified body can
never acquire provenance (`sampark.integrations.provenance`).

`hmac.compare_digest` is used rather than `==`: the comparison is against a
value an attacker controls, and a short-circuiting compare leaks digest
bytes through timing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sampark.integrations.provenance import Provenance, WebhookReceipt

SIGNATURE_HEADER = "x-razorpay-signature"
EVENT_ID_HEADER = "x-razorpay-event-id"

# The only webhook events this adapter acts on. Anything else is ACCEPTED
# (Razorpay retries a non-2xx, and a merchant may legitimately subscribe to
# more events than one adapter consumes) but explicitly IGNORED — never
# guessed at, never coerced into a recovery opportunity.
RECOVERABLE_EVENTS = frozenset({"payment.failed"})
KNOWN_EVENTS = RECOVERABLE_EVENTS | frozenset(
    {"payment.captured", "payment.authorized", "payment_link.paid", "payment_link.expired", "order.paid"}
)


class WebhookConfigError(RuntimeError):
    """RAZORPAY_WEBHOOK_SECRET is not configured, so no body can be verified.
    Raised rather than accepting an unverified body."""


class WebhookVerificationError(RuntimeError):
    """The signature did not verify. The body is discarded."""


class WebhookMalformedError(RuntimeError):
    """The body verified but is not a Razorpay event envelope."""


@dataclass(frozen=True)
class WebhookEnvelope:
    event: str
    razorpay_event_id: str | None
    account_id: str | None
    created_at: datetime | None
    entity: dict[str, Any]  # the payment (or other) entity the event carries
    receipt: WebhookReceipt

    @property
    def is_recoverable(self) -> bool:
        return self.event in RECOVERABLE_EVENTS

    @property
    def idempotency_key(self) -> str:
        """Stable per delivered event.

        Razorpay's own `x-razorpay-event-id` is preferred: it is constant
        across Razorpay's automatic retries of the SAME event, which is
        exactly the duplicate this must collapse. When absent (older
        deliveries, replayed captures), fall back to the event name plus the
        entity id — still stable, still collapses a replayed body."""
        if self.razorpay_event_id:
            return self.razorpay_event_id
        return self.event + ":" + str(self.entity.get("id", "unknown"))

    def provenance(self) -> Provenance:
        return Provenance.from_webhook(
            self.receipt,
            observed_at=datetime.now(timezone.utc),
            reference=str(self.entity.get("id")) if self.entity.get("id") else None,
        )


def webhook_secret() -> str | None:
    return os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip() or None


def webhook_configured() -> bool:
    return webhook_secret() is not None


def expected_signature(raw_body: bytes, secret: str) -> str:
    """Razorpay's scheme, verbatim: lowercase hex HMAC-SHA256 over the RAW
    request body. Exposed so a test can build a genuine signature rather
    than assert against a hard-coded one."""
    return hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, provided: str | None, secret: str | None = None) -> None:
    """Raise unless `provided` is a valid signature over `raw_body`."""
    secret = secret if secret is not None else webhook_secret()
    if not secret:
        raise WebhookConfigError(
            "RAZORPAY_WEBHOOK_SECRET is not set, so this webhook body cannot be "
            "verified. The request is refused rather than trusted."
        )
    if not provided:
        raise WebhookVerificationError("missing " + SIGNATURE_HEADER + " header")
    if not hmac.compare_digest(expected_signature(raw_body, secret), provided.strip().lower()):
        raise WebhookVerificationError("signature does not match the request body")


def verify_and_parse(
    raw_body: bytes, headers: dict[str, str], secret: str | None = None
) -> WebhookEnvelope:
    """Verify, then parse. THE ONLY PLACE A `WebhookReceipt` IS CONSTRUCTED,
    and strictly after `verify_signature` returned without raising."""
    lowered = {key.lower(): value for key, value in headers.items()}
    verify_signature(raw_body, lowered.get(SIGNATURE_HEADER), secret)

    try:
        envelope = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookMalformedError("verified body is not UTF-8 JSON: " + type(exc).__name__) from None
    if not isinstance(envelope, dict):
        raise WebhookMalformedError("verified body is not a JSON object")

    event = envelope.get("event")
    if not isinstance(event, str) or not event:
        raise WebhookMalformedError("envelope carries no `event` name")

    entity = _first_entity(envelope)
    created_at = envelope.get("created_at")

    return WebhookEnvelope(
        event=event,
        razorpay_event_id=lowered.get(EVENT_ID_HEADER) or None,
        account_id=envelope.get("account_id") if isinstance(envelope.get("account_id"), str) else None,
        created_at=(
            datetime.fromtimestamp(created_at, tz=timezone.utc) if isinstance(created_at, int) else None
        ),
        entity=entity,
        receipt=WebhookReceipt(event_name=event, razorpay_event_id=lowered.get(EVENT_ID_HEADER) or None),
    )


def _first_entity(envelope: dict[str, Any]) -> dict[str, Any]:
    """Razorpay nests entities as `payload.<name>.entity`. `contains` names
    which are present; it is followed when usable and the payload keys are
    used directly otherwise — the shape is read, never assumed."""
    payload = envelope.get("payload")
    if not isinstance(payload, dict) or not payload:
        raise WebhookMalformedError("envelope carries no `payload` object")

    contains = envelope.get("contains")
    names = [n for n in contains if isinstance(n, str)] if isinstance(contains, list) else []
    for name in names + sorted(payload):
        wrapper = payload.get(name)
        if isinstance(wrapper, dict) and isinstance(wrapper.get("entity"), dict):
            return wrapper["entity"]
    raise WebhookMalformedError("envelope payload carries no `<name>.entity` object")


__all__ = [
    "EVENT_ID_HEADER",
    "KNOWN_EVENTS",
    "RECOVERABLE_EVENTS",
    "SIGNATURE_HEADER",
    "WebhookConfigError",
    "WebhookEnvelope",
    "WebhookMalformedError",
    "WebhookVerificationError",
    "expected_signature",
    "verify_and_parse",
    "verify_signature",
    "webhook_configured",
    "webhook_secret",
]
