"""Arm B — mediated runner, Design Lock §13.

Same seed, same generator, same four Phase 2 agents (UNCHANGED
select_actions() output), same Environment, same MockChannelAdapter,
same sim.metrics.compute_metrics as Arm A (sim/arm_a.py). The ONLY
experimental change is mediation: every ContactAction becomes a signed
GrantRequest (agents/mediated.py), verified by the unmodified Phase 3
registry (sampark.registry.scope.evaluate_scope), then hard-filtered
and allocated (sampark.mediation.service.mediate_window) before ever
reaching Environment.observe.

Windowing (Design Lock §8): processed one IST calendar day at a time,
ascending, carrying deferred candidates forward. A candidate whose
original scheduled_at falls in the EARLY half of quiet hours ([00:00,
09:00) IST) is presented at its adjusted 09:00-same-day instant
directly, rather than being run through quiet_hours.evaluate() only to
be deferred back into the exact same window it arrived in — a
runner-level scheduling simplification, not a hard-policy change:
quiet_hours.evaluate() would produce the identical next_eligible_at
regardless, since [00:00, 09:00) always defers to the SAME calendar
day's 09:00 (sampark/budget/windows.py::next_quiet_hours_boundary). An
EVENING quiet-hour candidate ([21:00, 24:00) IST) is NOT pre-adjusted:
it legitimately competes in its as-requested window, is deferred there,
and is carried forward to the next window by this loop like any other
deferral.

The window range runs through
max(arrival date) + MAX_DEFERRAL_WINDOWS + 1 days — enough for every
candidate to reach a terminal outcome, since Design Lock §7 bounds any
one candidate to at most MAX_DEFERRAL_WINDOWS - 1 defers before its
next deferral attempt converts to a terminal DENY.

--- Phase 4C-2, Blocker 1: storage/issuance BACKEND selection ---

`run_arm_b(..., backend=...)` selects which GrantIssuer/MediationLedgerView
implementation the SAME mediation algorithm runs against:

    "memory"   — InMemoryGrantIssuer + InMemoryMediationLedger. No
                 concurrency guarantee (sampark/budget/store.py's own
                 docstring). Fast; DEFAULT, so every existing unit test
                 that calls run_arm_b(seed) is completely unaffected.
    "postgres" — sampark.budget.issuance.PostgresGrantIssuer +
                 sampark.budget.postgres_ledger.PostgresMediationLedger,
                 against the REAL, owner-authored SERIALIZABLE
                 transaction and schema. This is the ONLY backend the
                 official evidence CLI (sim/arm_b_cli.py) uses — see
                 that module for why it never falls back to "memory".

Both backends run the identical mediate_window/allocate_window call
with the identical candidates, in the identical window order — nothing
about the mediation algorithm, policy layer, allocator, Registry
verification, Environment, or metrics differs between them. Only how
GrantRequest/Grant/budget state is persisted and re-read differs.
"""

from __future__ import annotations

import collections
import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Mapping, Protocol

import psycopg

from agents import (
    CartRecoveryAgent,
    ContactOutcome,
    LedgerView,
    MandateRecoveryAgent,
    PaymentRetryAgent,
    ReceivablesAgent,
    RecoveryAgent,
)
from agents.channel import MockChannelAdapter
from agents.mediated import to_grant_request
from agents.types import ContactAction
from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import AGING_BONUS_PAISE, IST, MAX_DEFERRAL_WINDOWS, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.budget.issuance import PostgresGrantIssuer
from sampark.budget.postgres_ledger import PostgresMediationLedger, confirm_grant, execute_grant, seed_budget_window
from sampark.budget.store import MERCHANT_ID, InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.budget.windows import window_id_for, window_start_for
from sampark.contracts import Agent, AgentState, CapabilityScope, DecisionOutcome, GrantDecision, GrantRequest, RiskItem
from sampark.mediation import lifecycle
from sampark.mediation.service import mediate_window
from sampark.registry.keys import AgentKeypair
from sampark.registry.store import (
    InMemoryAgentRepository,
    InMemoryRiskItemRepository,
    PostgresAgentRepository,
    PostgresRiskItemRepository,
)
from sim.cli import build_dataset
from sim.environment import BETA_FATIGUE, BETA_INCENTIVE, Environment
from sim.ledger import Ledger
from sim.persistence import PostgresConfig, load_ledger

if TYPE_CHECKING:
    from sampark.allocator.scorer import Scorer
    from sampark.audit.sink import AuditSink

_AGENTS: tuple[RecoveryAgent, ...] = (
    PaymentRetryAgent(),
    CartRecoveryAgent(),
    MandateRecoveryAgent(),
    ReceivablesAgent(),
)

# Registered capability scopes — narrow, matching each agent's locked
# baseline configuration exactly (agents/payment_retry.py,
# cart_recovery.py, mandate_recovery.py, receivables.py). Scope
# violations are expected to be 0 in Arm B, by construction.
_AGENT_SCOPES: dict[str, CapabilityScope] = {
    "payment_retry_agent": CapabilityScope(
        allowed_channels=["sms"],
        allowed_intents=["payment_retry"],
        allowed_risk_sources=["failed_payment"],
        max_incentive_bps=0,
        max_requests_per_hour=10_000,
    ),
    "cart_recovery_agent": CapabilityScope(
        allowed_channels=["whatsapp"],
        allowed_intents=["cart_recovery"],
        allowed_risk_sources=["abandoned_checkout"],
        max_incentive_bps=500,
        max_requests_per_hour=10_000,
    ),
    "mandate_recovery_agent": CapabilityScope(
        allowed_channels=["whatsapp"],
        allowed_intents=["mandate_retry"],
        allowed_risk_sources=["mandate_failure"],
        max_incentive_bps=200,
        max_requests_per_hour=10_000,
    ),
    "receivables_agent": CapabilityScope(
        allowed_channels=["voice"],
        allowed_intents=["receivables_followup"],
        allowed_risk_sources=["overdue_invoice"],
        max_incentive_bps=0,
        max_requests_per_hour=10_000,
    ),
}

_ADAPTERS: dict[str, MockChannelAdapter] = {
    "sms": MockChannelAdapter("sms"),
    "whatsapp": MockChannelAdapter("whatsapp"),
    "voice": MockChannelAdapter("voice"),
}

_QUIET_EARLY_HOUR = 9  # [00:00, 09:00) IST — pre-adjusted, see module docstring

BACKEND_MEMORY = "memory"
BACKEND_POSTGRES = "postgres"
_VALID_BACKENDS = (BACKEND_MEMORY, BACKEND_POSTGRES)


@dataclass(frozen=True)
class ArmBResult:
    outcomes: tuple[ContactOutcome, ...]
    decisions: tuple[GrantDecision, ...]
    backend: str = BACKEND_MEMORY


def _build_ledger_view(ledger: Ledger) -> LedgerView:
    """Mirrors sim/arm_a.py::_build_ledger_view exactly. Duplicated
    rather than imported: sim/arm_a.py is frozen (CLAUDE.md §5's phase
    boundary; Design Lock §17.3) and does not export this as a public
    name."""
    risk_items_by_source: dict[str, list[RiskItem]] = {}
    for item in ledger.risk_items:
        risk_items_by_source.setdefault(item.source, []).append(item)
    return LedgerView(
        customers_by_id={c.customer_id: c for c in ledger.customers},
        risk_items_by_source={source: tuple(items) for source, items in risk_items_by_source.items()},
        customer_id_by_risk_id=dict(ledger.risk_customer_map),
    )


def _deterministic_keypair(seed: int, agent_id: str) -> AgentKeypair:
    """A REPRODUCIBLE Ed25519 keypair for the Postgres backend only —
    `sampark.registry.keys.generate_keypair()` (used by the "memory"
    backend, unchanged) draws from OS randomness, which is fine for a
    single in-process run but means re-running the SAME seed against
    Postgres would try to re-register the same agent_id under a
    DIFFERENT public key on every run, and
    sampark.registry.store.PostgresAgentRepository.register() correctly
    rejects that as AgentRegistrationConflictError. Deriving the
    signing key from (seed, agent_id) via SHA-256 makes registration
    for a given seed idempotent across repeated runs — no change to
    sampark/registry/keys.py itself, just a different, reproducible
    32-byte seed fed into the SAME nacl.signing.SigningKey the unchanged
    AgentKeypair dataclass already wraps."""
    from nacl.signing import SigningKey

    seed_bytes = hashlib.sha256(f"sampark-arm-b:{seed}:{agent_id}".encode("utf-8")).digest()
    return AgentKeypair(signing_key=SigningKey(seed_bytes))


def _build_agent_registry_memory(
    seed: int,
    audit_sink: "AuditSink | None" = None,
    registered_at: datetime | None = None,
) -> tuple[InMemoryAgentRepository, dict[str, AgentKeypair]]:
    """`audit_sink`/`registered_at` (Phase 5, U-8) — `None` by default,
    unchanged behavior. When both are given, each agent's registration
    is followed by one additive `audit_sink.record_agent_registered(...)`
    call — the registration itself (`repo.register(...)`) is unchanged
    either way. See `_run_arm_b_memory` for how `registered_at` is
    derived (a window-boundary instant, never a wall clock)."""
    from sampark.registry.keys import generate_keypair

    repo = InMemoryAgentRepository()
    keypairs: dict[str, AgentKeypair] = {}
    for agent in _AGENTS:
        keypair = generate_keypair()
        keypairs[agent.agent_id] = keypair
        registry_agent = Agent(
            agent_id=agent.agent_id, public_key=keypair.public_key_b64,
            publisher="SAMPARK Phase 2 baseline", state=AgentState.ACTIVE, strike_count=0,
        )
        repo.register(registry_agent, _AGENT_SCOPES[agent.agent_id])
        if audit_sink is not None and registered_at is not None:
            audit_sink.record_agent_registered(registry_agent, at=registered_at)
    return repo, keypairs


def _build_agent_registry_postgres(
    conn: psycopg.Connection,
    seed: int,
    audit_sink: "AuditSink | None" = None,
    registered_at: datetime | None = None,
) -> dict[str, AgentKeypair]:
    """`audit_sink`/`registered_at` — see `_build_agent_registry_memory`."""
    repo = PostgresAgentRepository(conn)
    keypairs: dict[str, AgentKeypair] = {}
    for agent in _AGENTS:
        keypair = _deterministic_keypair(seed, agent.agent_id)
        keypairs[agent.agent_id] = keypair
        registry_agent = Agent(
            agent_id=agent.agent_id, public_key=keypair.public_key_b64,
            publisher="SAMPARK Arm B evidence runner", state=AgentState.ACTIVE, strike_count=0,
        )
        repo.register(registry_agent, _AGENT_SCOPES[agent.agent_id])  # idempotent no-op on repeat
        if audit_sink is not None and registered_at is not None:
            audit_sink.record_agent_registered(registry_agent, at=registered_at)
    return keypairs


def _build_risk_item_repo(ledger: Ledger) -> InMemoryRiskItemRepository:
    repo = InMemoryRiskItemRepository()
    for item in ledger.risk_items:
        repo.add(item, customer_id=ledger.risk_customer_map[item.risk_id])
    return repo


def _risk_items_by_customer(ledger: Ledger) -> dict[str, tuple[RiskItem, ...]]:
    by_customer: dict[str, list[RiskItem]] = {}
    for item in ledger.risk_items:
        customer_id = ledger.risk_customer_map[item.risk_id]
        by_customer.setdefault(customer_id, []).append(item)
    return {cid: tuple(items) for cid, items in by_customer.items()}


def _adjusted_arrival(action: ContactAction) -> datetime:
    local = action.scheduled_at.astimezone(IST)
    if local.hour < _QUIET_EARLY_HOUR:
        return window_start_for(window_id_for(action.scheduled_at))
    return action.scheduled_at


class _LifecycleAdapter(Protocol):
    def execute(self, grant_id: uuid.UUID, at: datetime) -> None: ...
    def confirm(self, grant_id: uuid.UUID, at: datetime, actual_spend_paise: int) -> None: ...


@dataclass(frozen=True)
class _MemoryLifecycleAdapter:
    ledger: InMemoryMediationLedger

    def execute(self, grant_id: uuid.UUID, at: datetime) -> None:
        lifecycle.execute(self.ledger, grant_id, at=at)

    def confirm(self, grant_id: uuid.UUID, at: datetime, actual_spend_paise: int) -> None:
        lifecycle.confirm(self.ledger, grant_id, at=at, actual_spend_paise=actual_spend_paise)


@dataclass(frozen=True)
class _PostgresLifecycleAdapter:
    conn: psycopg.Connection

    def execute(self, grant_id: uuid.UUID, at: datetime) -> None:
        execute_grant(self.conn, grant_id, at)

    def confirm(self, grant_id: uuid.UUID, at: datetime, actual_spend_paise: int) -> None:
        confirm_grant(self.conn, grant_id, at, actual_spend_paise)


def _run_window_loop(
    seed: int,
    all_actions: list[ContactAction],
    keypairs: dict[str, AgentKeypair],
    agent_repo,
    risk_item_repo,
    mediation_ledger,
    issuer,
    environment: Environment,
    risk_items_by_id: dict[str, RiskItem],
    aging_bonus_paise: int,
    fifo_mode: bool,
    lifecycle_adapter: _LifecycleAdapter,
    conn_for_issuance: object,
    run_seed_risk_ids: frozenset[str],
    audit_sink: "AuditSink | None" = None,
    scorer: "Scorer | None" = None,
    outcome_observer=None,
    optout_writeback=None,
) -> tuple[tuple[ContactOutcome, ...], tuple[GrantDecision, ...]]:
    """The mediation algorithm itself — IDENTICAL for both backends. Every
    parameter here is already backend-specific data (a repository, a
    ledger, an issuer, a lifecycle adapter); this function makes no
    backend decision of its own.

    `run_seed_risk_ids` (Phase 4C hardening, W5) — this run's complete
    risk_id set, threaded to `mediate_window` -> ... -> `issuer.issue_grant`
    for the authoritative customer-margin-budget query's scoping.

    `audit_sink` (Phase 5, U-2) — `None` by default, unchanged behavior.
    Threaded straight through to `mediate_window`, which calls it for
    request/decision/grant-reservation events (the higher-fidelity
    integration point — see sampark/mediation/service.py's docstring).
    The two lifecycle events this function's OWN loop can see directly —
    grant.executing / grant.confirmed, both needing only `grant` +
    `request`, neither needing an AllocationOutcome — are recorded right
    alongside the EXISTING `lifecycle_adapter.execute`/`.confirm` calls
    below, in the same order, with no new control flow.

    `optout_writeback` (Phase 7, world v2 only) — an optional
    `Callable[[str, str, datetime], None]` called with
    `(customer_id, channel, at)` exactly once per CONFIRMED outcome whose
    `opt_out` is True. `None` by default (every pre-Phase-7 call site):
    the branch that reads it is skipped entirely, so this is a no-op for
    every existing caller. The one Phase 7 caller
    (`run_arm_b_holdout`, postgres backend only) passes
    `sim.optout_writeback.write_optout`, which persists the opt-out into
    `contact_states.optouts_by_channel` so `sampark/policy/hard/opt_out.py`
    denies this customer's channel in a LATER window — this function
    itself makes no policy decision, only records a fact the environment
    already produced."""
    if not all_actions:
        return (), ()

    actions_by_window: dict[date, list[ContactAction]] = {}
    for action in all_actions:
        actions_by_window.setdefault(window_id_for(_adjusted_arrival(action)), []).append(action)

    # W7 hardening: the SAME helper (_window_range) that seeds
    # budget_windows rows for the Postgres backend derives this range —
    # never a second, independently-maintained min()/max() computation
    # that could silently drift from it (see _window_range's docstring).
    first_window, last_window = _window_range(all_actions)

    carried_forward: tuple[Candidate, ...] = ()
    all_decisions: list[GrantDecision] = []
    outcomes: list[ContactOutcome] = []
    # Phase 7: cross-agent contact index per customer, tracked ONLY for
    # the contact.opt_out audit event's payload (informational — never
    # read by any decision). Mirrors Environment's own prior_contacts
    # bookkeeping; a SEPARATE counter, since Environment does not expose
    # its internal one.
    _contact_counts: dict[str, int] = collections.defaultdict(int)

    window = first_window
    while window <= last_window:
        window_actions = sorted(actions_by_window.get(window, ()), key=lambda a: (a.agent_id, a.risk_id))
        new_requests: list[tuple[GrantRequest, datetime]] = [
            (to_grant_request(action, seed, keypairs[action.agent_id]), _adjusted_arrival(action))
            for action in window_actions
        ]

        request_by_id: Mapping[uuid.UUID, GrantRequest] = {
            **{request.request_id: request for request, _ in new_requests},
            **{c.request.request_id: c.request for c in carried_forward},
        }

        decision_at = window_start_for(window)
        result = mediate_window(
            tuple(new_requests), carried_forward, agent_repo, risk_item_repo, mediation_ledger, issuer,
            decision_at, aging_bonus_paise, conn=conn_for_issuance, fifo_mode=fifo_mode,
            run_seed_risk_ids=run_seed_risk_ids, audit_sink=audit_sink,
            scorer=scorer, outcome_observer=outcome_observer,
        )
        all_decisions.extend(result.decisions)

        for decision in result.decisions:
            if decision.outcome is not DecisionOutcome.GRANTED:
                continue
            grant = decision.grant
            assert grant is not None
            request = request_by_id[decision.request_id]
            effective_bps = result.effective_incentive_bps_by_request_id[decision.request_id]

            reconstructed_action = ContactAction(
                agent_id=request.agent_id, risk_id=request.risk_id, customer_id=request.customer_id,
                channel=grant.channel, intent=request.intent, incentive_bps=effective_bps,
                scheduled_at=grant.send_after,
            )

            lifecycle_adapter.execute(grant.grant_id, at=grant.send_after)
            if audit_sink is not None:
                audit_sink.record_grant_executing(grant, request, grant.send_after)
            _ADAPTERS[grant.channel].send(reconstructed_action)
            outcome = environment.observe(reconstructed_action, risk_items_by_id[request.risk_id])
            lifecycle_adapter.confirm(grant.grant_id, at=grant.send_after, actual_spend_paise=outcome.incentive_paise)
            if audit_sink is not None:
                audit_sink.record_grant_confirmed(grant, request, grant.send_after, outcome.incentive_paise)
            contact_index = _contact_counts[request.customer_id]
            _contact_counts[request.customer_id] += 1
            if outcome.opt_out:
                assert outcome.opt_out_channel is not None
                if optout_writeback is not None:
                    optout_writeback(outcome.customer_id, outcome.opt_out_channel, grant.send_after)
                if audit_sink is not None:
                    audit_sink.record_contact_opt_out(grant, request, outcome.opt_out_channel, contact_index, grant.send_after)
            outcomes.append(outcome)

        carried_forward = result.rescheduled_candidates
        window += timedelta(days=1)

    return tuple(outcomes), tuple(all_decisions)


def _window_range(all_actions: list[ContactAction]) -> tuple[date, date]:
    """THE single source of the (first_window, last_window) computation
    (Phase 4C hardening, W7) — used both to pre-seed `budget_windows`
    rows for the Postgres backend (`_run_arm_b_postgres`, and its
    cleanup) AND, via `_run_window_loop`, to bound the day-by-day
    mediation loop itself. Before this hardening these were two
    independently-maintained `min()`/`max()` computations that happened
    to use the identical formula — a real (if latent) risk: if they had
    ever drifted, a window near the range's edge would silently revert
    to the FULL merchant budget (no pre-seeded row -> issuance's own
    `ON CONFLICT DO NOTHING` INSERT creates one from the frozen constant,
    Design Lock §11 step 3) for `merchant_margin_half`-style ablations,
    quietly weakening the ablation. `all_actions` must be non-empty —
    callers check that first (an empty action list has no window to
    range over)."""
    windows = {window_id_for(_adjusted_arrival(a)) for a in all_actions}
    first_window = min(windows)
    last_window = max(windows) + timedelta(days=MAX_DEFERRAL_WINDOWS + 1)
    return first_window, last_window


def _run_arm_b_memory(
    seed: int, ledger: Ledger, view: LedgerView, environment: Environment,
    all_actions: list[ContactAction], aging_bonus_paise: int, fifo_mode: bool,
    merchant_budget_paise_per_window: int = MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
    audit_sink: "AuditSink | None" = None,
    scorer: "Scorer | None" = None,
    outcome_observer=None,
) -> ArmBResult:
    registered_at = (
        window_start_for(_window_range(all_actions)[0]) if audit_sink is not None and all_actions else None
    )
    agent_repo, keypairs = _build_agent_registry_memory(seed, audit_sink=audit_sink, registered_at=registered_at)
    risk_item_repo = _build_risk_item_repo(ledger)
    mediation_ledger = InMemoryMediationLedger(
        _risk_items_by_customer(ledger), merchant_budget_paise_per_window=merchant_budget_paise_per_window
    )
    issuer = InMemoryGrantIssuer()
    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}
    run_seed_risk_ids = frozenset(item.risk_id for item in ledger.risk_items)

    outcomes, decisions = _run_window_loop(
        seed, all_actions, keypairs, agent_repo, risk_item_repo, mediation_ledger, issuer, environment,
        risk_items_by_id, aging_bonus_paise, fifo_mode,
        lifecycle_adapter=_MemoryLifecycleAdapter(mediation_ledger), conn_for_issuance=None,
        run_seed_risk_ids=run_seed_risk_ids, audit_sink=audit_sink,
        scorer=scorer, outcome_observer=outcome_observer,
    )
    return ArmBResult(outcomes=outcomes, decisions=decisions, backend=BACKEND_MEMORY)


def _cleanup_postgres_run(conn: psycopg.Connection, customer_ids: list[str], window_range: tuple[date, date]) -> None:
    """Resets Phase 4 TRANSACTIONAL state only (grant_requests, grants,
    contact_slot_claims, customer_margin_windows, this run's merchant
    budget_windows rows, AND contact_states' mutable cache columns) —
    never customers/risk_items/agents, and never contact_states'
    IDENTITY columns (customer_id) or its non-cache columns
    (optouts_by_channel, consent_scopes, fatigue_score) — those are
    reusable ledger/reference data.

    contact_states.contacts_24h/contacts_7d/last_contact_at ARE mutated
    by issuance (Design Lock §3.6's cache write), so — unlike
    customers/risk_items, which issuance never touches — this row must
    be reset too, or a later run (of the SAME seed, e.g. a Phase 1
    regression test, or a DIFFERENT ablation over the same customers)
    sees stale non-zero counts and either gets an incorrect rolling-cap
    pre-check or, for sim.persistence.load_ledger callers expecting the
    pristine all-zero values Phase 1 seeds, a spurious LedgerConflictError.

    Required for correctness, not just tidiness: budget_windows is keyed
    by (merchant_id, window_id), and window_id is a Gregorian calendar
    date shared across EVERY seed's simulated month (all start at the
    same MONTH_START) — without this cleanup, one seed's run would leave
    reserved/spent margin behind that silently starves the next seed's
    independent evaluation of the SAME merchant pool."""
    first_window, last_window = window_range
    with conn.cursor() as cur:
        cur.execute("DELETE FROM contact_slot_claims WHERE customer_id = ANY(%s)", (customer_ids,))
        cur.execute(
            "DELETE FROM grants WHERE request_id IN "
            "(SELECT request_id FROM grant_requests WHERE customer_id = ANY(%s))",
            (customer_ids,),
        )
        cur.execute("DELETE FROM grant_requests WHERE customer_id = ANY(%s)", (customer_ids,))
        cur.execute(
            "UPDATE contact_states SET contacts_24h = 0, contacts_7d = 0, last_contact_at = NULL "
            "WHERE customer_id = ANY(%s)",
            (customer_ids,),
        )
        cur.execute("DELETE FROM customer_margin_windows WHERE customer_id = ANY(%s)", (customer_ids,))
        cur.execute(
            "DELETE FROM budget_windows WHERE merchant_id = %s AND window_id BETWEEN %s AND %s",
            (MERCHANT_ID, first_window, last_window),
        )


def _run_arm_b_postgres(
    seed: int, ledger: Ledger, view: LedgerView, environment: Environment,
    all_actions: list[ContactAction], aging_bonus_paise: int, fifo_mode: bool,
    merchant_budget_paise_per_window: int,
    audit_sink: "AuditSink | None" = None,
    scorer: "Scorer | None" = None,
    outcome_observer=None,
) -> ArmBResult:
    config = PostgresConfig.from_env()  # raises PostgresConfigError if unset — never caught here
    conn = psycopg.connect(config.conninfo())  # raises psycopg.OperationalError if unreachable — never caught here
    conn.autocommit = True

    customer_ids = sorted(set(ledger.risk_customer_map.values()))
    window_range = _window_range(all_actions)

    try:
        load_ledger(conn, ledger)  # idempotent — Phase 1's own loader, unmodified

        registered_at = window_start_for(window_range[0]) if audit_sink is not None else None
        keypairs = _build_agent_registry_postgres(conn, seed, audit_sink=audit_sink, registered_at=registered_at)
        risk_item_repo = PostgresRiskItemRepository(conn)
        agent_repo = PostgresAgentRepository(conn)
        run_seed_risk_ids = frozenset(item.risk_id for item in ledger.risk_items)
        mediation_ledger = PostgresMediationLedger(
            conn, MERCHANT_ID, run_seed_risk_ids, merchant_budget_paise_per_window
        )
        issuer = PostgresGrantIssuer()
        risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}

        first_window, last_window = window_range
        w = first_window
        while w <= last_window:
            seed_budget_window(conn, MERCHANT_ID, w, merchant_budget_paise_per_window)
            w += timedelta(days=1)

        outcomes, decisions = _run_window_loop(
            seed, all_actions, keypairs, agent_repo, risk_item_repo, mediation_ledger, issuer, environment,
            risk_items_by_id, aging_bonus_paise, fifo_mode,
            lifecycle_adapter=_PostgresLifecycleAdapter(conn), conn_for_issuance=conn,
            run_seed_risk_ids=run_seed_risk_ids, audit_sink=audit_sink,
            scorer=scorer, outcome_observer=outcome_observer,
        )
        return ArmBResult(outcomes=outcomes, decisions=decisions, backend=BACKEND_POSTGRES)
    finally:
        _cleanup_postgres_run(conn, customer_ids, window_range)
        conn.close()


def run_arm_b(
    seed: int,
    aging_bonus_paise: int = AGING_BONUS_PAISE,
    backend: str = BACKEND_MEMORY,
    merchant_budget_paise_per_window: int = MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
    fifo_mode: bool = False,
    audit_sink: "AuditSink | None" = None,
    scorer: "Scorer | None" = None,
    outcome_observer=None,
    beta_fatigue: float = BETA_FATIGUE,
    beta_incentive: float = BETA_INCENTIVE,
) -> ArmBResult:
    """`backend` defaults to "memory" — EVERY existing call site and unit
    test that calls `run_arm_b(seed)` is unaffected by Blocker 1's
    Postgres support. The official evidence CLI (sim/arm_b_cli.py) is
    the one caller that explicitly passes backend="postgres" and never
    "memory" — see that module.

    `audit_sink` (Phase 5, U-2) — `None` by default; every existing call
    site (including sim/arm_b_cli.py's official evidence path) is
    unaffected. The caller owns constructing it (e.g.
    `sampark.audit.sink.PostgresAuditSink(conn)`, pointed at whatever
    connection/search_path it wants the resulting events durable in) —
    this function does not construct one itself, so it makes no decision
    about WHERE audit events go, only whether to call the sink it was
    given.

    `scorer` (Phase 6) — `None` preserves byte-identical Phase 4 behavior (a `sampark.allocator.scorer.HeuristicScorer` is constructed internally). Pass e.g. `sampark.models.scorer.ModelBackedScorer` to rank candidates with a trained model instead.

    `outcome_observer` (Phase 6) — read-only per-window instrumentation
    hook, threaded straight through to
    `sampark.mediation.service.mediate_window`; see that function's
    docstring. `None` by default, zero effect on any existing caller.

    `beta_fatigue` / `beta_incentive` (Phase 9, spec §11's sensitivity sweep) —
    default to the frozen `sim.environment` constants, so `run_arm_b(seed, ...)`
    without them is byte-identical (verified against the committed
    `results/arm_b_metrics_*.json`). They reach ONLY `Environment`'s
    ground-truth response model. Under world v1 no realized outcome feeds back
    into any admission, ranking, grant, deferral or denial — agent actions are
    all selected before the window loop starts, `carried_forward` is a function
    of the DECISION not the outcome, and nothing reads `outcome.recovered` — so
    varying either coefficient changes which contacts SUCCEED, never which
    contacts HAPPEN. `sim/sensitivity.py` depends on that property and
    `tests/sim_sensitivity/` asserts it directly."""
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"backend must be one of {_VALID_BACKENDS}, got {backend!r}")

    population, signals, ledger = build_dataset(seed)
    view = _build_ledger_view(ledger)
    environment = Environment.build(
        population, signals, ledger, seed,
        beta_fatigue=beta_fatigue, beta_incentive=beta_incentive,
    )

    all_actions: list[ContactAction] = []
    for agent in _AGENTS:
        all_actions.extend(agent.select_actions(view))

    if backend == BACKEND_MEMORY:
        return _run_arm_b_memory(
            seed, ledger, view, environment, all_actions, aging_bonus_paise, fifo_mode,
            merchant_budget_paise_per_window, audit_sink=audit_sink,
            scorer=scorer, outcome_observer=outcome_observer,
        )
    return _run_arm_b_postgres(
        seed, ledger, view, environment, all_actions, aging_bonus_paise, fifo_mode, merchant_budget_paise_per_window,
        audit_sink=audit_sink,
        scorer=scorer, outcome_observer=outcome_observer,
    )


# =============================================================================
# Phase 7 — Arm B-H (mediated, minus a randomized customer-level holdout,
# world v2). Additive: run_arm_b / _run_arm_b_memory / _run_arm_b_postgres /
# _run_window_loop's Phase-4/6 behavior is UNCHANGED (verified by the Phase
# 4 gate re-run and the placebo tests) — everything below is new code that
# no pre-Phase-7 caller reaches.
# =============================================================================


@dataclass(frozen=True)
class ArmBHoldoutResult:
    contact_outcomes: tuple[ContactOutcome, ...]
    natural_outcomes: "tuple"
    decisions: tuple[GrantDecision, ...]
    backend: str
    holdout_customer_ids: frozenset[str]
    holdout_customer_set_sha256: str
    seed: int
    fraction: float


def _cleanup_postgres_holdout_run(
    conn: psycopg.Connection, customer_ids: list[str], window_range: tuple[date, date]
) -> None:
    """Extends `_cleanup_postgres_run` (unmodified, called first) with the
    ONE additional piece of mutable state Arm B-H introduces:
    `contact_states.optouts_by_channel`, written by `sim.optout_writeback`
    during the run. Without this, a later run over overlapping customer_ids
    (e.g. a rerun of the same seed) would see stale opt-out state from a
    completed evidence run and get spurious `opt_out.py` denials — the same
    class of bug `_cleanup_postgres_run`'s own docstring already explains
    for `contacts_24h`/`contacts_7d`/`last_contact_at`. Never touches
    `consent_scopes` or `fatigue_score` — those remain reusable ledger data,
    matching `_cleanup_postgres_run`'s own documented scope exactly."""
    _cleanup_postgres_run(conn, customer_ids, window_range)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE contact_states SET optouts_by_channel = '{}'::jsonb WHERE customer_id = ANY(%s)",
            (customer_ids,),
        )


def _natural_outcomes_for_uncontacted(
    environment: Environment,
    ledger: Ledger,
    contacted_risk_ids: frozenset[str],
) -> tuple:
    """Phase 7 design lock, Decision 1 (Option 2): EVERY uncontacted risk
    item receives a natural-recovery draw, in every arm — not only
    held-out customers' items but also allocator-declined /
    deferred-to-terminal-deny items for non-held-out customers. Called
    strictly AFTER `_run_window_loop` has returned (i.e. after every
    admission/ranking/grant decision for the entire run is already final),
    so it cannot influence any of them.

    Distinguishing "randomized holdout" from "allocator-declined" among
    these uncontacted items is NOT this function's job — both receive an
    identical draw here. The distinction matters only for attribution
    baseline ESTIMATION (Phase 7 design lock, Decision 15's hard
    restriction), enforced in `sampark/attribution/baseline.py`, which
    reads ONLY the subset whose customer_id is in the holdout set."""
    from sim.natural import observation_window_end

    horizon = observation_window_end()
    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}
    uncontacted = sorted(set(risk_items_by_id) - contacted_risk_ids)
    return tuple(
        environment.observe_natural(
            risk_items_by_id[risk_id], ledger.risk_customer_map[risk_id], observed_at=horizon
        )
        for risk_id in uncontacted
    )


def _emit_holdout_assigned(
    audit_sink: "AuditSink | None", seed: int, fraction: float, held_out: frozenset[str],
    digest: str, occurred_at: datetime,
) -> None:
    if audit_sink is None:
        return
    audit_sink.record_holdout_assigned(
        seed=seed, fraction_bps=round(fraction * 10_000), assignment_version=1,
        holdout_customer_count=len(held_out), holdout_customer_set_sha256=digest, occurred_at=occurred_at,
    )


def _run_arm_b_holdout_memory(
    seed: int, fraction: float, ledger: Ledger, environment: Environment,
    all_actions: list[ContactAction], held_out: frozenset[str],
    aging_bonus_paise: int, fifo_mode: bool,
    merchant_budget_paise_per_window: int,
    scorer: "Scorer | None" = None,
    audit_sink: "AuditSink | None" = None,
) -> ArmBHoldoutResult:
    filtered_actions = [a for a in all_actions if a.customer_id not in held_out]

    registered_at = window_start_for(_window_range(all_actions)[0]) if audit_sink is not None and all_actions else None
    agent_repo, keypairs = _build_agent_registry_memory(seed, audit_sink=audit_sink, registered_at=registered_at)
    risk_item_repo = _build_risk_item_repo(ledger)
    mediation_ledger = InMemoryMediationLedger(
        _risk_items_by_customer(ledger), merchant_budget_paise_per_window=merchant_budget_paise_per_window
    )
    issuer = InMemoryGrantIssuer()
    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}
    run_seed_risk_ids = frozenset(item.risk_id for item in ledger.risk_items)

    from sim.holdout import membership_digest

    digest = membership_digest(held_out)
    if audit_sink is not None and all_actions:
        _emit_holdout_assigned(audit_sink, seed, fraction, held_out, digest, registered_at)

    # optout_writeback is intentionally NOT passed here: InMemoryMediationLedger
    # .optouts_by_channel is a hardcoded {} stub (sampark/budget/store.py,
    # frozen) with no mutable state to write into. Opt-out LABELS are still
    # drawn (ContactOutcome.opt_out), and contact.opt_out audit events are
    # still emitted if audit_sink is given — only cross-window ENFORCEMENT
    # is unavailable on this backend. See sim/optout_writeback.py's docstring.
    outcomes, decisions = _run_window_loop(
        seed, filtered_actions, keypairs, agent_repo, risk_item_repo, mediation_ledger, issuer, environment,
        risk_items_by_id, aging_bonus_paise, fifo_mode,
        lifecycle_adapter=_MemoryLifecycleAdapter(mediation_ledger), conn_for_issuance=None,
        run_seed_risk_ids=run_seed_risk_ids, scorer=scorer, audit_sink=audit_sink,
    )

    natural_outcomes = _natural_outcomes_for_uncontacted(
        environment, ledger, contacted_risk_ids=frozenset(o.risk_id for o in outcomes)
    )

    return ArmBHoldoutResult(
        contact_outcomes=outcomes, natural_outcomes=natural_outcomes, decisions=decisions,
        backend=BACKEND_MEMORY, holdout_customer_ids=held_out,
        holdout_customer_set_sha256=digest, seed=seed, fraction=fraction,
    )


def _run_arm_b_holdout_postgres(
    seed: int, fraction: float, ledger: Ledger, environment: Environment,
    all_actions: list[ContactAction], held_out: frozenset[str],
    aging_bonus_paise: int, fifo_mode: bool,
    merchant_budget_paise_per_window: int,
    scorer: "Scorer | None" = None,
    audit_sink: "AuditSink | None" = None,
) -> ArmBHoldoutResult:
    from sim.holdout import membership_digest
    from sim.optout_writeback import write_optout

    filtered_actions = [a for a in all_actions if a.customer_id not in held_out]

    config = PostgresConfig.from_env()
    conn = psycopg.connect(config.conninfo())
    conn.autocommit = True

    customer_ids = sorted(set(ledger.risk_customer_map.values()))
    window_range = _window_range(filtered_actions) if filtered_actions else _window_range(all_actions)

    try:
        load_ledger(conn, ledger)

        registered_at = window_start_for(window_range[0]) if audit_sink is not None else None
        keypairs = _build_agent_registry_postgres(conn, seed, audit_sink=audit_sink, registered_at=registered_at)
        risk_item_repo = PostgresRiskItemRepository(conn)
        agent_repo = PostgresAgentRepository(conn)
        run_seed_risk_ids = frozenset(item.risk_id for item in ledger.risk_items)
        mediation_ledger = PostgresMediationLedger(
            conn, MERCHANT_ID, run_seed_risk_ids, merchant_budget_paise_per_window
        )
        issuer = PostgresGrantIssuer()
        risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}

        first_window, last_window = window_range
        w = first_window
        while w <= last_window:
            seed_budget_window(conn, MERCHANT_ID, w, merchant_budget_paise_per_window)
            w += timedelta(days=1)

        digest = membership_digest(held_out)
        if audit_sink is not None:
            _emit_holdout_assigned(audit_sink, seed, fraction, held_out, digest, registered_at)

        def _optout_writeback(customer_id: str, channel: str, at: datetime) -> None:
            write_optout(conn, customer_id, channel, at)

        outcomes, decisions = _run_window_loop(
            seed, filtered_actions, keypairs, agent_repo, risk_item_repo, mediation_ledger, issuer, environment,
            risk_items_by_id, aging_bonus_paise, fifo_mode,
            lifecycle_adapter=_PostgresLifecycleAdapter(conn), conn_for_issuance=conn,
            run_seed_risk_ids=run_seed_risk_ids, scorer=scorer, audit_sink=audit_sink,
            optout_writeback=_optout_writeback,
        )

        natural_outcomes = _natural_outcomes_for_uncontacted(
            environment, ledger, contacted_risk_ids=frozenset(o.risk_id for o in outcomes)
        )

        return ArmBHoldoutResult(
            contact_outcomes=outcomes, natural_outcomes=natural_outcomes, decisions=decisions,
            backend=BACKEND_POSTGRES, holdout_customer_ids=held_out,
            holdout_customer_set_sha256=digest, seed=seed, fraction=fraction,
        )
    finally:
        _cleanup_postgres_holdout_run(conn, customer_ids, window_range)
        conn.close()


def run_arm_b_holdout(
    seed: int,
    fraction: float,
    backend: str = BACKEND_MEMORY,
    aging_bonus_paise: int = AGING_BONUS_PAISE,
    merchant_budget_paise_per_window: int = MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
    fifo_mode: bool = False,
    scorer: "Scorer | None" = None,
    audit_sink: "AuditSink | None" = None,
) -> ArmBHoldoutResult:
    """Arm B-H — Phase 7 (spec §8.9, §11). `fraction=0.0` holds out nobody
    (the world="v2"-with-empty-holdout diagnostic path, NOT the world="v1"
    placebo — see sim/arm_a_holdout.py's docstring for the same
    distinction). `merchant_budget_paise_per_window` is the FROZEN Phase 4
    constant by default and is deliberately NEVER scaled by `(1 - fraction)`
    here (Phase 7 design lock, Decision 3): scaling would introduce a
    second simultaneous difference from Phase 4, making any observed
    change uninterpretable. Run at two fractions (0.10, 0.20) to MEASURE
    the interference this creates instead.

    `audit_sink` (Phase 7) — `None` by default; every branch reading it
    is skipped, matching `run_arm_b`'s own established convention. When
    given, emits `holdout.assigned` ONCE per run (a digest event, never
    one per customer) and `contact.opt_out` for each CONFIRMED contact
    whose `outcome.opt_out` is True — both threaded through the SAME
    `_run_window_loop` Phase 5 already uses for `grant.reserved`/
    `grant.executing`/`grant.confirmed`. `recovery.credited` is NOT
    emitted here: computing a credit requires the full holdout-derived
    baseline estimate, only available AFTER this function returns (see
    `sampark.attribution`). It IS wired — into the operation that
    actually creates the credit, `sampark.attribution.store.insert_credit(conn,
    credit, request=..., audit_sink=...)`, called by whoever computes
    credits from this function's `natural_outcomes` (proven end-to-end
    against real PostgreSQL in
    tests/sampark_attribution/test_attribution_audit_integration.py)."""
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"backend must be one of {_VALID_BACKENDS}, got {backend!r}")

    from sim.holdout import assign, customer_amounts_from_risk_items

    population, signals, ledger = build_dataset(seed)
    view = _build_ledger_view(ledger)
    environment = Environment.build(population, signals, ledger, seed, world="v2")

    customer_amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    held_out = assign(seed, fraction, customer_amounts)

    all_actions: list[ContactAction] = []
    for agent in _AGENTS:
        all_actions.extend(agent.select_actions(view))

    if backend == BACKEND_MEMORY:
        return _run_arm_b_holdout_memory(
            seed, fraction, ledger, environment, all_actions, held_out,
            aging_bonus_paise, fifo_mode, merchant_budget_paise_per_window, scorer=scorer,
            audit_sink=audit_sink,
        )
    return _run_arm_b_holdout_postgres(
        seed, fraction, ledger, environment, all_actions, held_out,
        aging_bonus_paise, fifo_mode, merchant_budget_paise_per_window, scorer=scorer,
        audit_sink=audit_sink,
    )
