"""Failure 2 — the two-stage rogue agent (spec §12.3).

    STAGE 1  outside its scope. Registry denies. NO allocator involvement.
             NO strike.
    STAGE 2  inside its scope. The rate ceiling denies. Strikes accumulate.
             The key is revoked. The agent can no longer produce a
             verifiable request.

The contrast between the stages is, per spec §12.3, "the entire thesis in
ninety seconds of screen time", so these tests assert the SEPARATION as
hard as they assert the denials.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from sampark.contracts import Agent, AgentState, CapabilityScope, GrantRequest
from sampark.demo import isolation
from sampark.demo.enforcement import (
    RATE_CEILING_EXCEEDED,
    STAGE_TWO_STRIKE_REASON_CODES,
    AgentRateWindow,
    NotStrikeWorthyError,
    evaluate_agent_rate,
    record_stage_two_strike,
)
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import ROGUE_AGENT_ID, ROGUE_SCOPE
from sampark.registry.store import InMemoryAgentRepository
from sampark.registry.strikes import STRIKE_THRESHOLD

BASE = dt.datetime(2025, 9, 12, 9, 5, tzinfo=dt.timezone.utc)


def _request(i: int, agent_id: str = ROGUE_AGENT_ID, offset_seconds: int = 5) -> GrantRequest:
    return GrantRequest(
        request_id=uuid.uuid5(uuid.NAMESPACE_DNS, agent_id + str(i)),
        agent_id=agent_id, customer_id="c1", risk_id="r" + str(i),
        intent="cart_recovery", requested_channel="whatsapp",
        requested_max_incentive_bps=200,
        issued_at=BASE + dt.timedelta(seconds=offset_seconds * i),
        signature="sig",
    )


# --------------------------------------------------------------------------
# the rate ceiling itself
# --------------------------------------------------------------------------


def test_six_requests_in_one_minute_against_a_ceiling_of_three_denies_exactly_three():
    """spec §12.3: "six perfectly legitimate, correctly-scoped grant requests
    in one minute". With max_requests_per_hour=3 that is exactly
    STRIKE_THRESHOLD denials — the sixth request revokes the key."""
    window = AgentRateWindow()
    results = [evaluate_agent_rate(_request(i), ROGUE_SCOPE, window) for i in range(6)]
    assert results == [None, None, None] + [RATE_CEILING_EXCEEDED] * 3
    assert sum(1 for r in results if r) == STRIKE_THRESHOLD


def test_a_legitimate_agent_is_never_rate_limited():
    """The four honest agents declare max_requests_per_hour=10_000. This is
    the safety property that keeps the demo honest: mediation must not look
    like persecution of well-behaved agents."""
    honest = ROGUE_SCOPE.model_copy(update={"max_requests_per_hour": 10_000})
    window = AgentRateWindow()
    assert all(
        evaluate_agent_rate(_request(i, "cart_recovery_agent"), honest, window) is None
        for i in range(200)
    )


def test_the_window_really_rolls():
    window = AgentRateWindow()
    for i in range(3):
        assert evaluate_agent_rate(_request(i), ROGUE_SCOPE, window) is None
    assert evaluate_agent_rate(_request(3), ROGUE_SCOPE, window) == RATE_CEILING_EXCEEDED
    # ...but two hours later the trailing 60-minute window is empty again.
    later = _request(9, offset_seconds=0)
    later = later.model_copy(update={"issued_at": BASE + dt.timedelta(hours=2)})
    assert evaluate_agent_rate(later, ROGUE_SCOPE, window) is None


def test_rate_evaluation_reads_no_wall_clock():
    """Determinism: the gate is a pure function of the SIMULATED issued_at."""
    a, b = AgentRateWindow(), AgentRateWindow()
    assert [evaluate_agent_rate(_request(i), ROGUE_SCOPE, a) for i in range(6)] == [
        evaluate_agent_rate(_request(i), ROGUE_SCOPE, b) for i in range(6)
    ]


# --------------------------------------------------------------------------
# strikes and revocation
# --------------------------------------------------------------------------


def _repo_with_rogue() -> tuple[InMemoryAgentRepository, Agent]:
    repo = InMemoryAgentRepository()
    agent = Agent(
        agent_id=ROGUE_AGENT_ID, public_key="k", publisher="Third-Party Recovery Co",
        state=AgentState.ACTIVE, strike_count=0,
    )
    repo.register(agent, ROGUE_SCOPE)
    return repo, agent


def test_strikes_accumulate_then_revoke_at_the_threshold():
    repo, _ = _repo_with_rogue()
    seen = []
    for _ in range(STRIKE_THRESHOLD):
        result = record_stage_two_strike(repo, repo.get_agent(ROGUE_AGENT_ID), RATE_CEILING_EXCEEDED)
        seen.append((result.agent.strike_count, result.agent.state, result.newly_revoked))
    assert [s for s, _st, _n in seen] == [1, 2, 3]
    assert seen[-1][1] is AgentState.REVOKED
    assert [n for _s, _st, n in seen] == [False, False, True]
    assert repo.get_agent(ROGUE_AGENT_ID).state is AgentState.REVOKED  # persisted


def test_losing_a_fair_contest_can_never_strike():
    """The single most important safety property of the strike design: the
    codes below are the NORMAL outcome for well-behaved agents and occur in
    the thousands in every committed Arm B run."""
    repo, agent = _repo_with_rogue()
    for reason in (
        "allocation.lost_to_higher_expected_net",
        "budget.contact_cap_24h",
        "budget.contact_slot_taken",
        "budget.merchant_margin_exhausted",
        "policy.quiet_hours",
        "policy.opt_out_active",
        "scope.channel_not_allowed",
        None,
    ):
        with pytest.raises(NotStrikeWorthyError):
            record_stage_two_strike(repo, agent, reason)
    assert repo.get_agent(ROGUE_AGENT_ID).strike_count == 0


def test_the_strike_set_is_exactly_one_code():
    assert STAGE_TWO_STRIKE_REASON_CODES == frozenset({RATE_CEILING_EXCEEDED})


# --------------------------------------------------------------------------
# both stages, end to end, against real Postgres
# --------------------------------------------------------------------------


@pytest.mark.postgres
def test_both_rogue_stages_end_to_end(raw_conn, demo_scenario):
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()

        with raw_conn.cursor() as cur:
            cur.execute(
                "SELECT reason_code, count(*) FROM audit_events "
                "WHERE event_type = 'request.denied_on_scope' GROUP BY 1 ORDER BY 1"
            )
            scope_denials = dict(cur.fetchall())
            cur.execute(
                "SELECT count(*) FROM audit_events WHERE event_type = 'decision.denied' "
                "AND reason_code = %s", (RATE_CEILING_EXCEEDED,)
            )
            rate_denials = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM audit_events WHERE event_type = 'agent.struck'")
            strikes = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM audit_events WHERE event_type = 'agent.revoked'")
            revocations = cur.fetchone()[0]
            cur.execute("SELECT agent_id, state, strike_count FROM agents ORDER BY agent_id")
            agents = {a: (s, n) for a, s, n in cur.fetchall()}

        # STAGE 1 — authorization, two distinct scope violations.
        assert scope_denials.get("scope.channel_not_allowed") == 1
        assert scope_denials.get("scope.incentive_ceiling_exceeded") == 1

        # STAGE 2 — in scope, denied by the rate ceiling, struck, revoked.
        assert rate_denials == STRIKE_THRESHOLD
        assert strikes == STRIKE_THRESHOLD
        assert revocations == 1
        assert agents[ROGUE_AGENT_ID] == ("REVOKED", STRIKE_THRESHOLD)

        # ...and afterwards it cannot produce a verifiable request at all.
        assert scope_denials.get("scope.agent_revoked", 0) >= 1

        # THE SAFETY PROPERTY: the four honest agents are untouched.
        for agent_id, (state, count) in agents.items():
            if agent_id == ROGUE_AGENT_ID:
                continue
            assert state == "ACTIVE" and count == 0, agent_id + " was struck"
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


@pytest.mark.postgres
def test_stage_one_denials_never_produce_a_strike(raw_conn, demo_scenario):
    """A scope denial is authorization, not misbehaviour-with-consequences.
    `record_scope_denial` is deliberately left unwired in Phase 8 so that the
    revocation on screen is unambiguously caused by stage two."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        # Window 0 carries both stage-one violations and no burst.
        runner.status.window_index = 0
        runner.run_window(demo_scenario.windows[0])
        with raw_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM audit_events WHERE event_type = 'request.denied_on_scope'"
            )
            assert cur.fetchone()[0] >= 2
            cur.execute("SELECT count(*) FROM audit_events WHERE event_type = 'agent.struck'")
            assert cur.fetchone()[0] == 0, "a scope denial produced a strike"
            cur.execute("SELECT strike_count FROM agents WHERE agent_id = %s", (ROGUE_AGENT_ID,))
            assert cur.fetchone()[0] == 0
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


def test_record_scope_denial_has_no_production_call_site():
    """Phase 8 deliberately does NOT wire it (see the module docstring in
    sampark/demo/enforcement.py). If a later change wires it, this fails and
    forces the decision to be made deliberately rather than by drift."""
    import ast
    import pathlib

    roots = [pathlib.Path("sampark"), pathlib.Path("ui"), pathlib.Path("sim"), pathlib.Path("agents")]
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in str(path) or path.name == "strikes.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    name = getattr(func, "attr", None) or getattr(func, "id", None)
                    if name == "record_scope_denial":
                        offenders.append(str(path))
    assert offenders == [], "record_scope_denial gained a production call site: " + repr(offenders)
