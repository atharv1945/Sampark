"""The emitter — Phase 5A §1.2's "Emit" layer.

STRUCTURAL RULE (enforced by tests/audit/test_emit.py's
test_audit_package_never_imports_policy_or_scoring_or_greedy /
test_audit_package_never_imports_bare_policy_module via AST, the same
technique as tests/allocator/test_structural_boundaries.py):
this module may only COPY fields off objects Phase 4 already returned. It
must never import `sampark.policy`, `sampark.policy.hard`,
`sampark.allocator.scoring`, or `sampark.allocator.greedy`, and must never
recompute a verdict, a score, a fatigue term, an allocation ranking, or a
reason code. `sampark.allocator.candidate` (Candidate) and
`sampark.allocator.outcomes` (AllocationOutcome/OutcomeKind) are data
shapes, not evaluators — importing them here is the same distinction
Design Lock draws for sampark.policy.hard's own imports.

Every function returns a DRAFT AuditEvent with `prev_hash =
PENDING_PREV_HASH` — chain.append() derives the real value under the
advisory lock (Phase 5A §7.1). Nothing here touches the database.

Phase 4 integration status: WIRED. `sampark/mediation/service.py::
mediate_window` and `sim/arm_b.py` both call into this module via
`sampark.audit.sink.PostgresAuditSink` (U-2, applied) whenever an
`audit_sink` is supplied — `None` by default, so every pre-U-2 call site
is unaffected. `sim/arm_b.py`'s agent-registry builders
(`_build_agent_registry_memory`/`_build_agent_registry_postgres`) also
call `event_for_agent_registered` the same way (U-8's registration half,
applied). Every function here remains additionally exercised directly by
tests/audit/** against hand-built Phase 4 objects, and by
tests/audit/test_integration.py against the real Phase 4 decision path.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit.canonical import iso_utc_micros
from sampark.audit.chain import PENDING_PREV_HASH, event_id_for
from sampark.audit.event_types import (
    AGENT_REGISTERED,
    AGENT_REVOKED,
    AGENT_STRUCK,
    DECISION_DEFERRED,
    DECISION_DENIED,
    GRANT_CONFIRMED,
    GRANT_EXECUTING,
    GRANT_EXPIRED,
    GRANT_RESERVED,
    GRANT_ROLLED_BACK,
    REQUEST_DENIED_ON_SCOPE,
    REQUEST_RECEIVED,
)
from sampark.contracts import Agent, AuditEvent, Grant, GrantDecision, GrantRequest

PAYLOAD_VERSION = 1

# Fixed, literal reason strings for system-initiated grant terminal
# events — copied, not computed: they describe WHY this module is being
# called, supplied by the caller (Phase 4's own lifecycle transition
# functions already distinguish rollback-by-provider-failure from
# expiry-by-TTL; this module attaches the matching fixed label, it does
# not infer one).
ROLLBACK_REASON = "provider_failure"
EXPIRY_REASON = "ttl_expired"


def _round_paise(value: float) -> int:
    """Phase 5A §4.3 rule 7: floats are banned from payloads. Money is
    rounded to int paise, once, here — the only place a float->int
    conversion for a payload happens."""
    return round(value)


def _date_str(d: date) -> str:
    return d.isoformat()


def _draft(event_type: str, event_id: uuid.UUID, occurred_at: datetime,
           agent_signature: str | None, reason_code: str | None, payload: dict[str, Any]) -> AuditEvent:
    payload = dict(payload)
    payload["v"] = PAYLOAD_VERSION
    return AuditEvent(
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        prev_hash=PENDING_PREV_HASH,
        agent_signature=agent_signature,
        reason_code=reason_code,
        payload=payload,
    )


# --- request lifecycle -------------------------------------------------


def event_for_request_received(request: GrantRequest) -> AuditEvent:
    """Spec §8.10 "request". Emitted once per signed GrantRequest that
    reaches evaluate_scope() — the first point at which the request
    exists as a candidate fact worth recording, whether or not it
    survives scope evaluation."""
    return _draft(
        REQUEST_RECEIVED,
        event_id_for(REQUEST_RECEIVED, str(request.request_id)),
        request.issued_at,
        request.signature,
        None,
        {
            "request_id": str(request.request_id),
            "agent_id": request.agent_id,
            "customer_id": request.customer_id,
            "risk_id": request.risk_id,
            "intent": request.intent,
            "requested_channel": request.requested_channel,
            "requested_max_incentive_bps": request.requested_max_incentive_bps,
        },
    )


def event_for_denied_on_scope(decision: GrantDecision, request: GrantRequest, occurred_at: datetime) -> AuditEvent:
    """Spec §6.2's Registry-only denial path — "DENY {scope_violation,
    capability_required}", "no allocator involvement" (CLAUDE.md §12's
    test_scope_enforcement.py). `decision.reason_code` is copied
    verbatim from sampark.registry.scope.evaluate_scope's own
    `scope.*` code — never re-derived. No amount_paise / window_id: a
    scope denial can occur before a risk item is even confirmed to
    exist (e.g. scope.unknown_risk_item), so those facts are not
    reliably available at this point, and this event never claims them.
    `occurred_at` is passed explicitly (Design Lock §3.5: never a wall
    clock) — the Registry's own decision has no timestamp field of its
    own (CONTRACTS.md's GrantDecision carries none)."""
    if decision.reason_code is None:
        # Unreachable in practice: CONTRACTS.md's GrantDecision model
        # validator already requires a DENIED outcome to carry a
        # reason_code, so pydantic rejects the malformed decision before
        # it could ever reach this function. Kept as a defensive
        # invariant check, matching sampark.mediation.service's own
        # pattern for an equivalent unreachable branch.
        raise ValueError("a scope denial must carry a reason_code")
    return _draft(
        REQUEST_DENIED_ON_SCOPE,
        event_id_for(REQUEST_DENIED_ON_SCOPE, str(request.request_id)),
        occurred_at,
        request.signature,
        decision.reason_code,
        {
            "request_id": str(request.request_id),
            "agent_id": request.agent_id,
            "customer_id": request.customer_id,
            "risk_id": request.risk_id,
            "intent": request.intent,
            "requested_channel": request.requested_channel,
            "requested_max_incentive_bps": request.requested_max_incentive_bps,
        },
    )


# --- allocation decision -------------------------------------------------


def _decision_payload(outcome: AllocationOutcome) -> dict[str, Any]:
    candidate = outcome.candidate
    request = candidate.request
    expected_net_paise = None if outcome.score is None else _round_paise(outcome.score.expected_net_paise)
    return {
        "request_id": str(request.request_id),
        "agent_id": request.agent_id,
        "customer_id": candidate.customer_id,
        "risk_id": candidate.risk_item.risk_id,
        "window_id": _date_str(candidate.window_id),
        "intent": request.intent,
        "requested_channel": request.requested_channel,
        "requested_max_incentive_bps": request.requested_max_incentive_bps,
        "amount_paise": candidate.risk_item.amount_paise,
        "next_eligible_at": None if outcome.next_eligible_at is None else iso_utc_micros(outcome.next_eligible_at),
        "windows_deferred": candidate.windows_deferred,
        "fact_unavailable_reason_codes": list(outcome.fact_unavailable_reason_codes),
        "expected_net_paise": expected_net_paise,
    }


def event_for_decision(outcome: AllocationOutcome, occurred_at: datetime) -> AuditEvent:
    """DENIED / DEFERRED outcomes from either sampark.mediation.hard_filter
    (hard-policy INADMISSIBLE) or sampark.allocator.greedy (competitive
    loss / negative expected net) — both produce the identical
    AllocationOutcome shape (sampark/allocator/outcomes.py's own module
    docstring), so this ONE function covers both origins without caring
    which produced it. `outcome.reason_code` is copied verbatim; this
    module never re-evaluates a HardVerdict or a score.

    `outcome.score` is populated only for NEGATIVE_EXPECTED_NET denials
    today (Phase 4, unmodified) — `expected_net_paise` is `null` in the
    payload for every other reason_code until U-3 is applied. The key is
    ALWAYS present (Phase 5A §4.3 rule 4); only its value is
    conditional."""
    if outcome.outcome_kind is OutcomeKind.GRANTED:
        raise ValueError("event_for_decision is for DENIED/DEFERRED outcomes only; use event_for_grant_reserved")
    if outcome.reason_code is None:
        raise ValueError("a DENIED/DEFERRED outcome must carry a reason_code")

    request_id = str(outcome.candidate.request.request_id)
    if outcome.outcome_kind is OutcomeKind.DENIED:
        event_type = DECISION_DENIED
        event_id = event_id_for(DECISION_DENIED, request_id)
    else:
        assert outcome.outcome_kind is OutcomeKind.DEFERRED
        event_type = DECISION_DEFERRED
        event_id = event_id_for(DECISION_DEFERRED, request_id, _date_str(outcome.candidate.window_id))

    return _draft(
        event_type, event_id, occurred_at,
        outcome.candidate.request.signature, outcome.reason_code, _decision_payload(outcome),
    )


# --- grant lifecycle -------------------------------------------------


def event_for_grant_reserved(outcome: AllocationOutcome, budget_window_id: uuid.UUID, claim_id: uuid.UUID) -> AuditEvent:
    """The ONE grant event (U-4, approved): reservation and grant are
    one atomic fact inside sampark.budget.issuance's SERIALIZABLE
    transaction, so this module records one row, not two.
    `budget_window_id` / `claim_id` are not carried on the Grant or
    GrantRequest contracts (CONTRACTS.md) — the caller (the future
    arm_b.py integration) must supply them from whatever the issuer
    returned/looked up; this function never queries for them itself."""
    if outcome.outcome_kind is not OutcomeKind.GRANTED:
        raise ValueError("event_for_grant_reserved requires a GRANTED outcome")
    grant = outcome.grant
    if grant is None:
        raise ValueError("a GRANTED outcome must carry a grant")
    candidate = outcome.candidate
    request = candidate.request
    return _draft(
        GRANT_RESERVED,
        event_id_for(GRANT_RESERVED, str(grant.grant_id)),
        grant.send_after,
        request.signature,
        None,
        {
            "grant_id": str(grant.grant_id),
            "request_id": str(request.request_id),
            "agent_id": request.agent_id,
            "customer_id": candidate.customer_id,
            "risk_id": candidate.risk_item.risk_id,
            "window_id": _date_str(candidate.window_id),
            "channel": grant.channel,
            "incentive_ceiling_paise": grant.incentive_ceiling_paise,
            "effective_incentive_bps": outcome.effective_incentive_bps,
            "send_after": iso_utc_micros(grant.send_after),
            "expires_at": iso_utc_micros(grant.expires_at),
            "budget_window_id": str(budget_window_id),
            "claim_id": str(claim_id),
        },
    )


def event_for_grant_executing(grant: Grant, request: GrantRequest, at: datetime) -> AuditEvent:
    return _draft(
        GRANT_EXECUTING, event_id_for(GRANT_EXECUTING, str(grant.grant_id)), at,
        request.signature, None,
        {"grant_id": str(grant.grant_id), "request_id": str(request.request_id)},
    )


def event_for_grant_confirmed(grant: Grant, request: GrantRequest, at: datetime, actual_spend_paise: int) -> AuditEvent:
    return _draft(
        GRANT_CONFIRMED, event_id_for(GRANT_CONFIRMED, str(grant.grant_id)), at,
        request.signature, None,
        {"grant_id": str(grant.grant_id), "request_id": str(request.request_id), "actual_spend_paise": actual_spend_paise},
    )


def event_for_grant_rolled_back(grant: Grant, request: GrantRequest, at: datetime) -> AuditEvent:
    return _draft(
        GRANT_ROLLED_BACK, event_id_for(GRANT_ROLLED_BACK, str(grant.grant_id)), at,
        request.signature, ROLLBACK_REASON,
        {"grant_id": str(grant.grant_id), "request_id": str(request.request_id)},
    )


def event_for_grant_expired(grant_id: uuid.UUID, request_id: uuid.UUID, at: datetime) -> AuditEvent:
    """System-initiated (Design Lock §9's TTL sweep, never a signed
    request) — no agent_signature, matching event_types.SIGNED_EVENT_TYPES
    excluding this type."""
    return _draft(
        GRANT_EXPIRED, event_id_for(GRANT_EXPIRED, str(grant_id)), at,
        None, EXPIRY_REASON,
        {"grant_id": str(grant_id), "request_id": str(request_id)},
    )


# --- registry (U-8: append-after-write, sampark/registry/** unmodified) ---


def event_for_agent_registered(agent: Agent, at: datetime) -> AuditEvent:
    """`Agent.publisher` is a free-form display string (`CapabilityScope`/
    `Agent` place no format constraint on it — real values in this
    codebase include "Acme Recovery Co", "Third-Party Recovery Co", "SAMPARK
    Arm B evidence runner"), so it is deliberately NOT copied into the
    payload: canonical.py's `_SAFE_PAYLOAD_STRING_RE` requires every
    payload string to be a controlled ASCII identifier (Phase 5A §4.3 rule
    3 / §10 privacy rule — "no free-form message text"), and `publisher`
    fails that by construction the moment it contains a space. `agent_id`
    alone (already the identifier used by every other event type) is
    sufficient to identify which registration this event records; the
    registry's own `agents` table remains the source of truth for
    `publisher` if it is ever needed."""
    return _draft(
        AGENT_REGISTERED, event_id_for(AGENT_REGISTERED, agent.agent_id), at,
        None, None,
        {"agent_id": agent.agent_id},
    )


def event_for_agent_struck(agent_after_strike: Agent, reason_code: str, at: datetime, request: GrantRequest) -> AuditEvent:
    """`agent_after_strike` is the Agent returned by
    sampark.registry.strikes.record_scope_denial — its `strike_count`
    is the NEW value, which is what makes the event_id
    (agent_id + strike_count) unique per strike rather than colliding on
    repeat. `reason_code` is the STRIKE_WORTHY_REASON_CODES entry that
    triggered it, copied from the scope denial, never re-evaluated."""
    return _draft(
        AGENT_STRUCK,
        event_id_for(AGENT_STRUCK, agent_after_strike.agent_id, str(agent_after_strike.strike_count)),
        at, request.signature, reason_code,
        {
            "agent_id": agent_after_strike.agent_id,
            "strike_count": agent_after_strike.strike_count,
            "request_id": str(request.request_id),
        },
    )


def event_for_agent_revoked(agent_after_revocation: Agent, at: datetime, reason_code: str | None = None) -> AuditEvent:
    """Covers both auto-revocation (strike_count reaching the threshold
    — sampark.registry.strikes.apply_strike) and manual revocation
    (sampark.registry.strikes.revoke). `reason_code` is `None` for a
    manual revocation (no scope-denial reason behind it) — the contract
    already allows this (AuditEvent.reason_code: str | None)."""
    return _draft(
        AGENT_REVOKED, event_id_for(AGENT_REVOKED, agent_after_revocation.agent_id), at,
        None, reason_code,
        {"agent_id": agent_after_revocation.agent_id, "strike_count": agent_after_revocation.strike_count},
    )
