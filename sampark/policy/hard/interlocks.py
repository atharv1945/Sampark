"""Interlock matrix — Design Lock §5.

A conflict matrix, checked at hard-filter time, encoding mutually
exclusive cross-agent states (spec §8.8). Six rows, kept minimal per
the Design Lock — no speculative rows added.

Each row is data (an `Interlock`), not a bespoke function, so the
matrix can be introspected (Phase 8's chaos panel needs exactly this
shape to "trigger RTO flag on an active cart"). `sampark/policy/hard/__init__.py`
imports the six `evaluate_*` functions below — generated from
`INTERLOCKS`, one source of truth — and places them at their fixed
positions in the ordered hard-filter chain (Design Lock §5.1).

`applies_to(candidate)` gates relevance: a rule that cannot possibly
matter for this candidate's intent (e.g. rto_flag for an sms
payment_retry candidate) reports ADMISSIBLE outright, not
FACT_UNAVAILABLE — FACT_UNAVAILABLE means "this fact would matter here
and we cannot see it," not "this never applies."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sampark.allocator.candidate import Candidate
from sampark.allocator.reason_codes import (
    FACT_UNAVAILABLE_FRAUD_REVIEW,
    FACT_UNAVAILABLE_MANDATE_CANCELLATION,
    FACT_UNAVAILABLE_REFUND_IN_FLIGHT,
    FACT_UNAVAILABLE_RTO_FLAG,
    INTERLOCK_ACTIVE_GRANT_IN_WINDOW,
    INTERLOCK_DISPUTE_OPEN,
)
from sampark.budget.windows import next_window_start
from sampark.policy.types import HardVerdict, PolicyContext

_RETRY_INTENTS = frozenset({"payment_retry", "mandate_retry"})


@dataclass(frozen=True)
class Interlock:
    interlock_id: str
    reason_code: str
    citation: str
    defers: bool  # True = DEFER, False = DENY
    applies_to: Callable[[Candidate], bool]
    condition: Callable[[Candidate, PolicyContext], bool | None]  # None = fact unavailable
    unavailable_reason_code: str | None = None  # required iff condition can return None


def _dispute_open_condition(candidate: Candidate, ctx: PolicyContext) -> bool:
    items = ctx.ledger.risk_items_for_customer(candidate.customer_id)
    return any(item.root_cause == "disputed" for item in items)


def _active_grant_in_window_condition(candidate: Candidate, ctx: PolicyContext) -> bool:
    return ctx.ledger.has_active_claim(candidate.customer_id, candidate.window_id)


def _unavailable(_candidate: Candidate, _ctx: PolicyContext) -> bool | None:
    return None


INTERLOCKS: tuple[Interlock, ...] = (
    Interlock(
        interlock_id="dispute_open",
        reason_code=INTERLOCK_DISPUTE_OPEN,
        citation=(
            "spec §8.8 row 4 (chargeback raised in last 90 days). PROXY: derived "
            "from authoritative RiskItem.root_cause == 'disputed', not a chargeback "
            "flag — no chargeback field exists in this dataset."
        ),
        defers=False,
        applies_to=lambda c: c.request.requested_max_incentive_bps > 0,
        condition=_dispute_open_condition,
    ),
    Interlock(
        interlock_id="rto_flag",
        reason_code="",  # never reached — condition always FACT_UNAVAILABLE when applicable
        citation="spec §8.8 row 1 (RTO Shield flagged the order -> block cart recovery/upsell).",
        defers=False,
        applies_to=lambda c: c.request.intent == "cart_recovery",
        condition=_unavailable,
        unavailable_reason_code=FACT_UNAVAILABLE_RTO_FLAG,
    ),
    Interlock(
        interlock_id="refund_in_flight",
        reason_code="",
        citation="spec §8.8 row 2 (refund issued/in-flight -> block dispute contest/retry).",
        defers=False,
        applies_to=lambda c: c.request.intent in _RETRY_INTENTS,
        condition=_unavailable,
        unavailable_reason_code=FACT_UNAVAILABLE_REFUND_IN_FLIGHT,
    ),
    Interlock(
        interlock_id="fraud_review",
        reason_code="",
        citation="spec §8.8 row 3 (customer in fraud review -> block all promotional contact/incentives).",
        defers=False,
        applies_to=lambda c: c.request.requested_max_incentive_bps > 0,
        condition=_unavailable,
        unavailable_reason_code=FACT_UNAVAILABLE_FRAUD_REVIEW,
    ),
    Interlock(
        interlock_id="mandate_cancellation",
        reason_code="",
        citation="spec §8.8 row 5 (mandate cancellation requested -> block mandate retry).",
        defers=False,
        applies_to=lambda c: c.request.intent == "mandate_retry",
        condition=_unavailable,
        unavailable_reason_code=FACT_UNAVAILABLE_MANDATE_CANCELLATION,
    ),
    Interlock(
        interlock_id="active_grant_in_window",
        reason_code=INTERLOCK_ACTIVE_GRANT_IN_WINDOW,
        citation="spec §8.8 row 6 (grant already active this window -> block every other agent).",
        defers=True,
        applies_to=lambda c: True,
        condition=_active_grant_in_window_condition,
    ),
)


def _make_evaluator(interlock: Interlock) -> Callable[[Candidate, PolicyContext], HardVerdict]:
    def _evaluate(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
        if not interlock.applies_to(candidate):
            return HardVerdict.admissible()
        result = interlock.condition(candidate, ctx)
        if result is None:
            assert interlock.unavailable_reason_code is not None
            return HardVerdict.fact_unavailable(interlock.unavailable_reason_code)
        if not result:
            return HardVerdict.admissible()
        if interlock.defers:
            return HardVerdict.defer(interlock.reason_code, next_window_start(candidate.window_id))
        return HardVerdict.deny(interlock.reason_code)

    return _evaluate


_EVALUATORS: dict[str, Callable[[Candidate, PolicyContext], HardVerdict]] = {
    interlock.interlock_id: _make_evaluator(interlock) for interlock in INTERLOCKS
}

evaluate_dispute_open = _EVALUATORS["dispute_open"]
evaluate_rto_flag = _EVALUATORS["rto_flag"]
evaluate_refund_in_flight = _EVALUATORS["refund_in_flight"]
evaluate_fraud_review = _EVALUATORS["fraud_review"]
evaluate_mandate_cancellation = _EVALUATORS["mandate_cancellation"]
evaluate_active_grant_in_window = _EVALUATORS["active_grant_in_window"]
