"""Mediation service — the single entry point from a verified request to
a GrantDecision, Design Lock §1 (architecture), §16 (determinism).

    request -> Registry (sampark.registry.scope.evaluate_scope)
            -> DENIED (scope.*)                          [allocator never runs]
            -> verified -> Candidate -> hard filter -> allocator -> GrantDecision

This module does NOT re-implement signature verification or capability
scope checks (CLAUDE.md §10 / spec §8.1) — it calls Phase 3's
evaluate_scope() unchanged and treats its DENIED result as final,
exactly as sampark/registry/scope.py's own docstring specifies: "only
Phase 4's allocator may create" a GRANTED or DEFERRED decision.

`decision_id` is derived deterministically (Design Lock §16) rather
than the registry's `uuid4()`: a candidate receives at most one
decision per window, so (request_id, window_id) is a unique key.
sampark.registry.scope's own uuid4() scope-denial IDs are untouched —
scope violations are expected to be 0 in both arms and so cannot
perturb a reproducible Arm B log (flagged for Phase 5's replay work).

--- Phase 5 U-2: the `audit_sink` parameter ---

`mediate_window(..., audit_sink=None)` is the ONE Phase 4 decision-path
change U-2 makes, and it is additive/instrumentation-only: when
`audit_sink` is `None` (every call site before U-2, and every call site
that does not opt in), this module's behavior, control flow, and return
value are BYTE-IDENTICAL to before U-2 — no branch here reads
`audit_sink` for anything but "is it None."

This is the integration point Phase 5A/5B's investigation identified as
the higher-fidelity one: `sim/arm_b.py` only ever sees the projected
`GrantDecision` (via `MediationWindowResult.decisions`), which has no
`fact_unavailable_reason_codes`, `windows_deferred`, or `score` field —
those live only on `AllocationOutcome`, which exists in this module's
own `outcomes` loop below and nowhere the caller can reach. Recording
here, at the source, means the audit log gets the SAME facts the
decision was actually made from — not a caller's lossy reconstruction
of them.

`audit_sink` is typed only under TYPE_CHECKING (`sampark.audit.sink.
AuditSink`, a structural Protocol) so this Phase 4 module gets no new
RUNTIME dependency on `sampark.audit` — the type never needs to be
imported to run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sampark.allocator.candidate import Candidate, build_candidate
from sampark.allocator.constants import AGING_BONUS_PAISE
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.budget.store import GrantIssuer, InMemoryMediationLedger
from sampark.contracts import DecisionOutcome, GrantDecision, GrantRequest
from sampark.mediation.hard_filter import filter_and_allocate
from sampark.registry.scope import evaluate_scope
from sampark.registry.store import AgentRepository, RiskItemRepository

if TYPE_CHECKING:
    from sampark.audit.sink import AuditSink

NS_DECISION = uuid.UUID("2a9c9e2e-7a0a-4b7a-9a4a-8b6f0b6b3a55")


def decision_id_for(request_id: uuid.UUID, window_id: date) -> uuid.UUID:
    return uuid.uuid5(NS_DECISION, f"{request_id}:{window_id}")


def build_decision(outcome: AllocationOutcome) -> GrantDecision:
    candidate = outcome.candidate
    did = decision_id_for(candidate.request.request_id, candidate.window_id)
    if outcome.outcome_kind is OutcomeKind.GRANTED:
        return GrantDecision(
            decision_id=did,
            request_id=candidate.request.request_id,
            outcome=DecisionOutcome.GRANTED,
            reason_code=None,
            human_readable=None,
            next_eligible_at=None,
            grant=outcome.grant,
        )
    if outcome.outcome_kind is OutcomeKind.DENIED:
        return GrantDecision(
            decision_id=did,
            request_id=candidate.request.request_id,
            outcome=DecisionOutcome.DENIED,
            reason_code=outcome.reason_code,
            human_readable=None,
            next_eligible_at=None,
            grant=None,
        )
    assert outcome.outcome_kind is OutcomeKind.DEFERRED
    return GrantDecision(
        decision_id=did,
        request_id=candidate.request.request_id,
        outcome=DecisionOutcome.DEFERRED,
        reason_code=outcome.reason_code,
        human_readable=None,
        next_eligible_at=outcome.next_eligible_at,
        grant=None,
    )


@dataclass(frozen=True)
class MediationWindowResult:
    decisions: tuple[GrantDecision, ...]
    rescheduled_candidates: tuple[Candidate, ...]
    # Keyed by request_id, GRANTED entries only — the incentive actually
    # reserved (post-downgrade), needed by the caller to simulate the
    # outcome against what was ACTUALLY granted (sim/arm_b.py). Not part
    # of the GrantDecision/Grant contracts themselves (CONTRACTS.md).
    effective_incentive_bps_by_request_id: dict[uuid.UUID, int]


def mediate_window(
    new_requests: tuple[tuple[GrantRequest, datetime], ...],
    carried_forward: tuple[Candidate, ...],
    agent_repo: AgentRepository,
    risk_item_repo: RiskItemRepository,
    ledger: InMemoryMediationLedger,
    issuer: GrantIssuer,
    decision_at: datetime,
    aging_bonus_paise: int = AGING_BONUS_PAISE,
    conn: object = None,
    fifo_mode: bool = False,
    *,
    run_seed_risk_ids: frozenset[str] | None = None,
    audit_sink: "AuditSink | None" = None,
) -> MediationWindowResult:
    """`new_requests` are (request, proposed_send_after) pairs arriving
    fresh this window — each is scope-checked here, for the first and
    only time. `carried_forward` are Candidates already scope-verified
    in an earlier window (scope does not change across windows for a
    fixed request, so it is not re-checked).

    `conn` is passed straight through to `allocate_window`/`issuer.issue_grant`
    — it defaults to None, which `allocate_window` then defaults to
    `ledger` itself (the in-memory reference's "conn" IS the ledger; see
    sampark/budget/store.py). Pass a real `psycopg.Connection` here to
    use `sampark.budget.issuance.PostgresGrantIssuer` while still reading
    policy state from an `InMemoryMediationLedger` — this does not change
    the GrantIssuer protocol itself, only how the caller wires it up.

    `run_seed_risk_ids` — Phase 4C hardening, W5. Threaded straight
    through to `filter_and_allocate` -> `allocate_window` ->
    `issuer.issue_grant`. See `sampark.budget.issuance.issue_grant`'s
    docstring for the `None`-default rationale.

    `audit_sink` — Phase 5, U-2. `None` by default: every branch that
    touches it below is skipped entirely, and this function's decision
    logic, control flow, and return value are unchanged from pre-U-2.
    When given a real `AuditSink` (`sampark.audit.sink.PostgresAuditSink`
    in practice), this function calls it at the two points where it
    already has information no caller can reconstruct afterward: right
    after scope evaluation (module docstring's rationale), and inside
    the existing `outcomes` loop, once per `AllocationOutcome`. Nothing
    about WHICH candidates are admitted, how they are ranked, or what
    they are granted changes — `audit_sink` is called with outcomes
    already fully decided by the code above it."""
    decisions: list[GrantDecision] = []
    candidates: list[Candidate] = list(carried_forward)

    for request, proposed_send_after in new_requests:
        if audit_sink is not None:
            audit_sink.record_request_received(request)
        scope_decision = evaluate_scope(request, agent_repo, risk_item_repo)
        if scope_decision is not None:
            decisions.append(scope_decision)
            if audit_sink is not None:
                audit_sink.record_denied_on_scope(scope_decision, request, decision_at)
            continue
        record = risk_item_repo.get_risk_item(request.risk_id)
        if record is None:
            # evaluate_scope() already returned None only when the risk
            # item exists and matches the request's customer_id — this
            # is unreachable, kept only as a defensive invariant check.
            raise RuntimeError(f"risk_item {request.risk_id!r} vanished after scope verification")
        candidate = build_candidate(request, record.risk_item, record.customer_id, proposed_send_after)
        candidates.append(candidate)

    outcomes = filter_and_allocate(
        tuple(candidates), ledger, issuer, decision_at, aging_bonus_paise, conn=conn, fifo_mode=fifo_mode,
        run_seed_risk_ids=run_seed_risk_ids,
    )

    rescheduled: list[Candidate] = []
    effective_bps_by_request_id: dict[uuid.UUID, int] = {}
    for outcome in outcomes:
        decisions.append(build_decision(outcome))
        if audit_sink is not None:
            if outcome.outcome_kind is OutcomeKind.GRANTED:
                audit_sink.record_grant_reserved(outcome)
            else:
                audit_sink.record_decision(outcome, decision_at)
        if outcome.rescheduled_candidate is not None:
            rescheduled.append(outcome.rescheduled_candidate)
        if outcome.outcome_kind is OutcomeKind.GRANTED:
            assert outcome.effective_incentive_bps is not None
            effective_bps_by_request_id[outcome.candidate.request.request_id] = outcome.effective_incentive_bps

    return MediationWindowResult(
        decisions=tuple(decisions),
        rescheduled_candidates=tuple(rescheduled),
        effective_incentive_bps_by_request_id=effective_bps_by_request_id,
    )
