"""Phase 3 exit criterion (spec §18.1's phase table; CLAUDE.md §15):

    An out-of-scope request is rejected on signature-verified scope
    alone, with NO allocator involvement.

This is the file this phase is graded on. It uses a real Ed25519
keypair, a real registration, a genuinely valid signature, and a
deliberately out-of-scope request — mirroring spec §12.3's two-stage
rogue-agent demo, stage one: "attempts a voice channel it never
declared" — then proves both the denial and the structural absence of
any allocator dependency in the code path that produced it.
"""

from __future__ import annotations

import ast
import datetime as dt
import inspect
from uuid import uuid4

from sampark.contracts import Agent, AgentState, CapabilityScope, DecisionOutcome, GrantRequest, RiskItem
from sampark.registry import InMemoryAgentRepository, InMemoryRiskItemRepository, generate_keypair, reason_codes
from sampark.registry.scope import evaluate_scope


def test_out_of_scope_request_is_rejected_with_no_allocator_involvement():
    # 1. A real Ed25519 keypair.
    keypair = generate_keypair()

    # 2. Registration: a narrow, real capability scope. This agent never
    #    declared "voice".
    agent_repo = InMemoryAgentRepository()
    agent = Agent(
        agent_id="rogue-agent",
        public_key=keypair.public_key_b64,
        publisher="Third-Party Recovery Co",
        state=AgentState.ACTIVE,
        strike_count=0,
    )
    scope = CapabilityScope(
        allowed_channels=["sms"],
        allowed_intents=["recover_cart"],
        allowed_risk_sources=["abandoned_checkout"],
        max_incentive_bps=200,
        max_requests_per_hour=10,
    )
    agent_repo.register(agent, scope)

    risk_item_repo = InMemoryRiskItemRepository()
    risk_item = RiskItem(
        risk_id="risk-1",
        source="abandoned_checkout",
        amount_paise=15_000,
        root_cause="price_hesitation",
        detected_at=dt.datetime(2026, 8, 24, 9, 0, tzinfo=dt.timezone.utc),
    )
    risk_item_repo.add(risk_item, customer_id="cust-1")

    # 3. A genuinely, correctly signed request -- for "voice", outside
    #    this agent's declared scope.
    unsigned = GrantRequest(
        request_id=uuid4(),
        agent_id="rogue-agent",
        customer_id="cust-1",
        risk_id="risk-1",
        intent="recover_cart",
        requested_channel="voice",
        requested_max_incentive_bps=100,
        issued_at=dt.datetime(2026, 8, 25, 9, 0, tzinfo=dt.timezone.utc),
        signature="placeholder",
    )
    signature = keypair.sign(unsigned.canonical_bytes())
    request = unsigned.model_copy(update={"signature": signature})

    # 4. Evaluate.
    decision = evaluate_scope(request, agent_repo, risk_item_repo)

    # 5. DENIED on scope alone -- not unknown_agent, not invalid_signature,
    #    not agent_revoked. The signature really did verify; the scope
    #    check is what caught it.
    assert decision is not None
    assert decision.outcome is DecisionOutcome.DENIED
    assert decision.reason_code == reason_codes.CHANNEL_NOT_ALLOWED
    assert decision.grant is None
    assert decision.request_id == request.request_id

    # 6. No allocator involvement -- structural, not incidental. Parses
    #    scope.py's own source and asserts its import statements contain
    #    nothing allocator-shaped, so this holds even after an allocator
    #    package exists elsewhere in the repo (Phase 4), not just because
    #    one happens to be absent today.
    from sampark.registry import scope as scope_module

    tree = ast.parse(inspect.getsource(scope_module))
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    assert not any("allocator" in name.lower() for name in imported_names), (
        f"sampark.registry.scope must never import an allocator-shaped "
        f"module; found {imported_names}"
    )

    # ...and evaluate_scope's own parameter list has no allocator-shaped
    # dependency it could even call.
    params = list(inspect.signature(evaluate_scope).parameters)
    assert not any("alloc" in p.lower() for p in params)
