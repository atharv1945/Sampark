"""The deterministic demo runner — spec §12.1, §12.3.

This is Phase 8's orchestration, and deliberately nothing more. It owns the
window loop and the order of operations; every DECISION it reports was made
by unmodified Phase 3/4/6 code:

    sampark.registry.scope.evaluate_scope          (stage-one authorization)
    sampark.demo.enforcement.evaluate_agent_rate   (stage two, NEW - the only
                                                    gate Phase 8 adds)
    sampark.mediation.service.mediate_window       (hard filter + allocator)
    sampark.budget.issuance.issue_grant            (SERIALIZABLE reservation)
    sampark.budget.postgres_ledger.execute_grant / confirm_grant /
                                    rollback_grant (lifecycle, incl. the
                                                    compensation path that
                                                    existed but had never
                                                    been called)

--- Ordering, and why request.received is emitted twice ---

Per window the runner:

    1. emits request.received for EVERY arriving request, in a fixed order
    2. runs evaluate_scope; a denial emits request.denied_on_scope and the
       request stops here - the allocator is never reached
    3. runs the rate gate on survivors; a denial emits decision.denied
       (agent.rate_ceiling_exceeded), then a strike, then possibly a
       revocation - the allocator is still never reached
    4. hands the remaining requests to mediate_window as `new_requests`

`mediate_window` will itself call `record_request_received` and
`evaluate_scope` again for those survivors. That is intentional and free:
`event_for_request_received` derives `event_id = uuid5(NS_AUDIT,
"request.received:<request_id>")`, so `chain.append` finds the row already
present and returns `AlreadyAppended` - a no-op. The chain never advances
twice. The cost is one extra call to a pure function per surviving request,
and the benefit is that the Phase 4 decision path runs COMPLETELY unmodified
rather than being partially reimplemented here.

--- Determinism ---

Every instant is simulated and derived from the scenario. Nothing here reads
a wall clock except `time.monotonic()` in the optional presentation pacing,
which cannot reach a decision. Iteration is over sorted sequences only. All
ids are the existing uuid5 derivations. Two runs at one seed with no chaos
input produce the same events, in the same order, with the same ids - and,
because `sim.arm_b._deterministic_keypair` gives reproducible signatures,
the same canonical bytes and the same chain head hash.
"""

from __future__ import annotations

import collections
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Iterable

import psycopg

from agents.mediated import to_grant_request
from agents.types import ContactAction
from sampark.allocator.candidate import Candidate, build_candidate
from sampark.allocator.constants import MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit.sink import PostgresAuditSink
from sampark.budget.issuance import PostgresGrantIssuer
from sampark.budget.postgres_ledger import (
    PostgresMediationLedger,
    confirm_grant,
    execute_grant,
    rollback_grant,
    seed_budget_window,
)
from sampark.budget.store import MERCHANT_ID
from sampark.contracts import Agent, AgentState, DecisionOutcome, GrantDecision, GrantRequest
from sampark.demo import isolation
from sampark.demo.chaos import ChaosControlId, ChaosInapplicableError, ChaosState
from sampark.demo.enforcement import (
    RATE_CEILING_EXCEEDED,
    AgentRateWindow,
    evaluate_agent_rate,
    record_stage_two_strike,
)
from sampark.demo.provider import MAX_ATTEMPTS, MockProvider, ProviderFailureMode, ProviderTimeout
from sampark.demo.scenario import ROGUE_AGENT_ID, DemoScenario, RogueRequestSpec, agent_registrations
from sampark.demo.scorer_kill import (
    MODEL_DEGRADED_KILLED_BY_OPERATOR,
    KillableScorer,
    ModelUnavailableError,
    initial_degradation_reason,
)
from sampark.mediation.service import mediate_window
from sampark.registry.keys import AgentKeypair
from sampark.registry.scope import evaluate_scope
from sampark.registry.store import PostgresAgentRepository, PostgresRiskItemRepository
from sampark.registry.strikes import revoke
from sim.persistence import load_ledger

# Scripted failure injection, so a hands-off 40-second replay demonstrates
# ALL THREE of spec §12.3's failures without anyone touching the chaos panel.
# Window INDEXES (not dates), so the schedule is scenario-independent.
SCRIPTED_PROVIDER_FAILURE_WINDOW = 1
SCRIPTED_MODEL_KILL_WINDOW = 3


@dataclass
class RunnerStatus:
    state: str = "idle"  # idle | preparing | running | finished | failed
    window_index: int = -1
    current_window: date | None = None
    windows_total: int = 0
    error: str | None = None


@dataclass
class DemoRunner:
    """One demo run against one isolated schema.

    `conn` must already have its `search_path` pointed at the demo schema by
    `sampark.demo.isolation.create_demo_schema`.
    """

    conn: psycopg.Connection
    scenario: DemoScenario
    schema: str
    chaos: ChaosState = field(default_factory=ChaosState)
    pace: bool = False  # wall-clock pacing; off for tests, on for the live demo
    # A SECOND connection, used only by `fire_chaos` for its database work.
    # The API drives the runner on a background thread while serving chaos
    # requests on HTTP threads, and a psycopg connection is not safe for
    # concurrent use from two threads. Appending from a different connection
    # is safe by design: `sampark.audit.chain.append` takes
    # `pg_advisory_xact_lock`, which serialises appenders across connections
    # — that is precisely what it exists for. `None` (tests, the CLI) means
    # single-threaded, so the main connection is reused.
    chaos_conn: psycopg.Connection | None = None

    audit_sink: PostgresAuditSink = field(init=False)
    provider: MockProvider = field(init=False)
    scorer: KillableScorer = field(init=False)
    rate_window: AgentRateWindow = field(init=False)
    agent_repo: PostgresAgentRepository = field(init=False)
    risk_item_repo: PostgresRiskItemRepository = field(init=False)
    mediation_ledger: PostgresMediationLedger = field(init=False)
    issuer: PostgresGrantIssuer = field(init=False)
    keypairs: dict[str, AgentKeypair] = field(init=False, default_factory=dict)
    status: RunnerStatus = field(init=False, default_factory=RunnerStatus)

    _degraded: bool = field(init=False, default=False)
    _carried_forward: tuple[Candidate, ...] = field(init=False, default=())
    _extra_requests: dict[date, list[RogueRequestSpec]] = field(init=False, default_factory=dict)
    _quiet_hours_override: dict[date, bool] = field(init=False, default_factory=dict)
    _rollback_count: int = field(init=False, default=0)
    _retry_count: int = field(init=False, default=0)
    # Set by the session when a reset arrives mid-run. Checked between
    # windows so the thread stops at a clean boundary instead of being
    # dropped out from under (which is how a stray row once reached
    # `public.budget_windows` - see isolation.drop_demo_schema's docstring).
    _stop_requested: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        from sampark.models.scorer import build_scorer

        self.audit_sink = PostgresAuditSink(self.conn)
        self.provider = MockProvider()
        self.scorer = KillableScorer(inner=build_scorer())
        self.rate_window = AgentRateWindow()
        self.agent_repo = PostgresAgentRepository(self.conn)
        self.risk_item_repo = PostgresRiskItemRepository(self.conn)
        self.mediation_ledger = PostgresMediationLedger(
            self.conn, MERCHANT_ID, self.scenario.run_seed_risk_ids
        )
        self.issuer = PostgresGrantIssuer()
        self.status.windows_total = len(self.scenario.windows)

    # ------------------------------------------------------------------
    # preparation
    # ------------------------------------------------------------------

    def prepare(self) -> None:
        """Load the subset world, seed budget windows, register the five
        agents, and record any degradation that is ALREADY true."""
        self.status.state = "preparing"
        load_ledger(self.conn, self.scenario.ledger)

        for window in self.scenario.windows:
            seed_budget_window(
                self.conn, MERCHANT_ID, window, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
            )

        registered_at = self.scenario.clock.decision_at(self.scenario.first_window)
        for agent, scope, keypair in agent_registrations(self.scenario.seed):
            self.keypairs[agent.agent_id] = keypair
            self.agent_repo.register(agent, scope)
            self.audit_sink.record_agent_registered(agent, at=registered_at)

        # The uplift model is unavailable on this dataset (committed Phase 6
        # finding). Record that as a real degradation at run start rather
        # than silently beginning in a degraded state - the log should say so.
        reason = initial_degradation_reason(self.scorer.inner)
        if reason is not None:
            self._degraded = True
            self.audit_sink.record_model_degraded(
                reason_code=reason,
                scorer_before="ModelBackedScorer",
                scorer_after=self.scorer.inner_name,
                window_id=self.scenario.first_window,
                at=registered_at,
            )

    # ------------------------------------------------------------------
    # request assembly
    # ------------------------------------------------------------------

    def _honest_requests_for(self, window: date) -> list[tuple[GrantRequest, datetime]]:
        from sim.arm_b import _adjusted_arrival
        from sampark.budget.windows import window_id_for

        out: list[tuple[GrantRequest, datetime]] = []
        for action in self.scenario.honest_actions:
            arrival = _adjusted_arrival(action)
            if window_id_for(arrival) != window:
                continue
            if self._quiet_hours_override.pop(window, False):
                arrival = self.scenario.clock.quiet_hour_instant(window, 21, 40)
            request = to_grant_request(action, self.scenario.seed, self.keypairs[action.agent_id])
            out.append((request, arrival))
        return out

    def _rogue_requests_for(self, window: date) -> list[tuple[GrantRequest, datetime]]:
        from sampark.budget.windows import window_id_for

        specs = [s for s in self.scenario.rogue_requests if window_id_for(s.issued_at) == window]
        specs.extend(self._extra_requests.pop(window, []))
        out: list[tuple[GrantRequest, datetime]] = []
        keypair = self.keypairs[ROGUE_AGENT_ID]
        for spec in sorted(specs, key=lambda s: (s.issued_at, s.label)):
            action = ContactAction(
                agent_id=ROGUE_AGENT_ID,
                risk_id=spec.risk_id,
                customer_id=spec.customer_id,
                channel=spec.channel,
                intent=spec.intent,
                incentive_bps=spec.incentive_bps,
                scheduled_at=spec.issued_at,
            )
            request = to_grant_request(action, self.scenario.seed, keypair)
            out.append((request, spec.proposed_send_after))
        return out

    # ------------------------------------------------------------------
    # the window loop
    # ------------------------------------------------------------------

    def run_window(self, window: date) -> None:
        decision_at = self.scenario.clock.decision_at(window)
        arriving = self._honest_requests_for(window) + self._rogue_requests_for(window)
        arriving.sort(key=lambda pair: (pair[0].issued_at, pair[0].agent_id, pair[0].risk_id))

        # (1) Every arriving request is recorded before anything judges it.
        for request, _arrival in arriving:
            self.audit_sink.record_request_received(request)

        survivors: list[tuple[GrantRequest, datetime]] = []
        for request, arrival in arriving:
            # (2) STAGE ONE - authorization. Registry only; no allocator.
            scope_decision = evaluate_scope(request, self.agent_repo, self.risk_item_repo)
            if scope_decision is not None:
                self.audit_sink.record_denied_on_scope(scope_decision, request, decision_at)
                continue

            # (3) STAGE TWO - the rate ceiling. Still no allocator.
            agent = self.agent_repo.get_agent(request.agent_id)
            assert agent is not None  # evaluate_scope already proved it exists and is ACTIVE
            scope = self.agent_repo.get_capability_scope(request.agent_id)
            assert scope is not None
            rate_reason = evaluate_agent_rate(request, scope, self.rate_window)
            if rate_reason is not None:
                self._deny_on_rate(request, arrival, agent, rate_reason, decision_at)
                continue

            survivors.append((request, arrival))

        # (4) The unmodified Phase 4 decision path.
        #
        # `request_by_id` is built BEFORE mediating, from this window's
        # survivors plus the candidates carried IN from earlier windows —
        # exactly as `sim/arm_b.py::_run_window_loop` does. Building it from
        # `result.rescheduled_candidates` instead would lose the request
        # behind any deferred candidate that finally wins a later window.
        request_by_id = {
            **{r.request_id: r for r, _ in survivors},
            **{c.request.request_id: c.request for c in self._carried_forward},
        }
        result = self._mediate(tuple(survivors), decision_at, window)
        self._carried_forward = result.rescheduled_candidates
        for decision in result.decisions:
            if decision.outcome is DecisionOutcome.GRANTED:
                self._execute_grant(decision, request_by_id, result, window)

    def _deny_on_rate(
        self,
        request: GrantRequest,
        arrival: datetime,
        agent: Agent,
        reason_code: str,
        decision_at: datetime,
    ) -> None:
        """Emit the rate denial as a real `decision.denied`, then strike.

        The `AllocationOutcome` is constructed here rather than in
        `sampark.demo.enforcement`, deliberately: that module must stay free
        of any `sampark.allocator` import so the structural
        no-allocator-involvement test covers stage two as well as stage one.
        Building an outcome object is not invoking the allocator - no
        scoring, ranking, admission or issuance happens on this path, and
        `tests/demo/test_allocator_non_involvement.py` proves it by making
        `filter_and_allocate` raise.
        """
        record = self.risk_item_repo.get_risk_item(request.risk_id)
        assert record is not None  # evaluate_scope already proved it exists
        candidate = build_candidate(request, record.risk_item, record.customer_id, arrival)
        outcome = AllocationOutcome(
            candidate=candidate,
            outcome_kind=OutcomeKind.DENIED,
            reason_code=reason_code,
            next_eligible_at=None,
            grant=None,
            fact_unavailable_reason_codes=(),
            score=None,
            rescheduled_candidate=None,
        )
        self.audit_sink.record_decision(outcome, decision_at)

        strike = record_stage_two_strike(self.agent_repo, agent, reason_code)
        self.audit_sink.record_agent_struck(strike.agent, reason_code, decision_at, request)
        if strike.newly_revoked:
            self.audit_sink.record_agent_revoked(strike.agent, decision_at, reason_code)

    def _mediate(
        self, survivors: tuple[tuple[GrantRequest, datetime], ...], decision_at: datetime, window: date
    ):
        """Call `mediate_window`, handling a mid-window model kill.

        On `ModelUnavailableError` the runner emits `model.degraded` ONCE,
        swaps in the frozen Phase 4 heuristic, and re-runs the same window.
        Re-running is safe and is not a second decision: every audit event
        the aborted attempt could have written is keyed by a deterministic
        `event_id`, and `issue_grant` is idempotent on `request_id`, so the
        retry either re-derives the same facts or finds them already there.
        """
        try:
            return mediate_window(
                survivors,
                self._carried_forward,
                self.agent_repo,
                self.risk_item_repo,
                self.mediation_ledger,
                self.issuer,
                decision_at,
                conn=self.conn,
                run_seed_risk_ids=self.scenario.run_seed_risk_ids,
                audit_sink=self.audit_sink,
                scorer=self.scorer,
            )
        except ModelUnavailableError as exc:
            from sampark.allocator.scorer import default_scorer

            before = self.scorer.inner_name
            fallback = default_scorer()
            self.audit_sink.record_model_degraded(
                reason_code=exc.reason_code,
                scorer_before=before,
                scorer_after=type(fallback).__name__,
                window_id=window,
                at=decision_at,
            )
            self._degraded = True
            self.scorer = KillableScorer(inner=fallback)
            self.chaos.note(
                ChaosControlId.KILL_MODEL,
                "scorer raised ModelUnavailableError; fell back to " + type(fallback).__name__,
            )
            return mediate_window(
                survivors,
                self._carried_forward,
                self.agent_repo,
                self.risk_item_repo,
                self.mediation_ledger,
                self.issuer,
                decision_at,
                conn=self.conn,
                run_seed_risk_ids=self.scenario.run_seed_risk_ids,
                audit_sink=self.audit_sink,
                scorer=self.scorer,
            )

    def _execute_grant(self, decision: GrantDecision, request_by_id, result, window: date) -> None:
        """spec §6.2's execution half, including the compensation path."""
        grant = decision.grant
        assert grant is not None
        request = request_by_id[decision.request_id]
        effective_bps = result.effective_incentive_bps_by_request_id[decision.request_id]
        action = ContactAction(
            agent_id=request.agent_id,
            risk_id=request.risk_id,
            customer_id=request.customer_id,
            channel=grant.channel,
            intent=request.intent,
            incentive_bps=effective_bps,
            scheduled_at=grant.send_after,
        )

        execute_grant(self.conn, grant.grant_id, grant.send_after)
        self.audit_sink.record_grant_executing(grant, request, grant.send_after)

        # Scripted provider failure: arm on the first grant of the designated
        # window, so a hands-off replay shows a real rollback.
        if (
            self.status.window_index == SCRIPTED_PROVIDER_FAILURE_WINDOW
            and not self.provider.is_armed()
            and self._rollback_count == 0
            and self.chaos.pending_provider_mode is None
        ):
            self.provider.arm(ProviderFailureMode.HARD_DOWN, grant_id=grant.grant_id)
        if self.chaos.pending_provider_mode is not None:
            self.provider.arm(self.chaos.pending_provider_mode, grant_id=grant.grant_id)
            self.chaos.pending_provider_mode = None

        send_result = None
        for _attempt in range(MAX_ATTEMPTS):
            try:
                send_result = self.provider.send(grant.grant_id, action)
                break
            except ProviderTimeout:
                self._retry_count += 1
                continue

        if send_result is None:
            # Provider exhausted. COMPENSATE: the existing, already-tested
            # rollback releases both margin pools and the contact slot.
            rollback_grant(self.conn, grant.grant_id, grant.send_after)
            self.audit_sink.record_grant_rolled_back(grant, request, grant.send_after)
            self._rollback_count += 1
            self.provider.disarm()
            self.chaos.note(
                ChaosControlId.FORCE_PROVIDER_TIMEOUT,
                "grant " + str(grant.grant_id)[:8] + " rolled back after "
                + str(MAX_ATTEMPTS) + " provider timeouts; margin and contact slot released",
            )
            return

        if send_result.attempts > 1:
            self.chaos.note(
                ChaosControlId.FORCE_PROVIDER_TIMEOUT,
                "grant " + str(grant.grant_id)[:8] + " succeeded on attempt "
                + str(send_result.attempts)
                + (" (deduplicated - provider had already accepted; NO double-send)"
                   if send_result.deduplicated else " (retry sent for the first time)"),
            )
        self.provider.disarm()

        # The demo does not model recovery outcomes (that is the simulator's
        # job in sim/arm_b.py, and Phase 8 is a decision-trace demo, not an
        # evidence run). Settling at the reserved ceiling keeps the margin
        # arithmetic exact and honest.
        confirm_grant(self.conn, grant.grant_id, grant.send_after, grant.incentive_ceiling_paise)
        self.audit_sink.record_grant_confirmed(
            grant, request, grant.send_after, grant.incentive_ceiling_paise
        )

    # ------------------------------------------------------------------
    # driving
    # ------------------------------------------------------------------

    def run(self, on_window: Callable[[int, date], None] | None = None) -> None:
        self.status.state = "running"
        try:
            for index, window in enumerate(self.scenario.windows):
                if self._stop_requested:
                    self.status.state = "stopped"
                    return
                self.status.window_index = index
                self.status.current_window = window

                if index == SCRIPTED_MODEL_KILL_WINDOW and not self.scorer.killed:
                    self.scorer.kill(MODEL_DEGRADED_KILLED_BY_OPERATOR)

                if self.chaos.pending_flood:
                    self._inject_flood(window)

                self.run_window(window)
                if on_window is not None:
                    on_window(index, window)
                if self.pace and index < len(self.scenario.windows) - 1:
                    time.sleep(self.scenario.clock.wall_delay_for_window())
            self.status.state = "finished"
        except Exception as exc:  # surfaced, never swallowed (spec §12.1)
            self.status.state = "failed"
            self.status.error = type(exc).__name__ + ": " + str(exc)
            raise

    def _inject_flood(self, window: date) -> None:
        self.chaos.pending_flood = False
        burst = [s for s in self.scenario.rogue_requests if s.label.startswith("stage2_burst_")]
        base = self.scenario.clock.decision_at(window)
        moved = [
            RogueRequestSpec(
                label=s.label + "_flood",
                stage=2,
                risk_id=s.risk_id,
                customer_id=s.customer_id,
                channel=s.channel,
                intent=s.intent,
                incentive_bps=s.incentive_bps,
                issued_at=base + timedelta(seconds=5 * i),
                proposed_send_after=base + timedelta(hours=1),
                expectation=s.expectation,
            )
            for i, s in enumerate(burst)
        ]
        self._extra_requests.setdefault(window, []).extend(moved)

    # ------------------------------------------------------------------
    # chaos application
    # ------------------------------------------------------------------

    def fire_chaos(self, control_id: ChaosControlId, target: str | None = None) -> str:
        """Apply one chaos control. Returns a plain-English effect string.

        Raises `ChaosInapplicableError` (HTTP 409) when the control has
        nothing to act on. It never fakes an effect, and it never writes an
        audit event for the button press itself - only the downstream
        mechanism writes to the chain.
        """
        conn, agent_repo, audit_sink = self._chaos_ctx()

        if control_id is ChaosControlId.KILL_MODEL:
            if self.scorer.killed:
                raise ChaosInapplicableError("the scorer seam is already killed for this run")
            self.scorer.kill(MODEL_DEGRADED_KILLED_BY_OPERATOR)
            effect = "scorer seam killed; the next scoring call will raise and the runner will fall back"

        elif control_id is ChaosControlId.REVOKE_AGENT_KEY:
            agent_id = target or ROGUE_AGENT_ID
            agent = agent_repo.get_agent(agent_id)
            if agent is None:
                raise ChaosInapplicableError("unknown agent_id: " + repr(agent_id))
            if agent.state is not AgentState.ACTIVE:
                raise ChaosInapplicableError(agent_id + " is already " + agent.state.value)
            revoked = revoke(agent)
            agent_repo.save_agent(revoked)
            at = self.scenario.clock.decision_at(self._next_window())
            audit_sink.record_agent_revoked(revoked, at, None)
            effect = agent_id + " REVOKED; its next request cannot pass evaluate_scope"

        elif control_id is ChaosControlId.SET_CLOCK_QUIET_HOURS:
            window = self._next_window()
            self._quiet_hours_override[window] = True
            effect = "the next request in window " + window.isoformat() + " will be re-timed to 21:40 IST"

        elif control_id is ChaosControlId.FORCE_PROVIDER_TIMEOUT:
            mode = ProviderFailureMode(target) if target else ProviderFailureMode.HARD_DOWN
            self.chaos.pending_provider_mode = mode
            effect = "provider armed (" + mode.value + ") for the next grant execution"

        elif control_id is ChaosControlId.FLOOD_ROGUE_AGENT:
            agent = agent_repo.get_agent(ROGUE_AGENT_ID)
            if agent is None or agent.state is not AgentState.ACTIVE:
                raise ChaosInapplicableError(
                    "the rogue agent is already revoked - it can no longer produce a verifiable request"
                )
            self.chaos.pending_flood = True
            effect = "six correctly-scoped rogue requests queued into the next window, inside one simulated minute"

        elif control_id is ChaosControlId.MARK_CUSTOMER_OPTED_OUT:
            customer_id = target or self.scenario.customer_ids[0]
            if customer_id not in self.scenario.customer_ids:
                raise ChaosInapplicableError("customer " + repr(customer_id) + " is not in this demo")
            at = self.scenario.clock.decision_at(self._next_window())
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE contact_states SET optouts_by_channel = %s WHERE customer_id = %s",
                    ('{"whatsapp": "' + at.isoformat() + '"}', customer_id),
                )
                if cur.rowcount == 0:
                    raise ChaosInapplicableError("no contact_states row for " + repr(customer_id))
            effect = customer_id + " opted out of whatsapp; every future whatsapp grant is permanently denied"

        elif control_id is ChaosControlId.TRIGGER_INTERLOCK_ON_CART:
            customer_id = target or self._customer_with_open_cart()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE risk_items SET root_cause = 'disputed' WHERE risk_id = ("
                    "  SELECT risk_id FROM risk_items WHERE customer_id = %s "
                    "  AND source = 'abandoned_checkout' ORDER BY risk_id LIMIT 1)",
                    (customer_id,),
                )
                if cur.rowcount == 0:
                    raise ChaosInapplicableError(
                        "customer " + repr(customer_id) + " has no abandoned_checkout risk item to flag"
                    )
            effect = (
                customer_id + " has a disputed item; the dispute_open interlock now DENIES every "
                "discount-bearing grant for them (substituted for the RTO row - see control note)"
            )
        else:  # pragma: no cover - ChaosControlId is exhaustive
            raise ChaosInapplicableError("unknown control: " + str(control_id))

        self.chaos.note(control_id, effect)
        return effect

    def _chaos_ctx(self):
        """(connection, agent repo, audit sink) for chaos DB work.

        Uses `chaos_conn` when the caller supplied one (the API, which drives
        the runner on its own thread), otherwise the main connection (tests
        and the CLI, which are single-threaded). Built per call rather than
        cached: these are thin wrappers over a connection, and constructing
        them here keeps the "which connection am I on" question answered in
        exactly one place."""
        if self.chaos_conn is None:
            return self.conn, self.agent_repo, self.audit_sink
        return (
            self.chaos_conn,
            PostgresAgentRepository(self.chaos_conn),
            PostgresAuditSink(self.chaos_conn),
        )

    def _next_window(self) -> date:
        index = min(max(self.status.window_index, 0) + 1, len(self.scenario.windows) - 1)
        return self.scenario.windows[index]

    def _customer_with_open_cart(self) -> str:
        for risk_item in self.scenario.ledger.risk_items:
            if risk_item.source == "abandoned_checkout":
                return self.scenario.ledger.risk_customer_map[risk_item.risk_id]
        raise ChaosInapplicableError("this scenario has no abandoned_checkout risk item")

    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        """Ask the runner to stop at the next window boundary.

        Cooperative rather than forced: a window is a transaction boundary,
        and killing one mid-flight is how reservations leak. The session
        waits briefly for this before dropping the schema."""
        self._stop_requested = True

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    @property
    def rollback_count(self) -> int:
        return self._rollback_count

    @property
    def retry_count(self) -> int:
        return self._retry_count

    @property
    def degraded(self) -> bool:
        return self._degraded


__all__ = ["DemoRunner", "RunnerStatus", "SCRIPTED_MODEL_KILL_WINDOW", "SCRIPTED_PROVIDER_FAILURE_WINDOW"]
