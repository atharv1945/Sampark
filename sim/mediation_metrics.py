"""Compliance metrics — Design Lock §13.6, §14.

ONE set of predicates, TWO modes: Arm A's executed-contact stream is
scored in OBSERVATION-ONLY mode (Arm A is never mediated; these
predicates never influenced which contacts it sent) — Arm B's is
scored the same way as a corroborating check that enforcement actually
worked. This keeps compliance measurement to a single code path rather
than one Arm-A-shaped implementation and one Arm-B-shaped one.

Every count here is derived purely from `ContactOutcome` (the executed-
contact record both arms already produce) plus the authoritative
ledger's risk items — never from GrantDecision internals, so Arm A
(which has no GrantDecision at all) and Arm B are measured identically.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from agents.cart_recovery import INTENT as _CART_RECOVERY_INTENT
from agents.mandate_recovery import INTENT as _MANDATE_RETRY_INTENT
from agents.payment_retry import INTENT as _PAYMENT_RETRY_INTENT
from agents.receivables import INTENT as _RECEIVABLES_INTENT
from agents.types import ContactOutcome
from sampark.allocator.constants import CONTACT_CAP_24H, CONTACT_CAP_7D
from sampark.budget.windows import is_quiet_hours, window_id_for
from sampark.contracts import GrantDecision, RiskItem

INTENT_BY_AGENT_ID: dict[str, str] = {
    "payment_retry_agent": _PAYMENT_RETRY_INTENT,
    "cart_recovery_agent": _CART_RECOVERY_INTENT,
    "mandate_recovery_agent": _MANDATE_RETRY_INTENT,
    "receivables_agent": _RECEIVABLES_INTENT,
}

_RETRY_INTENTS = frozenset({_PAYMENT_RETRY_INTENT, _MANDATE_RETRY_INTENT})


@dataclass(frozen=True)
class ContactRecord:
    customer_id: str
    risk_item: RiskItem
    intent: str
    incentive_bps: int
    send_after: datetime  # see ContactOutcome.contacted_at


def build_contact_records(
    outcomes: Sequence[ContactOutcome], risk_items_by_id: Mapping[str, RiskItem]
) -> tuple[ContactRecord, ...]:
    return tuple(
        ContactRecord(
            customer_id=outcome.customer_id,
            risk_item=risk_items_by_id[outcome.risk_id],
            intent=INTENT_BY_AGENT_ID[outcome.agent_id],
            incentive_bps=outcome.incentive_bps,
            send_after=outcome.contacted_at,
        )
        for outcome in outcomes
    )


def compute_compliance_metrics(
    records: Sequence[ContactRecord], risk_items_by_customer: Mapping[str, tuple[RiskItem, ...]]
) -> dict:
    by_customer: dict[str, list[ContactRecord]] = collections.defaultdict(list)
    for record in records:
        by_customer[record.customer_id].append(record)

    quiet_hour_violations = sum(1 for r in records if is_quiet_hours(r.send_after))

    cap_24h_breaches = 0
    cap_7d_breaches = 0
    customers_3plus_in_24h: set[str] = set()
    conflicting_action_incidents = 0  # >1 contact, same customer, same IST window

    for customer_id, contacts in by_customer.items():
        contacts_sorted = sorted(contacts, key=lambda r: r.send_after)
        history: list[datetime] = []  # send_after values of prior contacts, in order
        for record in contacts_sorted:
            in_24h = sum(1 for t in history if record.send_after - t < timedelta(hours=24))
            in_7d = sum(1 for t in history if record.send_after - t < timedelta(days=7))
            if in_24h >= CONTACT_CAP_24H:
                cap_24h_breaches += 1
            if in_7d >= CONTACT_CAP_7D:
                cap_7d_breaches += 1
            if in_24h + 1 >= 3:
                customers_3plus_in_24h.add(customer_id)
            history.append(record.send_after)

        by_window: dict[object, int] = collections.defaultdict(int)
        for record in contacts_sorted:
            by_window[window_id_for(record.send_after)] += 1
        conflicting_action_incidents += sum(max(0, n - 1) for n in by_window.values())

    dispute_open_violations = 0
    for record in records:
        if record.incentive_bps <= 0:
            continue
        customer_items = risk_items_by_customer.get(record.customer_id, ())
        if any(item.root_cause == "disputed" for item in customer_items):
            dispute_open_violations += 1

    fact_unavailable_counts = {
        "fact_unavailable.rto_flag": sum(1 for r in records if r.intent == _CART_RECOVERY_INTENT),
        "fact_unavailable.refund_in_flight": sum(1 for r in records if r.intent in _RETRY_INTENTS),
        "fact_unavailable.fraud_review": sum(1 for r in records if r.incentive_bps > 0),
        "fact_unavailable.mandate_cancellation": sum(1 for r in records if r.intent == _MANDATE_RETRY_INTENT),
        # consent_scope is unconditional (Design Lock §4.3) — applies to every contact.
        "fact_unavailable.consent_scope": len(records),
    }

    return {
        "quiet_hour_violations": quiet_hour_violations,
        "contact_cap_24h_breaches": cap_24h_breaches,
        "contact_cap_7d_breaches": cap_7d_breaches,
        "customers_contacted_3plus_per_24h": len(customers_3plus_in_24h),
        "conflicting_action_incidents": conflicting_action_incidents,
        "interlock_dispute_open_violations": dispute_open_violations,
        "fact_unavailable_counts": fact_unavailable_counts,
        "post_optout_contacts": None,  # not measurable — no opt-out data in this dataset, Design Lock §4.3
        "consent_scope_violations": None,  # not measurable — same reason
    }


def scope_violation_count(decisions: Sequence[GrantDecision]) -> int:
    """Count of Phase 4 GrantDecisions carrying a scope.* reason_code —
    always 0 by construction (Design Lock: the allocator never sees a
    scope-denied request), kept as an explicit metric so the "0/0" row
    the spec expects is measured, not merely asserted."""
    return sum(
        1
        for d in decisions
        if d.reason_code is not None and d.reason_code.startswith("scope.")
    )
