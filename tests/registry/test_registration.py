"""sampark/registry/store.py — registration semantics.

In-memory tests cover the repository contract with no external
dependency. The Postgres section proves the same contract against the
real agents/capability_scopes tables (sampark/schema.sql) — skipped, not
failed, when Postgres is unreachable or unmigrated, mirroring
tests/sim_generator/test_postgres_load.py's existing pattern.
"""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from sampark.contracts import Agent, AgentState, CapabilityScope
from sampark.registry import (
    AgentRegistrationConflictError,
    InMemoryAgentRepository,
    PostgresAgentRepository,
)
from sim.persistence import PostgresConfig, PostgresConfigError


def _agent(agent_id: str, public_key: str = "pk-b64", publisher: str = "Acme") -> Agent:
    return Agent(
        agent_id=agent_id, public_key=public_key, publisher=publisher,
        state=AgentState.ACTIVE, strike_count=0,
    )


def _scope(max_incentive_bps: int = 200) -> CapabilityScope:
    return CapabilityScope(
        allowed_channels=["sms"], allowed_intents=["recover_cart"],
        allowed_risk_sources=["abandoned_checkout"],
        max_incentive_bps=max_incentive_bps, max_requests_per_hour=10,
    )


# --- in-memory ---------------------------------------------------------


def test_registration_persists_agent_and_scope():
    repo = InMemoryAgentRepository()
    agent, scope = _agent("agent-1"), _scope()

    repo.register(agent, scope)

    assert repo.get_agent("agent-1") == agent
    assert repo.get_capability_scope("agent-1") == scope


def test_identical_reregistration_is_idempotent():
    repo = InMemoryAgentRepository()
    agent, scope = _agent("agent-1"), _scope()

    repo.register(agent, scope)
    repo.register(agent, scope)  # must not raise

    assert repo.get_agent("agent-1") == agent


def test_conflicting_reregistration_raises_and_leaves_original_intact():
    repo = InMemoryAgentRepository()
    repo.register(_agent("agent-1"), _scope())

    with pytest.raises(AgentRegistrationConflictError):
        repo.register(_agent("agent-1", public_key="different-key"), _scope())

    assert repo.get_agent("agent-1").public_key == "pk-b64"


def test_unregistered_agent_lookup_returns_none():
    repo = InMemoryAgentRepository()
    assert repo.get_agent("nobody") is None
    assert repo.get_capability_scope("nobody") is None


# --- Postgres ------------------------------------------------------------


def _connect_or_skip() -> psycopg.Connection:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip(f"Postgres not configured: {exc}")
    try:
        return psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc}")


@pytest.fixture()
def pg_agent():
    """A fresh, uniquely-named agent_id, cleaned up after the test.

    Cleanup is a single DELETE on `agents`; sampark/schema.sql's
    `capability_scopes.agent_id ... ON DELETE CASCADE` removes the
    matching capability_scopes row automatically.
    """
    conn = _connect_or_skip()
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.agents')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("schema.sql has not been applied to this database")
    agent_id = f"test-registry-{uuid4().hex[:12]}"
    try:
        yield conn, agent_id
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))
        conn.commit()
        conn.close()


def test_postgres_registration_persists_and_round_trips(pg_agent):
    conn, agent_id = pg_agent
    repo = PostgresAgentRepository(conn)
    agent, scope = _agent(agent_id), _scope()

    repo.register(agent, scope)

    assert repo.get_agent(agent_id) == agent
    assert repo.get_capability_scope(agent_id) == scope


def test_postgres_identical_reregistration_is_idempotent(pg_agent):
    conn, agent_id = pg_agent
    repo = PostgresAgentRepository(conn)
    agent, scope = _agent(agent_id), _scope()

    repo.register(agent, scope)
    repo.register(agent, scope)  # must not raise

    assert repo.get_agent(agent_id) == agent


def test_postgres_conflicting_reregistration_raises_and_writes_nothing(pg_agent):
    conn, agent_id = pg_agent
    repo = PostgresAgentRepository(conn)
    repo.register(_agent(agent_id), _scope())

    with pytest.raises(AgentRegistrationConflictError):
        repo.register(_agent(agent_id, public_key="different-key"), _scope())

    assert repo.get_agent(agent_id).public_key == "pk-b64"
