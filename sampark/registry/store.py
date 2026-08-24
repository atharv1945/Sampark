"""Agent / CapabilityScope / RiskItem repositories.

Two repository protocols (AgentRepository, RiskItemRepository), each
with an in-memory implementation (no I/O — for scope/strike unit tests)
and a Postgres-backed implementation against sampark/schema.sql's
`agents`, `capability_scopes`, and `risk_items` tables, mirroring
sim/persistence.py's existing connection-and-loader pattern for this
same schema.

Registration writes `agents` and `capability_scopes` together in one
ordinary transaction (default READ COMMITTED) — SERIALIZABLE is reserved
for Phase 4's grant-issuance transaction, a different correctness
problem (concurrent contact-slot contention) that does not exist at
registration time. Re-registering an agent_id with identical fields is
a safe, idempotent no-op; re-registering it with any differing field
raises AgentRegistrationConflictError and writes nothing.

RiskItemRepository is read-only: risk_items is Phase 1 data
(sim/persistence.py owns writing it); Phase 3 never writes to it, and
never writes grant_requests either — this module has no INSERT path for
either table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import psycopg

from sampark.contracts import Agent, AgentState, CapabilityScope, RiskItem


class AgentRegistrationConflictError(RuntimeError):
    """A registration disagrees with an already-registered agent_id.

    Raised instead of silently overwriting an existing agent's public
    key, publisher, or capability scope.
    """


@dataclass(frozen=True)
class RiskItemRecord:
    """A RiskItem plus the customer_id that owns it.

    RiskItem itself deliberately excludes customer_id (CONTRACTS.md) —
    ownership is a relational fact, not a canonical field. The scope
    evaluator needs it to check request.customer_id against the risk
    item's actual owner (LOCKED DECISIONS step 6), so a repository
    lookup has to hand both back together.
    """

    risk_item: RiskItem
    customer_id: str


class AgentRepository(Protocol):
    def get_agent(self, agent_id: str) -> Agent | None: ...
    def get_capability_scope(self, agent_id: str) -> CapabilityScope | None: ...
    def register(self, agent: Agent, scope: CapabilityScope) -> None: ...
    def save_agent(self, agent: Agent) -> None: ...


class RiskItemRepository(Protocol):
    def get_risk_item(self, risk_id: str) -> RiskItemRecord | None: ...


class InMemoryAgentRepository:
    """Dict-backed AgentRepository — no I/O, for scope/strike unit tests."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._scopes: dict[str, CapabilityScope] = {}

    def get_agent(self, agent_id: str) -> Agent | None:
        return self._agents.get(agent_id)

    def get_capability_scope(self, agent_id: str) -> CapabilityScope | None:
        return self._scopes.get(agent_id)

    def register(self, agent: Agent, scope: CapabilityScope) -> None:
        existing_agent = self._agents.get(agent.agent_id)
        if existing_agent is not None:
            existing_scope = self._scopes[agent.agent_id]
            if existing_agent == agent and existing_scope == scope:
                return  # identical re-registration: idempotent no-op
            raise AgentRegistrationConflictError(
                f"agent_id {agent.agent_id!r} is already registered with different fields"
            )
        self._agents[agent.agent_id] = agent
        self._scopes[agent.agent_id] = scope

    def save_agent(self, agent: Agent) -> None:
        if agent.agent_id not in self._agents:
            raise KeyError(f"unknown agent_id: {agent.agent_id!r}")
        self._agents[agent.agent_id] = agent


class InMemoryRiskItemRepository:
    """Dict-backed RiskItemRepository — no I/O, for scope unit tests."""

    def __init__(self) -> None:
        self._records: dict[str, RiskItemRecord] = {}

    def add(self, risk_item: RiskItem, customer_id: str) -> None:
        self._records[risk_item.risk_id] = RiskItemRecord(risk_item, customer_id)

    def get_risk_item(self, risk_id: str) -> RiskItemRecord | None:
        return self._records.get(risk_id)


class PostgresAgentRepository:
    """Postgres-backed AgentRepository against `agents` / `capability_scopes`.

    Every method issues its own round trip and commits its own write;
    the caller owns the psycopg.Connection's lifetime.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get_agent(self, agent_id: str) -> Agent | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT public_key, publisher, state, strike_count "
                "FROM agents WHERE agent_id = %s",
                (agent_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        public_key, publisher, state, strike_count = row
        return Agent(
            agent_id=agent_id,
            public_key=public_key,
            publisher=publisher,
            state=AgentState(state),
            strike_count=strike_count,
        )

    def get_capability_scope(self, agent_id: str) -> CapabilityScope | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT allowed_channels, allowed_intents, allowed_risk_sources, "
                "max_incentive_bps, max_requests_per_hour "
                "FROM capability_scopes WHERE agent_id = %s",
                (agent_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        channels, intents, sources, max_incentive_bps, max_requests_per_hour = row
        return CapabilityScope(
            allowed_channels=channels,
            allowed_intents=intents,
            allowed_risk_sources=sources,
            max_incentive_bps=max_incentive_bps,
            max_requests_per_hour=max_requests_per_hour,
        )

    def register(self, agent: Agent, scope: CapabilityScope) -> None:
        existing_agent = self.get_agent(agent.agent_id)
        existing_scope = self.get_capability_scope(agent.agent_id)

        if existing_agent is not None or existing_scope is not None:
            if existing_agent == agent and existing_scope == scope:
                return  # identical re-registration: idempotent no-op, nothing written
            raise AgentRegistrationConflictError(
                f"agent_id {agent.agent_id!r} is already registered with different fields"
            )

        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agents (agent_id, public_key, publisher, state, strike_count) "
                "VALUES (%s, %s, %s, %s, %s)",
                (agent.agent_id, agent.public_key, agent.publisher, agent.state.value, agent.strike_count),
            )
            cur.execute(
                "INSERT INTO capability_scopes "
                "(agent_id, allowed_channels, allowed_intents, allowed_risk_sources, "
                "max_incentive_bps, max_requests_per_hour) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    agent.agent_id,
                    json.dumps(scope.allowed_channels),
                    json.dumps(scope.allowed_intents),
                    json.dumps(scope.allowed_risk_sources),
                    scope.max_incentive_bps,
                    scope.max_requests_per_hour,
                ),
            )
        self._conn.commit()

    def save_agent(self, agent: Agent) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "UPDATE agents SET state = %s, strike_count = %s WHERE agent_id = %s",
                (agent.state.value, agent.strike_count, agent.agent_id),
            )
            updated = cur.rowcount
        if updated == 0:
            self._conn.rollback()
            raise KeyError(f"unknown agent_id: {agent.agent_id!r}")
        self._conn.commit()


class PostgresRiskItemRepository:
    """Read-only Postgres-backed RiskItemRepository against `risk_items`.

    Resolves the authoritative risk_source for a request's risk_id —
    LOCKED DECISIONS: never trust a request-declared source, and
    GrantRequest never carries one in the first place.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get_risk_item(self, risk_id: str) -> RiskItemRecord | None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id, source, amount_paise, root_cause, detected_at "
                "FROM risk_items WHERE risk_id = %s",
                (risk_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        customer_id, source, amount_paise, root_cause, detected_at = row
        risk_item = RiskItem(
            risk_id=risk_id,
            source=source,
            amount_paise=amount_paise,
            root_cause=root_cause,
            detected_at=detected_at,
        )
        return RiskItemRecord(risk_item=risk_item, customer_id=customer_id)
