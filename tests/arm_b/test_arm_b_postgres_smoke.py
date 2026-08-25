"""Arm B — small real-PostgreSQL integration smoke test (Phase 4B-2).

NOT the five-seed evidence run (explicitly out of scope for this task).
Proves the full chain end to end, at small scale, with the REAL
PostgreSQL issuance transaction in the loop:

    agents.select_actions()
        -> agents/mediated.py (signed GrantRequest)
        -> sampark.registry.scope.evaluate_scope (real Phase 3, Postgres-backed)
        -> sampark.policy.hard (Phase 4 policy, via InMemoryMediationLedger's
           read-side, seeded from the same small dataset)
        -> sampark.allocator.greedy (Phase 4 allocator)
        -> sampark.budget.issuance.PostgresGrantIssuer (REAL PostgreSQL,
           SERIALIZABLE transaction — not the in-memory reference)
        -> agents.channel.MockChannelAdapter + sim.environment.Environment.observe
        -> sim.metrics.compute_metrics

Also proves the specific claim: swapping InMemoryGrantIssuer for
PostgresGrantIssuer required a `conn=` argument at the call site, but
NO change to sampark.allocator.greedy / sampark.mediation.service's
function signatures or the GrantIssuer protocol itself.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

import psycopg
import pytest

from agents import CartRecoveryAgent, MandateRecoveryAgent, PaymentRetryAgent, ReceivablesAgent
from agents.channel import MockChannelAdapter
from agents.mediated import to_grant_request
from agents.types import ContactAction, LedgerView
from sampark.allocator.constants import IST
from sampark.budget.issuance import PostgresGrantIssuer
from sampark.budget.store import InMemoryMediationLedger
from sampark.budget.windows import window_id_for, window_start_for
from sampark.contracts import CapabilityScope, Customer, DecisionOutcome, RiskItem
from sampark.mediation.service import mediate_window
from sampark.registry.keys import generate_keypair
from sampark.registry.store import PostgresAgentRepository, PostgresRiskItemRepository
from sim.environment import Environment
from sim.metrics import compute_metrics
from sim.persistence import PostgresConfig, PostgresConfigError
from sim.population import HiddenResponseProfile, Population, Person

pytestmark = pytest.mark.postgres

SEED = 999_001
DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def _connect_or_skip() -> psycopg.Connection:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip(f"Postgres not configured: {exc}")
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.contact_slot_claims')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("Phase 4 schema additions have not been applied to this database")
    return conn


_AGENTS = (PaymentRetryAgent(), CartRecoveryAgent(), MandateRecoveryAgent(), ReceivablesAgent())

_SCOPES = {
    "payment_retry_agent": CapabilityScope(
        allowed_channels=["sms"], allowed_intents=["payment_retry"],
        allowed_risk_sources=["failed_payment"], max_incentive_bps=0, max_requests_per_hour=1000,
    ),
    "cart_recovery_agent": CapabilityScope(
        allowed_channels=["whatsapp"], allowed_intents=["cart_recovery"],
        allowed_risk_sources=["abandoned_checkout"], max_incentive_bps=500, max_requests_per_hour=1000,
    ),
    "mandate_recovery_agent": CapabilityScope(
        allowed_channels=["whatsapp"], allowed_intents=["mandate_retry"],
        allowed_risk_sources=["mandate_failure"], max_incentive_bps=200, max_requests_per_hour=1000,
    ),
    "receivables_agent": CapabilityScope(
        allowed_channels=["voice"], allowed_intents=["receivables_followup"],
        allowed_risk_sources=["overdue_invoice"], max_incentive_bps=0, max_requests_per_hour=1000,
    ),
}

_ADAPTERS = {
    "sms": MockChannelAdapter("sms"), "whatsapp": MockChannelAdapter("whatsapp"), "voice": MockChannelAdapter("voice"),
}


@pytest.fixture()
def smoke_env():
    conn = _connect_or_skip()
    suffix = uuid.uuid4().hex[:10]
    customer_ids = [f"smoke-arm-b-{suffix}-{i}" for i in range(4)]  # one per source
    risk_items: list[RiskItem] = []
    keypairs = {}

    detected_at = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)
    sources = [
        ("failed_payment", "insufficient_funds", "payment_retry_agent"),
        ("abandoned_checkout", "price_hesitation", "cart_recovery_agent"),
        ("mandate_failure", "mandate_expired", "mandate_recovery_agent"),
        ("overdue_invoice", "disputed", "receivables_agent"),
    ]

    with conn.cursor() as cur:
        # Defensive: the OFFICIAL Postgres Arm B path (sim/arm_b_cli.py)
        # registers these SAME four global agent_ids with deterministic
        # per-seed keypairs and deliberately leaves them registered
        # (Phase 4C-2, Blocker 1 — reusable registry reference data, not
        # per-run transactional state). This fixture generates its OWN
        # random keypair for each agent, so any pre-existing row for
        # these agent_ids — from an official run, or a prior interrupted
        # test — must be cleared first, or this fixture's INSERT either
        # collides on the primary key or (with ON CONFLICT DO NOTHING)
        # silently signs requests with a keypair that does not match
        # what is actually registered.
        cur.execute("DELETE FROM agents WHERE agent_id = ANY(%s)", (list(_SCOPES.keys()),))
        for customer_id in customer_ids:
            cur.execute("INSERT INTO customers (customer_id) VALUES (%s)", (customer_id,))
            cur.execute(
                "INSERT INTO contact_states (customer_id, contacts_24h, contacts_7d, "
                "optouts_by_channel, consent_scopes, fatigue_score) VALUES (%s,0,0,'{}','{}',0.0)",
                (customer_id,),
            )
        for (source, root_cause, agent_id), customer_id in zip(sources, customer_ids):
            risk_id = f"smoke-risk-{suffix}-{source}"
            cur.execute(
                "INSERT INTO risk_items (risk_id, customer_id, source, amount_paise, root_cause, detected_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (risk_id, customer_id, source, 1_000_000, root_cause, detected_at),
            )
            risk_items.append(
                RiskItem(risk_id=risk_id, source=source, amount_paise=1_000_000, root_cause=root_cause, detected_at=detected_at)
            )

        for agent_id, scope in _SCOPES.items():
            keypair = generate_keypair()
            keypairs[agent_id] = keypair
            cur.execute(
                "INSERT INTO agents (agent_id, public_key, publisher, state, strike_count) "
                "VALUES (%s,%s,'smoke-test','ACTIVE',0)",
                (agent_id, keypair.public_key_b64),
            )
            cur.execute(
                "INSERT INTO capability_scopes "
                "(agent_id, allowed_channels, allowed_intents, allowed_risk_sources, "
                " max_incentive_bps, max_requests_per_hour) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    agent_id, json.dumps(scope.allowed_channels), json.dumps(scope.allowed_intents),
                    json.dumps(scope.allowed_risk_sources), scope.max_incentive_bps, scope.max_requests_per_hour,
                ),
            )

    try:
        yield conn, customer_ids, risk_items, keypairs, suffix
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM contact_slot_claims WHERE customer_id = ANY(%s)", (customer_ids,))
            cur.execute(
                "DELETE FROM grants WHERE request_id IN (SELECT request_id FROM grant_requests WHERE customer_id = ANY(%s))",
                (customer_ids,),
            )
            cur.execute("DELETE FROM grant_requests WHERE customer_id = ANY(%s)", (customer_ids,))
            cur.execute("DELETE FROM customer_margin_windows WHERE customer_id = ANY(%s)", (customer_ids,))
            cur.execute(
                "DELETE FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s",
                (dt.date(2025, 9, 10),),
            )
            cur.execute("DELETE FROM risk_items WHERE customer_id = ANY(%s)", (customer_ids,))
            cur.execute("DELETE FROM contact_states WHERE customer_id = ANY(%s)", (customer_ids,))
            cur.execute("DELETE FROM customers WHERE customer_id = ANY(%s)", (customer_ids,))
            cur.execute("DELETE FROM agents WHERE agent_id = ANY(%s)", (list(_SCOPES.keys()),))

            # W9 hardening: an explicit, self-verifying tripwire — rather
            # than trusting the DELETE statements above ran against the
            # right rows, actually check no smoke-* row survives this
            # fixture's own suffix. A background-task interruption (a
            # SIGKILL skips Python's `finally`) can still leave residue
            # from a DIFFERENT run, which this cannot detect — but it DOES
            # catch a regression in this fixture's own cleanup logic,
            # which is the failure mode this hardening targets.
            cur.execute(
                "SELECT customer_id FROM customers WHERE customer_id = ANY(%s)", (customer_ids,)
            )
            leftover_customers = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT risk_id FROM risk_items WHERE risk_id = ANY(%s)",
                ([item.risk_id for item in risk_items],),
            )
            leftover_risk_items = [r[0] for r in cur.fetchall()]
            assert not leftover_customers and not leftover_risk_items, (
                f"smoke test cleanup left rows behind: customers={leftover_customers} "
                f"risk_items={leftover_risk_items}"
            )
        conn.close()


def test_full_chain_agents_through_postgres_issuance_to_metrics(smoke_env):
    conn, customer_ids, risk_items, keypairs, suffix = smoke_env

    # --- agents.select_actions() — the real, unmodified Phase 2 agents ---
    risk_items_by_source = {item.source: (item,) for item in risk_items}
    customer_id_by_risk_id = {item.risk_id: cid for item, cid in zip(risk_items, customer_ids)}
    view = LedgerView(
        customers_by_id={cid: Customer(customer_id=cid) for cid in customer_ids},
        risk_items_by_source=risk_items_by_source,
        customer_id_by_risk_id=customer_id_by_risk_id,
    )
    actions: list[ContactAction] = []
    for agent in _AGENTS:
        actions.extend(agent.select_actions(view))
    assert len(actions) == 4  # one per source, small by design

    # --- agents/mediated.py: signed GrantRequest ---
    new_requests = [
        (to_grant_request(action, SEED, keypairs[action.agent_id]), action.scheduled_at)
        for action in actions
    ]

    # --- Phase 3: real Postgres-backed registry ---
    agent_repo = PostgresAgentRepository(conn)
    risk_item_repo = PostgresRiskItemRepository(conn)

    # --- Phase 4 policy/allocator: InMemoryMediationLedger read-side,
    #     seeded from this same small dataset ---
    ledger = InMemoryMediationLedger(
        {cid: (item,) for cid, item in zip(customer_ids, risk_items)},
        merchant_budget_paise_per_window=10_000_000,
    )

    # --- issuance: REAL PostgreSQL, via PostgresGrantIssuer ---
    issuer = PostgresGrantIssuer()

    decision_at = window_start_for(window_id_for(actions[0].scheduled_at))
    result = mediate_window(
        tuple(new_requests), (), agent_repo, risk_item_repo, ledger, issuer, decision_at, conn=conn,
    )

    assert len(result.decisions) == 4
    scope_denied = [d for d in result.decisions if d.reason_code and d.reason_code.startswith("scope.")]
    assert scope_denied == [], "the four well-behaved agents must never be denied on scope"

    granted = [d for d in result.decisions if d.outcome is DecisionOutcome.GRANTED]
    assert len(granted) >= 1, "at least one of four independent-source candidates should be admitted and granted"

    # Every GRANTED decision's Grant must be a REAL row in Postgres now.
    with conn.cursor() as cur:
        for decision in granted:
            cur.execute("SELECT state FROM grants WHERE grant_id = %s", (decision.grant.grant_id,))
            row = cur.fetchone()
            assert row is not None, "GRANTED decision must correspond to a real grants row"
            assert row[0] == "RESERVED"

    # --- execute + Environment.observe + confirm, exactly like sim/arm_b.py ---
    population = Population(
        people=tuple(Person(person_id=f"p-{i}", raw_phone=None, raw_email=f"p{i}@x.test") for i in range(len(customer_ids))),
        hidden_response=tuple(
            HiddenResponseProfile(person_id=f"p-{i}", conversion_propensity=0.3, fatigue_hazard=0.1, price_sensitivity=0.3)
            for i in range(len(customer_ids))
        ),
    )
    # Environment needs a customer_id -> profile map; build directly since
    # this smoke test bypasses the full generator/ledger pipeline.
    profile_by_customer = {
        cid: population.hidden_response[i] for i, cid in enumerate(customer_ids)
    }
    import numpy as np
    env = Environment(profile_by_customer, np.random.default_rng(SEED))

    risk_items_by_id = {item.risk_id: item for item in risk_items}
    request_by_id = {req.request_id: req for req, _ in new_requests}
    outcomes = []
    for decision in granted:
        request = request_by_id[decision.request_id]
        effective_bps = result.effective_incentive_bps_by_request_id[decision.request_id]
        action = ContactAction(
            agent_id=request.agent_id, risk_id=request.risk_id, customer_id=request.customer_id,
            channel=decision.grant.channel, intent=request.intent, incentive_bps=effective_bps,
            scheduled_at=decision.grant.send_after,
        )
        # NOTE: lifecycle.py's execute/confirm operate on InMemoryMediationLedger
        # bookkeeping, which the REAL Postgres issuer never populates (the two
        # are independent issuers, deliberately not sharing state in this
        # smoke test) — so state transitions here go straight to Postgres,
        # the actual authority, via the same states lifecycle.py would set.
        _ADAPTERS[decision.grant.channel].send(action)
        outcome = env.observe(action, risk_items_by_id[request.risk_id])
        with conn.cursor() as cur:
            cur.execute("UPDATE grants SET state = 'EXECUTING' WHERE grant_id = %s", (decision.grant.grant_id,))
            cur.execute("UPDATE grants SET state = 'CONFIRMED' WHERE grant_id = %s", (decision.grant.grant_id,))
        outcomes.append(outcome)

    metrics = compute_metrics(tuple(outcomes))
    assert metrics["total_contacts"] == len(granted)
    assert metrics["recovery_unit"] == "risk_item"

    with conn.cursor() as cur:
        for decision in granted:
            cur.execute("SELECT state FROM grants WHERE grant_id = %s", (decision.grant.grant_id,))
            assert cur.fetchone()[0] == "CONFIRMED"
