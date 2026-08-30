"""The structural claim behind both rogue stages.

Phase 3's exit criterion — "an out-of-scope request is rejected on
signature-verified scope alone, with NO allocator involvement" — is already
proven for stage one by `tests/test_scope_enforcement.py`, which Phase 8
reuses unmodified.

Phase 8 adds a SECOND pre-allocator gate (the rate ceiling), so the same
claim has to be re-established for it. These tests do that two ways, because
either alone is weak:

    STATICALLY  `sampark/demo/enforcement.py` must not import the allocator
                or the hard-policy package at all — the same AST technique
                `tests/allocator/test_structural_boundaries.py` uses.
    DYNAMICALLY `filter_and_allocate` is monkeypatched to raise. Any request
                denied on scope or on rate must complete without touching it.

A "mock was called" assertion would prove nothing here; making the allocator
explode is what proves it was never reached.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import uuid

import pytest

from sampark.contracts import Agent, AgentState, CapabilityScope, GrantRequest, RiskItem
from sampark.demo import isolation
from sampark.demo.enforcement import AgentRateWindow, evaluate_agent_rate
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import ROGUE_AGENT_ID, ROGUE_SCOPE
from sampark.registry.keys import generate_keypair
from sampark.registry.scope import evaluate_scope
from sampark.registry.store import InMemoryAgentRepository, InMemoryRiskItemRepository

FORBIDDEN_PREFIXES = ("sampark.allocator", "sampark.policy", "sampark.mediation", "sampark.budget")


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_enforcement_module_never_imports_the_allocator_or_policy_engine():
    """The stage-two gate must be structurally incapable of scoring,
    ranking, admitting or issuing. It only reads a declared ceiling and a
    counter."""
    modules = _imported_modules(pathlib.Path("sampark/demo/enforcement.py"))
    offending = [m for m in modules if m.startswith(FORBIDDEN_PREFIXES)]
    assert offending == [], "sampark/demo/enforcement.py imports " + repr(offending)


def test_enforcement_only_depends_on_contracts_and_the_registry():
    modules = _imported_modules(pathlib.Path("sampark/demo/enforcement.py"))
    sampark_modules = {m for m in modules if m.startswith("sampark")}
    assert sampark_modules == {
        "sampark.contracts",
        "sampark.registry.store",
        "sampark.registry.strikes",
    }, sampark_modules


@pytest.mark.postgres
def test_no_scope_or_rate_denial_ever_reaches_the_allocator(raw_conn, demo_scenario, monkeypatch):
    """Make the allocator explode, then drive the window that contains both
    stage-one scope violations. Every request in it must be answered without
    the allocator running."""
    import sampark.mediation.service as service

    def explode(*args, **kwargs):
        raise AssertionError("the allocator was invoked for a request denied before it")

    monkeypatch.setattr(service, "filter_and_allocate", explode)

    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()

        window = demo_scenario.windows[0]
        decision_at = demo_scenario.clock.decision_at(window)
        rogue_specs = [s for s in demo_scenario.rogue_requests if s.stage == 1]
        assert rogue_specs, "the scenario must contain stage-one violations"

        for spec in rogue_specs:
            from agents.types import ContactAction
            from agents.mediated import to_grant_request

            action = ContactAction(
                agent_id=ROGUE_AGENT_ID, risk_id=spec.risk_id, customer_id=spec.customer_id,
                channel=spec.channel, intent=spec.intent, incentive_bps=spec.incentive_bps,
                scheduled_at=spec.issued_at,
            )
            request = to_grant_request(action, demo_scenario.seed, runner.keypairs[ROGUE_AGENT_ID])
            runner.audit_sink.record_request_received(request)
            decision = evaluate_scope(request, runner.agent_repo, runner.risk_item_repo)
            assert decision is not None, spec.label + " should have been denied on scope"
            runner.audit_sink.record_denied_on_scope(decision, request, decision_at)

        # And the rate gate, likewise, with the allocator still primed to explode.
        agent = runner.agent_repo.get_agent(ROGUE_AGENT_ID)
        scope = runner.agent_repo.get_capability_scope(ROGUE_AGENT_ID)
        burst = [s for s in demo_scenario.rogue_requests if s.label.startswith("stage2_burst_")]
        denied = 0
        for spec in burst:
            from agents.types import ContactAction
            from agents.mediated import to_grant_request

            action = ContactAction(
                agent_id=ROGUE_AGENT_ID, risk_id=spec.risk_id, customer_id=spec.customer_id,
                channel=spec.channel, intent=spec.intent, incentive_bps=spec.incentive_bps,
                scheduled_at=spec.issued_at,
            )
            request = to_grant_request(action, demo_scenario.seed, runner.keypairs[ROGUE_AGENT_ID])
            if evaluate_scope(request, runner.agent_repo, runner.risk_item_repo) is not None:
                continue
            if evaluate_agent_rate(request, scope, runner.rate_window) is not None:
                denied += 1
                runner._deny_on_rate(request, spec.proposed_send_after, agent, "agent.rate_ceiling_exceeded", decision_at)
                agent = runner.agent_repo.get_agent(ROGUE_AGENT_ID)
        assert denied == 3, "expected three rate-ceiling denials, got " + str(denied)
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


def test_scope_denial_path_is_pure_and_needs_no_allocator_at_all():
    """The same claim in miniature, with no database and no runner: a real
    keypair, a real registration, a real signature, an out-of-scope request."""
    keypair = generate_keypair()
    agent_repo = InMemoryAgentRepository()
    agent_repo.register(
        Agent(agent_id=ROGUE_AGENT_ID, public_key=keypair.public_key_b64,
              publisher="Third-Party Recovery Co", state=AgentState.ACTIVE, strike_count=0),
        ROGUE_SCOPE,
    )
    risk_repo = InMemoryRiskItemRepository()
    risk_repo.add(
        RiskItem(risk_id="r1", source="abandoned_checkout", amount_paise=15_000,
                 root_cause="price_hesitation",
                 detected_at=dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)),
        customer_id="c1",
    )
    unsigned = GrantRequest(
        request_id=uuid.uuid4(), agent_id=ROGUE_AGENT_ID, customer_id="c1", risk_id="r1",
        intent="cart_recovery", requested_channel="voice", requested_max_incentive_bps=100,
        issued_at=dt.datetime(2025, 9, 10, 10, 0, tzinfo=dt.timezone.utc), signature="placeholder",
    )
    request = unsigned.model_copy(update={"signature": keypair.sign(unsigned.canonical_bytes())})

    decision = evaluate_scope(request, agent_repo, risk_repo)
    assert decision is not None and decision.reason_code == "scope.channel_not_allowed"
    assert decision.grant is None
