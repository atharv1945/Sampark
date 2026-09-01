"""Razorpay payment entity -> SAMPARK recovery opportunity.

Spec §8.2 already describes this boundary: *"Signals arrive from Razorpay
webhooks (test mode) and the simulator. The normaliser maps each to a
RISK_ITEM with a canonical amount, source, and detected time, then resolves
the owner to one CUSTOMER via hashed phone/email."* This module is that
normaliser for the Razorpay half. The simulator half is `sim/generator.py`
and is untouched.

--- Nothing here is a new domain model ---

The output carries the EXISTING human-owned contracts (`sampark.contracts`'s
`RiskItem`, `Customer`, `ContactState`) built from Razorpay fields. No
parallel payment model is introduced, and no contract is redefined
(CLAUDE.md §3).

--- Root cause is a lookup, not a model (CLAUDE.md §7) ---

`sampark.rootcause.classify` is called with `source="failed_payment"` and a
context code derived by a fixed, documented preference order:

    1. `error_reason`, uppercased   (Razorpay's specific cause, e.g.
                                     `insufficient_funds`)
    2. `error_code`                 (Razorpay's coarse class, e.g.
                                     `BAD_REQUEST_ERROR`)
    3. `unknown`

The committed `sampark/rootcause/taxonomy.yaml` is READ, never extended.
That file's own header records that its context codes were written as
illustrative simulator codes; three of them (`BAD_REQUEST_ERROR`,
`GATEWAY_ERROR`, `SERVER_ERROR`) turn out to be exactly Razorpay's real
`error_code` vocabulary, and two more match real `error_reason` values once
uppercased. Codes it does not map fall to `unknown` — which is not a failure:
`("failed_payment", "unknown")` is a genuine calibrated bucket in
`sampark/allocator/calibrated.py` (p_base 0.2742). Extending the taxonomy
would change a file the committed Phase 1 dataset was generated through, so
it is left alone (CLAUDE.md §2, "do not regenerate committed evidence").

--- Privacy ---

`email` and `contact` are read, hashed by `sampark.identity.resolution`, and
discarded. Neither the returned object, the audit payload, nor any log line
carries a raw phone number or email address (CLAUDE.md §8).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sampark.contracts import ContactState, Customer, RiskItem
from sampark.identity.resolution import ContactSignal, email_hash, phone_hash, resolve_customer_ids
from sampark.integrations.provenance import Provenance
from sampark.rootcause.lookup import classify

# Spec §8.3's risk source for a failed payment. Matches
# `agents/payment_retry.py`'s SOURCE, `sim/generator.py`'s source vocabulary,
# `sampark/allocator/calibrated.py`'s calibrated buckets and
# `sim/arm_b.py`'s `payment_retry_agent` capability scope — the whole
# downstream stack already speaks this string.
RISK_SOURCE = "failed_payment"

# Razorpay payment statuses. Only one of them is a recovery opportunity.
STATUS_FAILED = "failed"
TERMINAL_SUCCESS_STATUSES = frozenset({"captured", "authorized", "refunded"})
IN_FLIGHT_STATUSES = frozenset({"created", "pending"})

# risk_id prefix, so a Razorpay-originated item is distinguishable from a
# generated one at a glance in the ledger and in every audit payload.
RISK_ID_PREFIX = "rzp_"


class NotAPaymentError(ValueError):
    """The object handed in is not a Razorpay payment entity."""


class UnsupportedPaymentStateError(ValueError):
    """A payment whose status this adapter will not act on. Raised rather
    than coerced — a `created` payment has not failed, and inventing a
    recovery opportunity for it would be fabricating a signal."""


@dataclass(frozen=True)
class RecoveryOpportunity:
    """One normalised revenue-at-risk opportunity, ready for the mediation
    layer. Every field is either copied from Razorpay or derived by a pure,
    deterministic function of it."""

    opportunity_id: str
    payment_id: str
    order_id: str | None
    payment_link_id: str | None
    method: str | None
    amount_paise: int
    currency: str
    status: str
    failure_code: str | None
    failure_reason: str | None
    failure_source: str | None
    failure_step: str | None
    context_code: str
    detected_at: datetime
    customer: Customer
    contact_state: ContactState
    risk_item: RiskItem
    provenance: Provenance

    @property
    def customer_id(self) -> str:
        return self.customer.customer_id

    def with_customer_id(self, customer_id: str) -> "RecoveryOpportunity":
        """Rebind this opportunity to an ALREADY-KNOWN customer.

        `resolve_customer_ids` unifies signals across a BATCH — two signals
        sharing a phone or an email hash, transitively. A payment adapter sees
        one signal at a time, so the batch identity is not available at
        normalisation and the id derived here is PROVISIONAL: a pure function
        of this payment's own hash material.

        The ledger closes the gap. `sampark.demo.razorpay_product` looks the
        hashes up against the `customers` table before writing and calls this
        to adopt an existing customer's id when one matches. That is what makes
        spec §8.2's "one human is one row" hold for signals arriving one at a
        time, without ever rewriting an id already in the ledger.

        The hashes are carried over unchanged — they are facts about THIS
        payment, and the adopted id is a fact about the person."""
        return dataclasses.replace(
            self, customer=self.customer.model_copy(update={"customer_id": customer_id})
        )

    @property
    def risk_id(self) -> str:
        return self.risk_item.risk_id

    @property
    def root_cause(self) -> str:
        return self.risk_item.root_cause

    @property
    def amount_inr(self) -> str:
        """Display only. Never used in arithmetic — paise is authoritative."""
        return "{:,.2f}".format(self.amount_paise / 100)

    def as_public_dict(self) -> dict[str, Any]:
        """What the product API returns. Contains no raw contact detail."""
        return {
            "opportunity_id": self.opportunity_id,
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "payment_link_id": self.payment_link_id,
            "method": self.method,
            "amount_paise": self.amount_paise,
            "amount_inr": self.amount_inr,
            "currency": self.currency,
            "status": self.status,
            "failure_code": self.failure_code,
            "failure_reason": self.failure_reason,
            "failure_source": self.failure_source,
            "failure_step": self.failure_step,
            "context_code": self.context_code,
            "root_cause": self.root_cause,
            "source": self.risk_item.source,
            "risk_id": self.risk_id,
            "customer_id": self.customer_id,
            "detected_at": self.detected_at.isoformat(),
            "provenance": self.provenance.as_display(),
        }


def is_recoverable(payment: dict[str, Any]) -> bool:
    return _text(payment.get("status")) == STATUS_FAILED


def context_code_for(payment: dict[str, Any]) -> str:
    """The taxonomy lookup key. Deterministic, total, and documented in the
    module docstring — never a guess."""
    reason = _text(payment.get("error_reason"))
    if reason:
        upper = reason.upper()
        if classify(RISK_SOURCE, upper) != "unknown":
            return upper
    code = _text(payment.get("error_code"))
    return code.upper() if code else "unknown"


def normalize_payment(
    payment: dict[str, Any],
    provenance: Provenance,
    *,
    payment_link_id: str | None = None,
) -> RecoveryOpportunity:
    """Map ONE Razorpay payment entity onto a `RecoveryOpportunity`.

    Raises `NotAPaymentError` for a malformed object and
    `UnsupportedPaymentStateError` for any status other than `failed`. It
    never returns an opportunity for a payment that did not fail."""
    if not isinstance(payment, dict):
        raise NotAPaymentError("expected a Razorpay payment object, got " + type(payment).__name__)

    payment_id = _text(payment.get("id"))
    if not payment_id.startswith("pay_"):
        raise NotAPaymentError("payment entity has no `pay_*` id")

    status = _text(payment.get("status"))
    if not status:
        raise NotAPaymentError("payment " + payment_id + " carries no status")
    if status != STATUS_FAILED:
        raise UnsupportedPaymentStateError(
            "payment " + payment_id + " has status " + repr(status) + "; only "
            + repr(STATUS_FAILED) + " is a recovery opportunity"
        )

    amount = payment.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise NotAPaymentError("payment " + payment_id + " has no positive integer `amount` in paise")

    created_at = payment.get("created_at")
    detected_at = (
        datetime.fromtimestamp(created_at, tz=timezone.utc)
        if isinstance(created_at, int)
        else provenance.observed_at
    )

    context_code = context_code_for(payment)
    root_cause = classify(RISK_SOURCE, context_code)

    customer = _resolve_customer(payment_id, payment)
    risk_item = RiskItem(
        risk_id=RISK_ID_PREFIX + payment_id,
        source=RISK_SOURCE,
        amount_paise=amount,
        root_cause=root_cause,
        detected_at=detected_at,
    )

    return RecoveryOpportunity(
        opportunity_id="opp_" + payment_id,
        payment_id=payment_id,
        order_id=_text(payment.get("order_id")) or None,
        payment_link_id=payment_link_id,
        method=_text(payment.get("method")) or None,
        amount_paise=amount,
        currency=_text(payment.get("currency")) or "INR",
        status=status,
        failure_code=_text(payment.get("error_code")) or None,
        failure_reason=_text(payment.get("error_reason")) or None,
        failure_source=_text(payment.get("error_source")) or None,
        failure_step=_text(payment.get("error_step")) or None,
        context_code=context_code,
        detected_at=detected_at,
        customer=customer,
        contact_state=_fresh_contact_state(),
        risk_item=risk_item,
        provenance=provenance,
    )


def _resolve_customer(payment_id: str, payment: dict[str, Any]) -> Customer:
    """Hash, then resolve — `sampark.identity.resolution` unmodified.

    `resolve_customer_ids` is called with a single signal because one webhook
    delivers one payment. Its deduplication across signals is still the right
    primitive: two payments from the same phone hash resolve to the SAME
    customer_id, since the id is derived from the hash material and not from
    the signal, so a repeat payer is one row in the at-risk ledger."""
    signal = ContactSignal(
        signal_id=payment_id,
        phone_hash=phone_hash(_text(payment.get("contact")) or None),
        email_hash=email_hash(_text(payment.get("email")) or None),
    )
    customer_id = resolve_customer_ids([signal])[payment_id]
    return Customer(customer_id=customer_id, phone_hash=signal.phone_hash, email_hash=signal.email_hash)


def _fresh_contact_state() -> ContactState:
    """A customer SAMPARK has not contacted yet.

    `optouts_by_channel` and `consent_scopes` are `{}` for the same reason
    `sim/ledger.py` sets them so: Razorpay's payment entity carries neither
    fact. `sampark.policy.hard.consent_scope` already reports
    FACT_UNAVAILABLE for exactly this gap rather than interpreting the empty
    dict, so the honest empty value flows into a rule that handles it
    honestly."""
    return ContactState(
        contacts_24h=0,
        contacts_7d=0,
        last_contact_at=None,
        optouts_by_channel={},
        consent_scopes={},
        fatigue_score=0.0,
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "IN_FLIGHT_STATUSES",
    "RISK_ID_PREFIX",
    "RISK_SOURCE",
    "STATUS_FAILED",
    "TERMINAL_SUCCESS_STATUSES",
    "NotAPaymentError",
    "RecoveryOpportunity",
    "UnsupportedPaymentStateError",
    "context_code_for",
    "is_recoverable",
    "normalize_payment",
]
