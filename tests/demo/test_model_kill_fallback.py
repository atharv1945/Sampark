"""Failure 3 — model kill and heuristic degradation (spec §12.3).

    "The allocator degrades to unweighted heuristic ranking, logs a
     degradation event, and keeps issuing grants. Recovery drops; compliance
     does not. That distinction is the whole design philosophy."

The compliance half is the part that matters and the part these tests assert
hardest: killing the model must not produce a single quiet-hour violation, a
single contact-cap breach, or a single unauthorized contact.
"""

from __future__ import annotations

import pytest

from sampark.allocator.scorer import HeuristicScorer, default_scorer
from sampark.audit.emit import (
    MODEL_DEGRADED_ARTIFACT_UNAVAILABLE,
    MODEL_DEGRADED_KILLED_BY_OPERATOR,
)
from sampark.demo import isolation
from sampark.demo.runner import SCRIPTED_MODEL_KILL_WINDOW, DemoRunner
from sampark.demo.scorer_kill import KillableScorer, ModelUnavailableError, initial_degradation_reason


# --------------------------------------------------------------------------
# the wrapper
# --------------------------------------------------------------------------


def test_a_live_wrapper_delegates_verbatim():
    """A run that never kills the scorer must be byte-identical to one that
    passes `inner` directly, or the wrapper would not be safe to leave in the
    normal path."""
    class Spy:
        def __init__(self):
            self.calls = []

        def score(self, candidate, bps, n, others):
            self.calls.append((candidate, bps, n, tuple(others)))
            return "SCORE"

    spy = Spy()
    wrapped = KillableScorer(inner=spy)
    assert wrapped.score("cand", 500, 2, [1, 2]) == "SCORE"
    assert spy.calls == [("cand", 500, 2, (1, 2))]


def test_kill_makes_score_raise_with_the_reason_code():
    wrapped = KillableScorer(inner=default_scorer())
    assert wrapped.killed is False
    wrapped.kill(MODEL_DEGRADED_KILLED_BY_OPERATOR)
    assert wrapped.killed is True
    with pytest.raises(ModelUnavailableError) as exc:
        wrapped.score(None, 0, 0, [])
    assert exc.value.reason_code == MODEL_DEGRADED_KILLED_BY_OPERATOR


def test_it_satisfies_the_phase_6_scorer_protocol():
    from sampark.allocator.scorer import Scorer

    assert isinstance(KillableScorer(inner=default_scorer()), Scorer)


def test_the_dataset_reports_the_real_permanent_degradation_reason():
    """Honesty check. `build_scorer()` already returns a HeuristicScorer on
    this dataset because the uplift T-learner has no control population
    (committed Phase 6 finding). Phase 8 records that as a real degradation
    rather than starting silently degraded — and must never claim the model
    was available."""
    from sampark.models.scorer import build_scorer

    scorer = build_scorer()
    assert isinstance(scorer, HeuristicScorer)
    assert initial_degradation_reason(scorer) == MODEL_DEGRADED_ARTIFACT_UNAVAILABLE


def test_a_genuinely_model_backed_scorer_reports_no_initial_degradation():
    class NotHeuristic:
        def score(self, *a):  # pragma: no cover - shape only
            raise NotImplementedError

    assert initial_degradation_reason(NotHeuristic()) is None


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------


@pytest.mark.postgres
def test_the_kill_is_detected_logged_and_recovered_from(raw_conn, demo_scenario):
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()

        with raw_conn.cursor() as cur:
            cur.execute(
                "SELECT reason_code, payload->>'scorer_after', payload->>'window_id' "
                "FROM audit_events WHERE event_type = 'model.degraded' ORDER BY seq"
            )
            degradations = cur.fetchall()

        reasons = [r for r, _after, _w in degradations]
        assert MODEL_DEGRADED_ARTIFACT_UNAVAILABLE in reasons, (
            "the real, permanent dataset degradation must be recorded at run start"
        )
        assert MODEL_DEGRADED_KILLED_BY_OPERATOR in reasons, (
            "the scripted mid-run kill must be recorded"
        )
        # Fallback target is the frozen Phase 4 heuristic, not something new.
        assert {after for _r, after, _w in degradations} == {"HeuristicScorer"}
        # The kill happened in the window it was scripted for.
        killed_window = next(w for r, _a, w in degradations if r == MODEL_DEGRADED_KILLED_BY_OPERATOR)
        assert killed_window == demo_scenario.windows[SCRIPTED_MODEL_KILL_WINDOW].isoformat()

        assert runner.degraded is True
        assert isinstance(runner.scorer.inner, HeuristicScorer)
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


@pytest.mark.postgres
def test_grants_keep_being_issued_after_the_kill(raw_conn, demo_scenario):
    """Degradation must not stop the system — it must make it dumber, safely."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        kill_window = demo_scenario.windows[SCRIPTED_MODEL_KILL_WINDOW]
        with raw_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM audit_events WHERE event_type = 'grant.reserved' "
                "AND (payload->>'window_id')::date >= %s", (kill_window,)
            )
            assert cur.fetchone()[0] > 0, "no grants were issued after the model was killed"
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


@pytest.mark.postgres
def test_compliance_is_intact_across_the_degradation(raw_conn, demo_scenario):
    """The whole point of §12.3 failure 3. Recovery quality may drop;
    regulatory compliance may not."""
    from sampark.budget.windows import is_quiet_hours

    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()

        with raw_conn.cursor() as cur:
            # 1. No grant was ever scheduled inside the TCCCPR blackout.
            cur.execute("SELECT send_after FROM grants")
            for (send_after,) in cur.fetchall():
                assert not is_quiet_hours(send_after), "a grant was scheduled in quiet hours"

            # 2. The contact cap (1 per 24h) was never breached: at most one
            #    capacity-consuming claim per customer per window.
            cur.execute(
                "SELECT customer_id, window_id, count(*) FROM contact_slot_claims "
                "WHERE state IN ('RESERVED','EXECUTING','CONFIRMED') GROUP BY 1,2 HAVING count(*) > 1"
            )
            assert cur.fetchall() == [], "more than one active contact slot for a customer-window"

            # 3. No opted-out contact, and no contact to a customer outside
            #    the demo world.
            cur.execute("SELECT DISTINCT customer_id FROM grant_requests")
            contacted = {r[0] for r in cur.fetchall()}
            assert contacted <= set(demo_scenario.customer_ids)

            # 4. Zero scope violations by the four honest agents.
            cur.execute(
                "SELECT count(*) FROM audit_events WHERE event_type = 'request.denied_on_scope' "
                "AND payload->>'agent_id' <> 'third_party_recovery_agent'"
            )
            assert cur.fetchone()[0] == 0
    finally:
        isolation.drop_demo_schema(raw_conn, schema)
