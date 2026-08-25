"""Determinism of the OFFICIAL PostgreSQL Arm B path — Phase 4C
pre-evidence hardening (W6).

Design Lock §16 / §20 step 8 requires each arm run TWICE at one seed
with metrics diffed. That was previously automated for the "memory"
backend only (tests/arm_b/test_arm_b_runner.py) — never for the actual
evidence-producing path, `sim.arm_b._run_arm_b_postgres` (what
`run_arm_b(seed, backend="postgres")` / the official
`sim/arm_b_cli.py` delegate to).

A full seed-42 Postgres run takes ~10 minutes — too expensive to run
TWICE in a unit test. This exercises the SAME official function
(`_run_arm_b_postgres`, including the real `PostgresMediationLedger`,
`PostgresGrantIssuer`/SERIALIZABLE issuance, `seed_budget_window`
pre-seeding, deterministic per-seed agent keypairs, and
`_cleanup_postgres_run`) against a small, hand-built fixture instead of
the full generator output — same code path, controlled scale:

    - 4 agents (all four Phase 2 RecoveryAgent classes)
    - 5 candidates across 5 customers: one per agent's source, PLUS a
      second `abandoned_checkout` item at a tiny amount (100 paise) for
      a 5th customer, guaranteed DENIED (negative expected_net) —
      giving at least one denial alongside at least one (incentive-
      bearing) grant
    - every GRANTED decision goes through a real lifecycle transition
      (RESERVED -> EXECUTING -> CONFIRMED) inside `_run_window_loop`

Runs the identical fixture through `_run_arm_b_postgres` TWICE
(sequentially — `_cleanup_postgres_run` clears the transactional rows
between runs, `load_ledger`'s reference-data insert is idempotent) and
asserts byte-identical decisions, outcomes, and metrics.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid

import numpy as np
import pytest

from agents import CartRecoveryAgent, MandateRecoveryAgent, PaymentRetryAgent, ReceivablesAgent
from agents.types import LedgerView
from sampark.allocator.constants import AGING_BONUS_PAISE, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.contracts import ContactState, Customer, RiskItem
from sim.arm_b import BACKEND_POSTGRES, _run_arm_b_postgres
from sim.environment import Environment
from sim.ledger import Ledger
from sim.metrics import compute_metrics
from sim.persistence import PostgresConfig, PostgresConfigError
from sim.population import HiddenResponseProfile

pytestmark = pytest.mark.postgres

SEED = 42  # reuses whatever this seed's 4 global agent_ids are ALREADY
# registered under in this Postgres instance (idempotent either way —
# see module docstring) rather than risking an AgentRegistrationConflictError
# against a different seed's already-persisted deterministic keypairs for
# the SAME shared agent_ids.

_AGENTS = (PaymentRetryAgent(), CartRecoveryAgent(), MandateRecoveryAgent(), ReceivablesAgent())
_SOURCES = [
    ("failed_payment", "insufficient_funds"),
    ("abandoned_checkout", "price_hesitation"),
    ("mandate_failure", "mandate_expired"),
    ("overdue_invoice", "disputed"),
]


def _connect_or_skip():
    import psycopg

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


def _build_fixture(suffix: str):
    detected_at = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)
    customer_ids = [f"determinism-test-{suffix}-{i}" for i in range(5)]
    items: list[RiskItem] = []
    customer_id_by_risk_id: dict[str, str] = {}

    for i, (source, root_cause) in enumerate(_SOURCES):
        risk_id = f"determinism-risk-{suffix}-{source}"
        items.append(RiskItem(risk_id=risk_id, source=source, amount_paise=1_000_000, root_cause=root_cause, detected_at=detected_at))
        customer_id_by_risk_id[risk_id] = customer_ids[i]

    # 5th customer: a SECOND abandoned_checkout item, tiny amount ->
    # guaranteed DENIED (allocation.negative_expected_net), independent
    # of RNG — see tests/allocator/test_greedy.py's identical pattern.
    tiny_risk_id = f"determinism-risk-{suffix}-abandoned_checkout-tiny"
    items.append(RiskItem(risk_id=tiny_risk_id, source="abandoned_checkout", amount_paise=100, root_cause="price_hesitation", detected_at=detected_at))
    customer_id_by_risk_id[tiny_risk_id] = customer_ids[4]

    customers = tuple(Customer(customer_id=cid) for cid in customer_ids)
    contact_states = {
        cid: ContactState(contacts_24h=0, contacts_7d=0, optouts_by_channel={}, consent_scopes={}, fatigue_score=0.0)
        for cid in customer_ids
    }
    ledger = Ledger(
        customers=customers, contact_states=contact_states,
        risk_items=tuple(items), risk_customer_map=customer_id_by_risk_id,
    )

    risk_items_by_source: dict[str, list[RiskItem]] = {}
    for item in items:
        risk_items_by_source.setdefault(item.source, []).append(item)
    view = LedgerView(
        customers_by_id={c.customer_id: c for c in customers},
        risk_items_by_source={s: tuple(v) for s, v in risk_items_by_source.items()},
        customer_id_by_risk_id=customer_id_by_risk_id,
    )

    profile_by_customer = {
        cid: HiddenResponseProfile(person_id=cid, conversion_propensity=0.3, fatigue_hazard=0.1, price_sensitivity=0.3)
        for cid in customer_ids
    }

    all_actions = []
    for agent in _AGENTS:
        all_actions.extend(agent.select_actions(view))

    return ledger, all_actions, profile_by_customer, customer_ids


def _run_once(ledger, all_actions, profile_by_customer):
    environment = Environment(dict(profile_by_customer), np.random.default_rng(SEED))
    return _run_arm_b_postgres(
        SEED, ledger, None, environment, all_actions, AGING_BONUS_PAISE, False, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
    )


def _cleanup_reference_data(conn, customer_ids: list[str], risk_ids: list[str]) -> None:
    """_run_arm_b_postgres's own _cleanup_postgres_run clears
    transactional rows (grant_requests/grants/claims/margin windows)
    after each call, but deliberately leaves customers/risk_items/
    contact_states as reusable reference data (Phase 1's committed-
    generator pattern) — this fixture's rows are throwaway, unlike a
    real seed's, so this test cleans them up itself."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM risk_items WHERE risk_id = ANY(%s)", (risk_ids,))
        cur.execute("DELETE FROM contact_states WHERE customer_id = ANY(%s)", (customer_ids,))
        cur.execute("DELETE FROM customers WHERE customer_id = ANY(%s)", (customer_ids,))


def test_official_postgres_arm_b_path_is_deterministic_across_two_runs():
    conn = _connect_or_skip()
    conn.close()  # only used to skip-check; _run_arm_b_postgres opens/closes its own connection

    suffix = uuid.uuid4().hex[:10]
    ledger, all_actions, profile_by_customer, customer_ids = _build_fixture(suffix)
    risk_ids = [item.risk_id for item in ledger.risk_items]
    assert len(all_actions) == 5  # 4 sources + the tiny competing abandoned_checkout item

    conn = _connect_or_skip()
    try:
        result1 = _run_once(ledger, all_actions, profile_by_customer)
        result2 = _run_once(ledger, all_actions, profile_by_customer)
    finally:
        _cleanup_reference_data(conn, customer_ids, risk_ids)
        conn.close()

    assert result1.backend == BACKEND_POSTGRES
    assert result2.backend == BACKEND_POSTGRES

    # --- the actual invariants under test ---

    outcomes1 = tuple(dataclasses.astuple(o) for o in result1.outcomes)
    outcomes2 = tuple(dataclasses.astuple(o) for o in result2.outcomes)
    assert outcomes1 == outcomes2, "identical fixture, identical seed -> byte-identical ContactOutcomes"

    decisions1 = tuple((d.decision_id, d.request_id, d.outcome, d.reason_code) for d in result1.decisions)
    decisions2 = tuple((d.decision_id, d.request_id, d.outcome, d.reason_code) for d in result2.decisions)
    assert decisions1 == decisions2, "identical fixture, identical seed -> byte-identical decision outcomes"

    assert compute_metrics(result1.outcomes) == compute_metrics(result2.outcomes)

    # --- and the fixture actually exercises what the docstring claims ---
    granted = [d for d in result1.decisions if d.outcome.value == "GRANTED"]
    denied = [d for d in result1.decisions if d.outcome.value == "DENIED"]
    assert len(granted) >= 1, "at least one grant"
    assert len(denied) >= 1, "at least one denial (the tiny negative-expected-net candidate)"
    assert any(d.grant.incentive_ceiling_paise > 0 for d in granted), "at least one incentive-bearing grant"
