"""The Project layer — Phase 5A §1.2, §9. Pure functions of `AuditEvent`
sequences. No ledger, no database, no `sampark.policy`, no
`sampark.allocator` evaluator of any kind (only `sampark.contracts` for
type hints, which is data, not logic). If a fact is not IN the events
handed to these functions, it is not in the explanation — that is the
enforcement mechanism for "reconstructable from the log alone"
(spec §18.1's Phase 5 exit criterion), not a convention.

Two entry points:

    explain_request(events)          — one request's full timeline
    explain_contested_window(events) — one (customer, window) allocation
                                        round's full contested set
                                        (Phase 5A §9.2 competitor
                                        reconstruction)

Both raise `IncompleteLogError` rather than silently inventing a missing
fact — e.g. a grant.executing event with no antecedent grant.reserved for
the same grant_id is contradictory, not a gap to paper over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from sampark.audit.event_types import (
    DECISION_DEFERRED,
    DECISION_DENIED,
    GRANT_CONFIRMED,
    GRANT_EXECUTING,
    GRANT_EXPIRED,
    GRANT_RESERVED,
    GRANT_ROLLED_BACK,
    REQUEST_DENIED_ON_SCOPE,
    REQUEST_RECEIVED,
    TYPE_ORDER,
)
from sampark.contracts import AuditEvent

_GRANT_LIFECYCLE_TYPES = frozenset({GRANT_EXECUTING, GRANT_CONFIRMED, GRANT_ROLLED_BACK, GRANT_EXPIRED})

Outcome = Literal["GRANTED", "DENIED", "DEFERRED", "PENDING"]


class IncompleteLogError(RuntimeError):
    """The event set is inconsistent with any legal Phase 4 lifecycle
    (Phase 5A §2.2) — e.g. a grant lifecycle step with no antecedent
    grant.reserved, more than one grant.reserved for one request, or a
    request with no request.received at all. Raised instead of guessing
    a fact the log does not actually contain."""


@dataclass(frozen=True)
class RiskSummary:
    risk_id: str
    amount_paise: int | None  # None if no event in this timeline reached a Candidate (e.g. scope denial)


@dataclass(frozen=True)
class RequestedTerms:
    intent: str
    requested_channel: str
    requested_max_incentive_bps: int


@dataclass(frozen=True)
class ScopeResult:
    passed: bool
    reason_code: str | None  # the scope.* code, if denied


@dataclass(frozen=True)
class PolicyResult:
    reason_code: str | None  # decisive reason_code on the terminal decision event, or None if never denied/deferred
    fact_unavailable_reason_codes: tuple[str, ...]
    window_id: str | None
    windows_deferred: int | None
    next_eligible_at: str | None
    expected_net_paise: int | None


@dataclass(frozen=True)
class GrantSummary:
    grant_id: str
    channel: str
    incentive_ceiling_paise: int
    effective_incentive_bps: int | None
    send_after: str
    expires_at: str


@dataclass(frozen=True)
class LifecycleStep:
    event_type: str
    occurred_at: datetime
    reason_code: str | None


@dataclass(frozen=True)
class DecisionExplanation:
    request_id: str
    agent_id: str
    authenticated: bool  # a request.received event exists in this timeline
    customer_id: str
    risk: RiskSummary
    requested: RequestedTerms
    scope_result: ScopeResult
    policy_result: PolicyResult | None  # None if scope-denied (allocator never ran)
    grant: GrantSummary | None
    lifecycle: tuple[LifecycleStep, ...]
    outcome: Outcome
    timeline: tuple[AuditEvent, ...]


def _by_type(events: Sequence[AuditEvent], event_type: str) -> tuple[AuditEvent, ...]:
    return tuple(e for e in events if e.event_type == event_type)


def _payload(event: AuditEvent, *keys: str) -> Any:
    value: Any = event.payload
    for key in keys:
        value = value[key]
    return value


def explain_request(events: Sequence[AuditEvent]) -> DecisionExplanation:
    """Reconstructs one request's full explanation from its timeline
    ALONE. `events` should already be scoped to one request_id (e.g. via
    sampark.audit.store.events_for_request) — a mixed-request event list
    is itself treated as an incomplete/inconsistent log."""
    if not events:
        raise IncompleteLogError("no events given — cannot explain a request with an empty log")

    ordered = sorted(events, key=lambda e: (e.occurred_at, TYPE_ORDER.get(e.event_type, 99), str(e.event_id)))

    received = _by_type(ordered, REQUEST_RECEIVED)
    if not received:
        raise IncompleteLogError(
            "no request.received event in this timeline — cannot establish the request's own identity"
        )
    if len(received) > 1:
        raise IncompleteLogError("more than one request.received event for what should be one request")
    base = received[0]

    request_id = _payload(base, "request_id")
    other_request_ids = {_payload(e, "request_id") for e in ordered if "request_id" in e.payload}
    if other_request_ids - {request_id}:
        raise IncompleteLogError(
            f"events reference more than one request_id ({sorted(other_request_ids)}); "
            "explain_request requires a single-request timeline"
        )

    scope_denials = _by_type(ordered, REQUEST_DENIED_ON_SCOPE)
    decision_denials = _by_type(ordered, DECISION_DENIED)
    decision_deferrals = _by_type(ordered, DECISION_DEFERRED)
    reservations = _by_type(ordered, GRANT_RESERVED)
    lifecycle_events = tuple(e for e in ordered if e.event_type in _GRANT_LIFECYCLE_TYPES)

    if len(reservations) > 1:
        raise IncompleteLogError(f"more than one grant.reserved event for request_id={request_id}")
    if scope_denials and (decision_denials or decision_deferrals or reservations):
        raise IncompleteLogError(
            f"request_id={request_id} has both a scope denial and an allocator decision — "
            "spec §6.2's scope path never reaches the allocator"
        )
    if lifecycle_events and not reservations:
        raise IncompleteLogError(
            f"request_id={request_id} has grant lifecycle events with no antecedent grant.reserved"
        )

    customer_id = _payload(base, "customer_id")
    risk_id = _payload(base, "risk_id")
    amount_paise = None
    for e in (*decision_denials, *decision_deferrals, *reservations):
        if "amount_paise" in e.payload:
            amount_paise = e.payload["amount_paise"]
            break

    requested = RequestedTerms(
        intent=_payload(base, "intent"),
        requested_channel=_payload(base, "requested_channel"),
        requested_max_incentive_bps=_payload(base, "requested_max_incentive_bps"),
    )

    if scope_denials:
        scope_result = ScopeResult(passed=False, reason_code=scope_denials[0].reason_code)
    else:
        scope_result = ScopeResult(passed=True, reason_code=None)

    policy_result: PolicyResult | None = None
    if not scope_denials:
        terminal_decision = None
        if decision_denials:
            terminal_decision = decision_denials[-1]
        elif decision_deferrals:
            terminal_decision = decision_deferrals[-1]
        if terminal_decision is not None:
            policy_result = PolicyResult(
                reason_code=terminal_decision.reason_code,
                fact_unavailable_reason_codes=tuple(terminal_decision.payload.get("fact_unavailable_reason_codes", ())),
                window_id=terminal_decision.payload.get("window_id"),
                windows_deferred=terminal_decision.payload.get("windows_deferred"),
                next_eligible_at=terminal_decision.payload.get("next_eligible_at"),
                expected_net_paise=terminal_decision.payload.get("expected_net_paise"),
            )
        elif reservations:
            policy_result = PolicyResult(
                reason_code=None, fact_unavailable_reason_codes=(), window_id=reservations[0].payload.get("window_id"),
                windows_deferred=None, next_eligible_at=None, expected_net_paise=None,
            )

    grant: GrantSummary | None = None
    if reservations:
        r = reservations[0]
        grant = GrantSummary(
            grant_id=_payload(r, "grant_id"), channel=_payload(r, "channel"),
            incentive_ceiling_paise=_payload(r, "incentive_ceiling_paise"),
            effective_incentive_bps=r.payload.get("effective_incentive_bps"),
            send_after=_payload(r, "send_after"), expires_at=_payload(r, "expires_at"),
        )

    lifecycle = tuple(
        LifecycleStep(event_type=e.event_type, occurred_at=e.occurred_at, reason_code=e.reason_code)
        for e in lifecycle_events
    )

    if reservations:
        outcome: Outcome = "GRANTED"
    elif scope_denials or decision_denials:
        outcome = "DENIED"
    elif decision_deferrals:
        outcome = "DEFERRED"
    else:
        outcome = "PENDING"

    return DecisionExplanation(
        request_id=request_id, agent_id=_payload(base, "agent_id"), authenticated=True,
        customer_id=customer_id, risk=RiskSummary(risk_id=risk_id, amount_paise=amount_paise),
        requested=requested, scope_result=scope_result, policy_result=policy_result, grant=grant,
        lifecycle=lifecycle, outcome=outcome, timeline=tuple(ordered),
    )


@dataclass(frozen=True)
class CompetitorOutcome:
    request_id: str
    agent_id: str
    risk_id: str
    outcome: Literal["GRANTED", "DENIED", "DEFERRED"]
    reason_code: str | None
    expected_net_paise: int | None


@dataclass(frozen=True)
class ContestedWindowSummary:
    customer_id: str
    window_id: str
    winner: CompetitorOutcome | None
    losers: tuple[CompetitorOutcome, ...]


def explain_contested_window(events: Sequence[AuditEvent]) -> ContestedWindowSummary:
    """Phase 5A §9.2: reconstructs one (customer_id, window_id)
    allocation round from its full event set alone — `events` should be
    exactly what sampark.audit.store.events_for_customer_window returns
    for one round. Requires no Phase 4 change: window_id/customer_id are
    already on every decision/grant.reserved payload."""
    relevant = [
        e for e in events
        if e.event_type in (DECISION_DENIED, DECISION_DEFERRED, GRANT_RESERVED)
    ]
    if not relevant:
        raise IncompleteLogError("no decision/grant.reserved events given for this contested window")

    customer_ids = {e.payload.get("customer_id") for e in relevant}
    window_ids = {e.payload.get("window_id") for e in relevant}
    if len(customer_ids) != 1 or len(window_ids) != 1:
        raise IncompleteLogError(
            f"events span more than one (customer_id, window_id) pair: "
            f"customers={customer_ids}, windows={window_ids}"
        )

    winner: CompetitorOutcome | None = None
    losers: list[CompetitorOutcome] = []
    for e in sorted(relevant, key=lambda e: (e.occurred_at, str(e.event_id))):
        request_id = _payload(e, "request_id")
        agent_id = _payload(e, "agent_id")
        risk_id = _payload(e, "risk_id")
        if e.event_type == GRANT_RESERVED:
            competitor = CompetitorOutcome(
                request_id=request_id, agent_id=agent_id, risk_id=risk_id,
                outcome="GRANTED", reason_code=None, expected_net_paise=None,
            )
            if winner is not None:
                raise IncompleteLogError("more than one grant.reserved event for one (customer, window) round")
            winner = competitor
        else:
            competitor = CompetitorOutcome(
                request_id=request_id, agent_id=agent_id, risk_id=risk_id,
                outcome="DENIED" if e.event_type == DECISION_DENIED else "DEFERRED",
                reason_code=e.reason_code, expected_net_paise=e.payload.get("expected_net_paise"),
            )
            losers.append(competitor)

    customer_id = customer_ids.pop()
    window_id = window_ids.pop()
    return ContestedWindowSummary(customer_id=customer_id, window_id=window_id, winner=winner, losers=tuple(losers))


def format_explanation(explanation: DecisionExplanation) -> str:
    """Deterministic plain-English formatter — no LLM (CLAUDE.md §7).
    Same input, same string, always."""
    lines = [
        f"Request {explanation.request_id} by agent {explanation.agent_id} "
        f"({'authenticated' if explanation.authenticated else 'not authenticated'}).",
        f"Customer {explanation.customer_id}, risk item {explanation.risk.risk_id}"
        + (f", amount at risk Rs {explanation.risk.amount_paise / 100:.2f}." if explanation.risk.amount_paise is not None else "."),
        f"Requested: {explanation.requested.requested_channel} channel, intent={explanation.requested.intent}, "
        f"max incentive {explanation.requested.requested_max_incentive_bps} bps.",
    ]
    if not explanation.scope_result.passed:
        lines.append(f"DENIED on scope: {explanation.scope_result.reason_code}. The allocator never ran.")
    else:
        lines.append("Scope check passed; forwarded to the allocator.")
        if explanation.policy_result is not None and explanation.policy_result.reason_code is not None:
            pr = explanation.policy_result
            lines.append(
                f"{explanation.outcome} for reason: {pr.reason_code}"
                + (f" (window {pr.window_id})" if pr.window_id else "")
                + (f", next eligible at {pr.next_eligible_at}." if pr.next_eligible_at else ".")
            )
            if pr.fact_unavailable_reason_codes:
                lines.append(f"Unresolved facts recorded (did not block): {', '.join(pr.fact_unavailable_reason_codes)}.")
        if explanation.grant is not None:
            g = explanation.grant
            lines.append(
                f"GRANTED: grant {g.grant_id}, channel {g.channel}, "
                f"incentive ceiling {g.incentive_ceiling_paise} paise, send_after {g.send_after}, expires_at {g.expires_at}."
            )
    if explanation.lifecycle:
        steps = "; ".join(f"{s.event_type}@{s.occurred_at.isoformat()}" for s in explanation.lifecycle)
        lines.append(f"Lifecycle: {steps}.")
    lines.append(f"Final status: {explanation.outcome}.")
    return " ".join(lines)
