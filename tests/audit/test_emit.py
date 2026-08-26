"""T-19, T-20, T-22 — emitter correctness, lifecycle coverage, scope-
denial isolation, structural import boundaries (Phase 5A §1.2, §2.2).

No database. sampark.audit.emit builds draft AuditEvent objects purely
from Phase 4 objects handed to it.
"""

from __future__ import annotations

import ast
import datetime as dt
import importlib
import inspect
import pkgutil
import uuid

import pytest

import sampark.audit
from sampark.allocator.candidate import build_candidate
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit import emit
from sampark.audit.chain import PENDING_PREV_HASH
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
)
from sampark.contracts import (
    Agent,
    AgentState,
    DecisionOutcome,
    Grant,
    GrantDecision,
    GrantRequest,
    GrantState,
    RiskItem,
)

ISSUED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _request(**overrides) -> GrantRequest:
    fields = dict(
        request_id=uuid.uuid4(), agent_id="cart_recovery_agent", customer_id="cust-1", risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=500,
        issued_at=ISSUED_AT, signature="sig-abc123",
    )
    fields.update(overrides)
    return GrantRequest(**fields)


def _candidate(request: GrantRequest | None = None):
    request = request or _request()
    item = RiskItem(
        risk_id=request.risk_id, source="abandoned_checkout", amount_paise=500_000,
        root_cause="price_hesitation", detected_at=ISSUED_AT,
    )
    return build_candidate(request, item, request.customer_id, dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))


# --- structural boundary (T-22) -----------------------------------------


def _imported_module_names(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _all_submodules(package):
    modules = [package]
    if hasattr(package, "__path__"):
        for info in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
            modules.append(importlib.import_module(info.name))
    return modules


def test_audit_package_never_imports_policy_or_scoring_or_greedy():
    banned_substrings = ("policy.hard", "policy.soft", "allocator.scoring", "allocator.greedy")
    for module in _all_submodules(sampark.audit):
        if module.__name__ == "sampark.audit.verify":
            continue  # verify.py is a CLI entry point; still checked below explicitly
        imported = _imported_module_names(module)
        for banned in banned_substrings:
            assert not any(banned in name for name in imported), (
                f"{module.__name__} must never import anything containing {banned!r}; found {imported}"
            )
        assert not any(name == "sampark.policy" for name in imported), (
            f"{module.__name__} must never import sampark.policy; found {imported}"
        )


def test_audit_package_never_imports_bare_policy_module():
    import sampark.audit.verify

    for module in _all_submodules(sampark.audit) + [sampark.audit.verify]:
        imported = _imported_module_names(module)
        assert "sampark.policy" not in imported, f"{module.__name__} imports sampark.policy directly"


# --- request / scope emitters -------------------------------------------


def test_event_for_request_received_shape():
    request = _request()
    event = emit.event_for_request_received(request)
    assert event.event_type == REQUEST_RECEIVED
    assert event.prev_hash == PENDING_PREV_HASH
    assert event.agent_signature == request.signature
    assert event.reason_code is None
    assert event.payload["request_id"] == str(request.request_id)
    assert event.payload["agent_id"] == request.agent_id
    assert event.payload["v"] == 1


def test_event_for_request_received_is_deterministic_in_event_id():
    request = _request()
    e1 = emit.event_for_request_received(request)
    e2 = emit.event_for_request_received(request)
    assert e1.event_id == e2.event_id


def test_event_for_denied_on_scope_shape():
    # T-20: this event carries NO allocator-only fields (window_id,
    # amount_paise, expected_net_paise) — proving, at the payload-shape
    # level, that the scope path never touches the allocator.
    request = _request(requested_channel="voice")
    decision = GrantDecision(
        decision_id=uuid.uuid4(), request_id=request.request_id, outcome=DecisionOutcome.DENIED,
        reason_code="scope.channel_not_allowed", human_readable=None, next_eligible_at=None, grant=None,
    )
    event = emit.event_for_denied_on_scope(decision, request, ISSUED_AT)
    assert event.event_type == REQUEST_DENIED_ON_SCOPE
    assert event.reason_code == "scope.channel_not_allowed"
    assert "window_id" not in event.payload
    assert "amount_paise" not in event.payload
    assert "expected_net_paise" not in event.payload


# --- decision emitters ---------------------------------------------------


def test_event_for_decision_denied_shape():
    candidate = _candidate()
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DENIED, reason_code="policy.opt_out_active",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None,
        rescheduled_candidate=None,
    )
    event = emit.event_for_decision(outcome, dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))
    assert event.event_type == DECISION_DENIED
    assert event.reason_code == "policy.opt_out_active"
    assert event.payload["window_id"] == "2025-09-10"
    assert event.payload["amount_paise"] == 500_000
    assert event.payload["next_eligible_at"] is None
    assert event.payload["expected_net_paise"] is None  # score not populated (U-3 not applied)


def test_event_for_decision_deferred_carries_next_eligible_at():
    candidate = _candidate()
    next_eligible = dt.datetime(2025, 9, 11, 3, 30, tzinfo=dt.timezone.utc)
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DEFERRED, reason_code="budget.contact_cap_24h",
        next_eligible_at=next_eligible, grant=None, fact_unavailable_reason_codes=("fact_unavailable.rto_flag",),
        score=None, rescheduled_candidate=None,
    )
    event = emit.event_for_decision(outcome, dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))
    assert event.event_type == DECISION_DEFERRED
    assert event.payload["next_eligible_at"] == "2025-09-11T03:30:00.000000Z"
    assert event.payload["fact_unavailable_reason_codes"] == ["fact_unavailable.rto_flag"]


def test_event_for_decision_deferred_event_id_scoped_to_window():
    # Design Lock §16's (request_id, window_id) uniqueness applies to
    # audit event_ids too — a request deferred across multiple windows
    # gets a DISTINCT decision.deferred event_id per window.
    request = _request()
    item = RiskItem(risk_id=request.risk_id, source="abandoned_checkout", amount_paise=500_000,
                     root_cause="price_hesitation", detected_at=ISSUED_AT)
    c1 = build_candidate(request, item, request.customer_id, dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))
    c2 = c1.rescheduled(dt.date(2025, 9, 11), dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc)).aged()

    def _outcome(c):
        return AllocationOutcome(
            candidate=c, outcome_kind=OutcomeKind.DEFERRED, reason_code="budget.contact_cap_24h",
            next_eligible_at=dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc), grant=None,
            fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        )

    e1 = emit.event_for_decision(_outcome(c1), dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc))
    e2 = emit.event_for_decision(_outcome(c2), dt.datetime(2025, 9, 11, 9, 0, tzinfo=dt.timezone.utc))
    assert e1.event_id != e2.event_id


def test_event_for_decision_rejects_granted_outcome():
    candidate = _candidate()
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        expires_at=dt.datetime(2025, 9, 10, 14, 0, tzinfo=dt.timezone.utc), state=GrantState.RESERVED,
    )
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    with pytest.raises(ValueError):
        emit.event_for_decision(outcome, ISSUED_AT)


# --- grant lifecycle emitters (T-19) --------------------------------------


def _granted_outcome(candidate):
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        expires_at=dt.datetime(2025, 9, 10, 14, 0, tzinfo=dt.timezone.utc), state=GrantState.RESERVED,
    )
    return AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )


def test_grant_lifecycle_confirmed_path_emits_expected_event_sequence():
    request = _request()
    candidate = _candidate(request)
    outcome = _granted_outcome(candidate)
    grant = outcome.grant

    reserved = emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4())
    executing = emit.event_for_grant_executing(grant, request, grant.send_after)
    confirmed = emit.event_for_grant_confirmed(grant, request, grant.send_after, actual_spend_paise=20_000)

    assert [e.event_type for e in (reserved, executing, confirmed)] == [
        GRANT_RESERVED, GRANT_EXECUTING, GRANT_CONFIRMED,
    ]
    assert confirmed.payload["actual_spend_paise"] == 20_000
    assert all(e.payload["grant_id"] == str(grant.grant_id) for e in (reserved, executing, confirmed))


def test_grant_lifecycle_rolled_back_path():
    request = _request()
    candidate = _candidate(request)
    outcome = _granted_outcome(candidate)
    grant = outcome.grant

    reserved = emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4())
    executing = emit.event_for_grant_executing(grant, request, grant.send_after)
    rolled_back = emit.event_for_grant_rolled_back(grant, request, grant.send_after)

    assert [e.event_type for e in (reserved, executing, rolled_back)] == [
        GRANT_RESERVED, GRANT_EXECUTING, GRANT_ROLLED_BACK,
    ]
    assert rolled_back.reason_code == "provider_failure"


def test_grant_lifecycle_expired_path_has_no_agent_signature():
    request = _request()
    candidate = _candidate(request)
    outcome = _granted_outcome(candidate)
    grant = outcome.grant

    reserved = emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4())
    expired = emit.event_for_grant_expired(grant.grant_id, request.request_id, grant.expires_at)

    assert reserved.event_type == GRANT_RESERVED
    assert expired.event_type == GRANT_EXPIRED
    assert expired.reason_code == "ttl_expired"
    assert expired.agent_signature is None  # system-initiated: TTL sweep, never a signed request


def test_event_for_grant_reserved_requires_granted_outcome():
    candidate = _candidate()
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DENIED, reason_code="allocation.negative_expected_net",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    with pytest.raises(ValueError):
        emit.event_for_grant_reserved(outcome, uuid.uuid4(), uuid.uuid4())


# --- registry (U-8) --------------------------------------------------------


def test_event_for_agent_registered_and_struck_and_revoked():
    agent = Agent(agent_id="agent-1", public_key="pk", publisher="Acme", state=AgentState.ACTIVE, strike_count=0)
    registered = emit.event_for_agent_registered(agent, ISSUED_AT)
    assert registered.event_type == "agent.registered"
    assert registered.agent_signature is None

    request = _request(agent_id="agent-1")
    struck_agent = agent.model_copy(update={"strike_count": 1})
    struck = emit.event_for_agent_struck(struck_agent, "scope.channel_not_allowed", ISSUED_AT, request)
    assert struck.event_type == "agent.struck"
    assert struck.reason_code == "scope.channel_not_allowed"
    assert struck.payload["strike_count"] == 1

    revoked_agent = agent.model_copy(update={"strike_count": 3, "state": AgentState.REVOKED})
    revoked = emit.event_for_agent_revoked(revoked_agent, ISSUED_AT, reason_code="scope.channel_not_allowed")
    assert revoked.event_type == "agent.revoked"
    assert revoked.agent_signature is None

    # Distinct strike events for the SAME agent at different strike
    # counts must get distinct event_ids (Phase 5A §3.1's collision-
    # avoidance rationale for including strike_count in the derivation).
    struck_agent_2 = agent.model_copy(update={"strike_count": 2})
    struck_2 = emit.event_for_agent_struck(struck_agent_2, "scope.channel_not_allowed", ISSUED_AT, request)
    assert struck.event_id != struck_2.event_id
