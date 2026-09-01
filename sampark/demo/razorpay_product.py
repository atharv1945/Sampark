"""The Razorpay product flow — one real test-mode payment through SAMPARK.

This is the PRODUCT demo. It is deliberately small, because almost nothing
here is new: a normalised Razorpay opportunity IS a `RiskItem`, and every
decision below it is made by code this phase did not touch.

    sampark.integrations.normalize        Razorpay payment -> RiskItem
    sim.persistence.load_ledger           RiskItem -> the at-risk ledger
    agents.payment_retry                  the agent's own contact policy
    agents.mediated.to_grant_request      -> signed GrantRequest (Ed25519)
    sampark.registry.scope.evaluate_scope stage-one authorization
    sampark.mediation.service.mediate_window
                                          hard policy filter + allocator
    sampark.budget.issuance.issue_grant   SERIALIZABLE reservation
    sampark.budget.postgres_ledger        execute / confirm / rollback
    sampark.audit.*                       the hash-chained record

The ONE thing this module adds to the chain is `payment.risk_detected`,
which records where the money at risk came from. Everything else the screen
shows is an existing event type.

--- Isolation ---

Every write goes to a throwaway `sampark_demo_<...>` schema created by
`sampark.demo.isolation`, the same mechanism Phase 8 uses and for the same
reason: `public.audit_events` is the protected, append-only Phase 0-7 chain
and a product-demo event appended into it could never be removed.

--- Windows, and why the flow may run more than one ---

`agents/payment_retry.py` schedules a contact `detected_at + 2h`. A payment
that fails at 20:00 IST therefore proposes 22:00 IST, inside the TCCCPR
21:00-09:00 blackout, and `sampark.policy.hard.quiet_hours` DEFERS it to
09:00 the next morning. That is the system working, not a demo failure, and
it is time-of-day dependent because the input really is a live payment.

So the flow runs a short window loop carrying deferred candidates forward —
the same loop `sim/arm_b.py` runs, at the same `decision_at` convention
(`window_start_for(window)`) — until the candidate is granted, terminally
denied, or `MAX_WINDOWS` is reached. Deferral-then-grant is the deferral
contract being honoured, so following it is the mechanism, not a workaround.

--- What this module never does ---

It does not model recovery outcomes. Phase 8 made the same call for the same
reason: this is a decision-trace demo, not an evidence run, so a grant
settles at its reserved ceiling and Arm A/Arm B economics remain `sim/`'s
job. Nothing here produces, reads or alters a `results/*.json` file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import psycopg

from agents.mediated import to_grant_request
from agents.payment_retry import CHANNEL, INCENTIVE_BPS, INTENT, SCHEDULE_OFFSET
from agents.types import ContactAction
from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
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
from sampark.budget.windows import window_id_for
from sampark.contracts import Agent, AgentState, DecisionOutcome, GrantDecision, GrantRequest
from sampark.demo.provider import MAX_ATTEMPTS, MockProvider, ProviderFailureMode, ProviderTimeout
from sampark.demo.scorer_kill import KillableScorer, initial_degradation_reason
from sampark.integrations.normalize import RecoveryOpportunity
from sampark.mediation.service import mediate_window
from sampark.registry.scope import evaluate_scope
from sampark.registry.store import PostgresAgentRepository, PostgresRiskItemRepository
from sim.arm_b import _AGENT_SCOPES, _adjusted_arrival, _deterministic_keypair
from sim.ledger import Ledger
from sim.persistence import load_ledger

# The one agent this flow registers. Its capability scope is `sim/arm_b.py`'s
# UNCHANGED `payment_retry_agent` entry: sms channel, `payment_retry` intent,
# `failed_payment` risk source, 0 bps incentive ceiling. A Razorpay failed
# payment is exactly what it is scoped for — nothing was widened to admit it.
AGENT_ID = "payment_retry_agent"
PUBLISHER = "SAMPARK Razorpay product integration"

# Keypair derivation seed. Fixed so re-registering the agent in a fresh demo
# schema is idempotent (`_deterministic_keypair`'s own rationale), and shared
# with the synthetic demo so the SAME agent identity appears in both.
PRODUCT_SEED = 42

# How many consecutive windows a deferred candidate may be carried forward
# before the flow stops following it. Four covers the worst real case (a
# late-evening payment deferred over quiet hours) with room to spare; the
# allocator's own `MAX_DEFERRAL_WINDOWS = 7` remains the authority on when a
# deferral becomes `allocation.deferral_exhausted`.
MAX_WINDOWS = 4


class ProductFlowError(RuntimeError):
    """The flow could not proceed. Raised, never papered over."""


@dataclass(frozen=True)
class DeliveryReport:
    """The channel boundary's own account of one send. Mocked on purpose —
    synthetic consent cannot lawfully reach a real phone (CLAUDE.md §8) — so
    this reports the payload that WOULD have gone out, plus the retry and
    idempotency facts spec §6.2 asks for."""

    delivered: bool
    attempts: int
    deduplicated: bool
    channel: str
    rolled_back: bool


@dataclass(frozen=True)
class IngestOutcome:
    """CONTROL STATE for the product API — deliberately NOT the trace.

    Everything a viewer is asked to believe about the DECISION is read from
    the audit stream (spec §12.1). This object carries only what an operator
    needs to know about their own action: whether it was a duplicate, which
    windows were evaluated, and what the channel boundary did. The
    `outcome`/`reason_code` fields are the same values the corresponding
    audit event carries, returned so the API can answer synchronously; they
    are a copy of the record, never a substitute for it."""

    payment_id: str
    request_id: str
    risk_id: str
    customer_id: str
    duplicate: bool
    outcome: str
    reason_code: str | None
    windows_evaluated: tuple[str, ...]
    grant_id: str | None
    delivery: DeliveryReport | None


@dataclass
class RazorpayProductRun:
    """One product-demo session over one isolated schema.

    Not thread-safe: the API serialises access behind its session lock, the
    same way `ui.session.DemoSession` does for the synthetic demo."""

    conn: psycopg.Connection
    schema: str
    seed: int = PRODUCT_SEED

    audit_sink: PostgresAuditSink = field(init=False)
    provider: MockProvider = field(init=False)
    scorer: KillableScorer = field(init=False)
    agent_repo: PostgresAgentRepository = field(init=False)
    risk_item_repo: PostgresRiskItemRepository = field(init=False)
    issuer: PostgresGrantIssuer = field(init=False)

    _risk_ids: set[str] = field(init=False, default_factory=set)
    _seeded_windows: set[date] = field(init=False, default_factory=set)
    _outcomes: dict[str, IngestOutcome] = field(init=False, default_factory=dict)
    _degraded: bool = field(init=False, default=False)
    _prepared: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        from sampark.models.scorer import build_scorer

        self.audit_sink = PostgresAuditSink(self.conn)
        self.provider = MockProvider()
        self.scorer = KillableScorer(inner=build_scorer())
        self.agent_repo = PostgresAgentRepository(self.conn)
        self.risk_item_repo = PostgresRiskItemRepository(self.conn)
        self.issuer = PostgresGrantIssuer()

    # ------------------------------------------------------------------

    def prepare(self, at: datetime) -> None:
        """Register the agent and record any degradation that is ALREADY
        true. `at` is the instant the session opened; it is a real instant
        because a live Razorpay payment is a real event, not a replay."""
        if self._prepared:
            return
        keypair = _deterministic_keypair(self.seed, AGENT_ID)
        agent = Agent(
            agent_id=AGENT_ID,
            public_key=keypair.public_key_b64,
            publisher=PUBLISHER,
            state=AgentState.ACTIVE,
            strike_count=0,
        )
        self.agent_repo.register(agent, _AGENT_SCOPES[AGENT_ID])
        self.audit_sink.record_agent_registered(agent, at=at)

        # The uplift model is unavailable on this repository's data (the
        # committed Phase 6/7 finding). Record it as a real degradation at
        # session start rather than starting silently degraded — the same
        # call Phase 8 makes, for the same reason. Nothing anywhere claims
        # the model was available.
        reason = initial_degradation_reason(self.scorer.inner)
        if reason is not None:
            self._degraded = True
            self.audit_sink.record_model_degraded(
                reason_code=reason,
                scorer_before="ModelBackedScorer",
                scorer_after=self.scorer.inner_name,
                window_id=window_id_for(at),
                at=at,
            )
        self._prepared = True

    @property
    def keypair(self):
        return _deterministic_keypair(self.seed, AGENT_ID)

    @property
    def degraded(self) -> bool:
        return self._degraded

    def outcome_for(self, payment_id: str) -> IngestOutcome | None:
        return self._outcomes.get(payment_id)

    def ingested_payment_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._outcomes))

    # ------------------------------------------------------------------

    def ingest(self, opportunity: RecoveryOpportunity) -> IngestOutcome:
        """Detect -> authorize -> policy -> allocate -> execute -> audit.

        IDEMPOTENT on `payment_id`. A Razorpay webhook retry, a second poll,
        or a judge pressing the button twice returns the FIRST outcome and
        performs no second decision, no second grant and no second send
        (task brief §18). The audit chain is idempotent independently, by
        deterministic `event_id`, so the two guarantees do not depend on each
        other."""
        if not self._prepared:
            raise ProductFlowError("prepare() must run before ingest()")

        existing = self._outcomes.get(opportunity.payment_id)
        if existing is not None:
            return IngestOutcome(**{**existing.__dict__, "duplicate": True})

        # Identity is reconciled against the ledger BEFORE anything is
        # recorded, so the audit event names the customer the system will
        # actually mediate for — not a provisional id a later lookup would
        # contradict.
        opportunity = self._load_into_ledger(opportunity)
        self.audit_sink.record_payment_risk_detected(opportunity)

        request, arrival = self._build_request(opportunity)
        outcome = self._mediate_forward(opportunity, request, arrival)
        self._outcomes[opportunity.payment_id] = outcome
        return outcome

    # --- ledger --------------------------------------------------------

    def _load_into_ledger(self, opportunity: RecoveryOpportunity) -> RecoveryOpportunity:
        """Resolve identity against the ledger, then write.

        Returns the opportunity actually written — which may carry a DIFFERENT
        customer_id than normalisation derived, if this person is already in
        the at-risk ledger. See `_existing_customer_id` for why that
        reconciliation is necessary.

        `sim.persistence.load_ledger` is REUSED rather than a second INSERT
        path written here: it already checks a colliding primary key against
        the existing row's values and raises rather than silently discarding
        (`LedgerConflictError`), which is exactly the behaviour a re-delivered
        webhook needs.

        When the customer already exists, ONLY the risk item is written. The
        existing `customers` / `contact_states` rows are left alone: a second
        payment's email may differ from the first's, and overwriting a known
        customer's contact record from whatever the newest payment happened to
        carry is not this adapter's decision to make. It is also exactly what
        would make `load_ledger` raise `LedgerConflictError` — correctly."""
        existing = self._existing_customer_id(opportunity)
        if existing is not None:
            opportunity = opportunity.with_customer_id(existing)
            ledger = Ledger(
                customers=(),
                contact_states={},
                risk_items=(opportunity.risk_item,),
                risk_customer_map={opportunity.risk_id: opportunity.customer_id},
            )
        else:
            ledger = Ledger(
                customers=(opportunity.customer,),
                contact_states={opportunity.customer_id: opportunity.contact_state},
                risk_items=(opportunity.risk_item,),
                risk_customer_map={opportunity.risk_id: opportunity.customer_id},
            )
        load_ledger(self.conn, ledger)
        self._risk_ids.add(opportunity.risk_id)
        return opportunity

    def _existing_customer_id(self, opportunity: RecoveryOpportunity) -> str | None:
        """The customer already in this ledger who shares a contact hash with
        this payment, if any — spec §8.2's deduplication, applied
        INCREMENTALLY.

        `sampark.identity.resolution.resolve_customer_ids` deduplicates across
        a BATCH of signals; a payment adapter receives one at a time, so its
        provisional id is derived from that payment's hashes alone. Two
        payments from the same person carrying different emails would
        therefore mint two customers, and the unified at-risk ledger — the one
        row per human that every contact budget and every fatigue term depends
        on — would silently split. That is the failure this method exists to
        prevent, and `tests/demo/test_razorpay_product_flow.py` is where it was
        caught.

        The rule is the same union-on-a-shared-key rule `resolve_customer_ids`
        applies, evaluated against what the ledger already knows rather than
        against a batch. `ORDER BY customer_id LIMIT 1` keeps it deterministic
        when more than one row matches.

        A payment with no contact hashes at all matches nothing and stays its
        own singleton — the same outcome `resolve_customer_ids` gives it."""
        hashes = [
            h for h in (opportunity.customer.phone_hash, opportunity.customer.email_hash) if h
        ]
        if not hashes:
            return None
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id FROM customers "
                "WHERE phone_hash = ANY(%s) OR email_hash = ANY(%s) "
                "ORDER BY customer_id LIMIT 1",
                (hashes, hashes),
            )
            row = cur.fetchone()
        return None if row is None else row[0]

    def _seed_window(self, window: date) -> None:
        if window in self._seeded_windows:
            return
        seed_budget_window(self.conn, MERCHANT_ID, window, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW)
        self._seeded_windows.add(window)

    def _ledger_view(self) -> PostgresMediationLedger:
        # Rebuilt per mediation so `run_seed_risk_ids` reflects every
        # opportunity ingested so far — a second Razorpay payment for the
        # same customer must see the first one's open amount in the fatigue
        # term, which is the whole point of a unified at-risk ledger.
        return PostgresMediationLedger(self.conn, MERCHANT_ID, frozenset(self._risk_ids))

    # --- the request ---------------------------------------------------

    def _build_request(self, opportunity: RecoveryOpportunity) -> tuple[GrantRequest, datetime]:
        """The agent's own contact policy, unmodified.

        Channel, intent, incentive and the two-hour schedule offset are
        IMPORTED from `agents/payment_retry.py` rather than restated, so the
        product flow cannot drift from the agent the evidence runs use."""
        action = ContactAction(
            agent_id=AGENT_ID,
            risk_id=opportunity.risk_id,
            customer_id=opportunity.customer_id,
            channel=CHANNEL,
            intent=INTENT,
            incentive_bps=INCENTIVE_BPS,
            scheduled_at=opportunity.detected_at + SCHEDULE_OFFSET,
        )
        request = to_grant_request(action, self.seed, self.keypair)
        return request, _adjusted_arrival(action)

    # --- the decision --------------------------------------------------

    def _mediate_forward(
        self, opportunity: RecoveryOpportunity, request: GrantRequest, arrival: datetime
    ) -> IngestOutcome:
        from sampark.budget.windows import window_start_for

        # STAGE ONE - authorization. Registry only; the allocator never runs
        # for a scope denial, and this is the identical call Phase 3 ships.
        self.audit_sink.record_request_received(request)
        scope_decision = evaluate_scope(request, self.agent_repo, self.risk_item_repo)
        if scope_decision is not None:
            first_window = window_id_for(arrival)
            self.audit_sink.record_denied_on_scope(
                scope_decision, request, window_start_for(first_window)
            )
            return self._outcome(
                opportunity, request, "DENIED", scope_decision.reason_code,
                (first_window,), None, None,
            )

        windows: list[date] = []
        carried: tuple[Candidate, ...] = ()
        new_requests: tuple[tuple[GrantRequest, datetime], ...] = ((request, arrival),)
        window = window_id_for(arrival)
        last: GrantDecision | None = None

        for _step in range(MAX_WINDOWS):
            windows.append(window)
            self._seed_window(window)
            decision_at = window_start_for(window)

            result = mediate_window(
                new_requests,
                carried,
                self.agent_repo,
                self.risk_item_repo,
                self._ledger_view(),
                self.issuer,
                decision_at,
                conn=self.conn,
                run_seed_risk_ids=frozenset(self._risk_ids),
                audit_sink=self.audit_sink,
                scorer=self.scorer,
            )
            new_requests = ()
            decision = _decision_for(result.decisions, request.request_id)
            if decision is not None:
                last = decision
            if decision is not None and decision.outcome is DecisionOutcome.GRANTED:
                delivery = self._execute(decision, request, result)
                return self._outcome(
                    opportunity, request,
                    "ROLLED_BACK" if delivery.rolled_back else "GRANTED",
                    None, tuple(windows),
                    str(decision.grant.grant_id) if decision.grant else None,
                    delivery,
                )
            if decision is not None and decision.outcome is DecisionOutcome.DENIED:
                return self._outcome(
                    opportunity, request, "DENIED", decision.reason_code, tuple(windows), None, None
                )

            carried = result.rescheduled_candidates
            if not carried:
                break
            window = carried[0].window_id
            if window in windows:  # defensive: never spin on one window
                window = windows[-1] + timedelta(days=1)

        return self._outcome(
            opportunity, request, "DEFERRED",
            last.reason_code if last is not None else None,
            tuple(windows), None, None,
        )

    # --- execution -----------------------------------------------------

    def _execute(self, decision: GrantDecision, request: GrantRequest, result) -> DeliveryReport:
        """spec §6.2's execution half, including the compensation path.

        Identical in shape to `sampark.demo.runner._execute_grant`, and it
        calls the identical already-tested lifecycle functions. The
        difference is only that there is one grant here, not a window's
        worth."""
        grant = decision.grant
        assert grant is not None
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

        send_result = None
        for _attempt in range(MAX_ATTEMPTS):
            try:
                send_result = self.provider.send(grant.grant_id, action)
                break
            except ProviderTimeout:
                continue

        if send_result is None:
            rollback_grant(self.conn, grant.grant_id, grant.send_after)
            self.audit_sink.record_grant_rolled_back(grant, request, grant.send_after)
            self.provider.disarm()
            return DeliveryReport(
                delivered=False,
                attempts=self.provider.attempts_for(grant.grant_id),
                deduplicated=False,
                channel=grant.channel,
                rolled_back=True,
            )

        self.provider.disarm()
        confirm_grant(self.conn, grant.grant_id, grant.send_after, grant.incentive_ceiling_paise)
        self.audit_sink.record_grant_confirmed(
            grant, request, grant.send_after, grant.incentive_ceiling_paise
        )
        return DeliveryReport(
            delivered=True,
            attempts=send_result.attempts,
            deduplicated=send_result.deduplicated,
            channel=grant.channel,
            rolled_back=False,
        )

    def arm_provider_failure(self, mode: ProviderFailureMode) -> None:
        """Operator control: make the NEXT send fail. Reuses the Phase 8
        provider unchanged, so the rollback the product demo shows is the
        same rollback the system demo shows."""
        self.provider.arm(mode)

    # --- assembly ------------------------------------------------------

    def _outcome(
        self,
        opportunity: RecoveryOpportunity,
        request: GrantRequest,
        outcome: str,
        reason_code: str | None,
        windows: tuple[date, ...],
        grant_id: str | None,
        delivery: DeliveryReport | None,
    ) -> IngestOutcome:
        return IngestOutcome(
            payment_id=opportunity.payment_id,
            request_id=str(request.request_id),
            risk_id=opportunity.risk_id,
            customer_id=opportunity.customer_id,
            duplicate=False,
            outcome=outcome,
            reason_code=reason_code,
            windows_evaluated=tuple(w.isoformat() for w in windows),
            grant_id=grant_id,
            delivery=delivery,
        )


def _decision_for(decisions, request_id: uuid.UUID) -> GrantDecision | None:
    for decision in decisions:
        if decision.request_id == request_id:
            return decision
    return None


__all__ = [
    "AGENT_ID",
    "MAX_WINDOWS",
    "PRODUCT_SEED",
    "PUBLISHER",
    "DeliveryReport",
    "IngestOutcome",
    "ProductFlowError",
    "RazorpayProductRun",
]
