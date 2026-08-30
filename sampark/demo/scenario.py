"""The deterministic demo scenario — spec §12.1 ("Same seed, same trace").

--- Why a subset, and not a new world ---

`sim/generator.py` and `sim/population.py` fix `N_SIGNALS = 20_000` and
`N_PEOPLE = 5_000` as MODULE CONSTANTS, not parameters. They are the
committed generator every piece of Phase 1/4/6/7 evidence was produced
from, and Phase 8 does not get to change them. A full Arm B run over that
world was measured at ~48 minutes against real Postgres — unusable for a
40-second demo.

So the demo builds the SAME world from the SAME committed generator at the
SAME headline seed (42), in memory, in about a second, and then selects a
small deterministic slice of it. There is no second generator, no
hand-authored fixture world, and no new data story to defend: every
customer, risk item, amount and root cause the reviewer sees came out of
`sim.cli.build_dataset(42)` exactly as it does for the frozen evidence.

--- The selection rule (deterministic, documented, no randomness) ---

1. Run the four UNCHANGED Phase 2 agents over the full world to get every
   `ContactAction` they would take.
2. Group by (customer, window). A pair is CONTENDED when two or more
   DIFFERENT agents want the same customer in the same window — that is the
   thesis in one data structure, so the demo is selected to contain as much
   of it as possible.
3. Choose the 5-consecutive-window span containing the most contended
   pairs; ties break to the earliest span.
4. Within that span rank customers by (contended windows desc, total amount
   at risk desc, customer_id asc) and take the first 8.
5. Keep every risk item owned by those customers, and every honest action
   that falls inside the span.

At seed 42 this yields 8 customers, 29 honest actions across all four
agents, and 5 windows (2025-09-10 .. 2025-09-14) — roughly 150 audit
events, which is a legible 40 seconds rather than an unwatchable month.

--- The rogue ---

Spec §12.3's rogue must be denied on SCOPE in stage one and on in-scope
policy in stage two. Two constraints shape its declared scope:

  * `sampark/policy/hard/dlt_template.py` DENIES any (intent, channel) pair
    outside its four registered ones. A rogue declaring an invented intent
    would be killed by the DLT rule at position 3 of the hard chain and
    would never reach quiet hours at position 10 — so stage two would
    demonstrate the wrong rule. The rogue therefore declares
    (`cart_recovery`, `whatsapp`), a REGISTERED pair. This is also the more
    on-thesis framing: a third-party agent doing perfectly legitimate,
    correctly-scoped cart recovery that still has to be mediated.
  * `max_requests_per_hour = 3`, so spec §12.3's "six perfectly legitimate,
    correctly-scoped grant requests in one minute" produces exactly
    `STRIKE_THRESHOLD` (3) denials and revokes the key on the sixth.

The scripted sequence, and what each request proves:

    R1  voice channel (never declared)     -> scope.channel_not_allowed
    R2  4000 bps vs its 200 ceiling        -> scope.incentive_ceiling_exceeded
        ... both denied by the Registry alone, allocator never invoked.
    R3  in scope, send_after 23:15 IST     -> scope PASSES, then
                                              policy.quiet_hours (DEFER)
    R4-R9  six in-scope requests inside one simulated minute
                                           -> R4,R5,R6 admitted and compete;
                                              R7,R8,R9 agent.rate_ceiling_exceeded
                                              -> 3 strikes -> REVOKED
    R10 anything, afterwards               -> scope.agent_revoked

R1 and R2 carry REAL risk items owned by real demo customers, so the denial
lands on the intended scope check rather than on `scope.unknown_risk_item`
(the evaluation order in `sampark/registry/scope.py` checks risk-item
existence and ownership BEFORE channel/intent/source/incentive).

Note that R1 and R2 never reach the rate gate at all — it runs only after
scope passes. That is not an implementation detail, it is the stage
separation: authorization is answered before anything stateful happens.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from agents.types import ContactAction
from sampark.allocator.constants import IST
from sampark.budget.windows import window_id_for
from sampark.contracts import Agent, AgentState, CapabilityScope, ContactState, Customer, RiskItem
from sampark.demo.clock import DemoClock
from sampark.registry.keys import AgentKeypair
from sim.arm_b import _AGENT_SCOPES, _AGENTS, _adjusted_arrival, _build_ledger_view, _deterministic_keypair
from sim.cli import build_dataset
from sim.ledger import Ledger

DEFAULT_SEED = 42
DEFAULT_CUSTOMER_COUNT = 8
DEFAULT_WINDOW_COUNT = 5

ROGUE_AGENT_ID = "third_party_recovery_agent"
ROGUE_PUBLISHER = "Third-Party Recovery Co"

# The rogue's DECLARED capability. Narrow and entirely legitimate — this is
# an agent that could genuinely be published to a marketplace. Everything it
# is denied for later, it is denied for despite this being correct.
ROGUE_SCOPE = CapabilityScope(
    allowed_channels=["whatsapp"],
    allowed_intents=["cart_recovery"],
    allowed_risk_sources=["abandoned_checkout"],
    max_incentive_bps=200,
    max_requests_per_hour=3,
)

HONEST_PUBLISHER = "SAMPARK Phase 2 baseline"


@dataclass(frozen=True)
class RogueRequestSpec:
    """One scripted rogue request. Everything here is a simulated instant or
    a literal; nothing is drawn or read from a clock."""

    label: str
    stage: int  # 1 = authorization (scope), 2 = in-scope policy/rate
    risk_id: str
    customer_id: str
    channel: str
    intent: str
    incentive_bps: int
    issued_at: datetime
    proposed_send_after: datetime
    expectation: str  # plain-English, shown in the UI's demo-control region


@dataclass(frozen=True)
class DemoScenario:
    seed: int
    customer_ids: tuple[str, ...]
    windows: tuple[date, ...]
    ledger: Ledger
    honest_actions: tuple[ContactAction, ...]
    rogue_requests: tuple[RogueRequestSpec, ...]
    clock: DemoClock
    run_seed_risk_ids: frozenset[str]

    @property
    def first_window(self) -> date:
        return self.windows[0]

    @property
    def last_window(self) -> date:
        return self.windows[-1]


def _select_span(
    action_windows: dict[tuple[str, date], list[ContactAction]], window_count: int
) -> tuple[date, ...]:
    windows = sorted({w for (_, w) in action_windows})
    contended = {
        key for key, acts in action_windows.items() if len({a.agent_id for a in acts}) >= 2
    }
    best_span: tuple[date, ...] | None = None
    best_score = -1
    for i in range(max(len(windows) - window_count + 1, 1)):
        span = tuple(windows[i : i + window_count])
        if len(span) < window_count:
            break
        span_set = set(span)
        score = sum(1 for (_c, w) in contended if w in span_set)
        if score > best_score:  # strict >: ties keep the EARLIEST span
            best_score = score
            best_span = span
    assert best_span is not None
    return best_span


def _select_customers(
    action_windows: dict[tuple[str, date], list[ContactAction]],
    span: tuple[date, ...],
    amount_by_risk_id: dict[str, int],
    customer_count: int,
) -> tuple[str, ...]:
    span_set = set(span)
    contended_windows: dict[str, int] = collections.defaultdict(int)
    total_amount: dict[str, int] = collections.defaultdict(int)
    for (customer_id, window), acts in action_windows.items():
        if window not in span_set:
            continue
        if len({a.agent_id for a in acts}) >= 2:
            contended_windows[customer_id] += 1
        total_amount[customer_id] += sum(amount_by_risk_id[a.risk_id] for a in acts)
    ranked = sorted(
        total_amount,
        key=lambda c: (-contended_windows[c], -total_amount[c], c),
    )
    return tuple(ranked[:customer_count])


def _subset_ledger(full: Ledger, customer_ids: frozenset[str]) -> Ledger:
    """Every risk item owned by the chosen customers, plus their Customer and
    ContactState rows. Sorted so the demo schema is loaded in a stable order.

    Risk items OUTSIDE the demo's window span are kept: they are real ledger
    state for these customers, they legitimately affect the fatigue term and
    the customer margin pool, and hiding them would make the demo's numbers
    differ from what the same customers would see in a full run."""
    customers = tuple(sorted((c for c in full.customers if c.customer_id in customer_ids), key=lambda c: c.customer_id))
    contact_states: dict[str, ContactState] = {
        cid: full.contact_states[cid] for cid in sorted(customer_ids)
    }
    risk_items = tuple(
        sorted(
            (r for r in full.risk_items if full.risk_customer_map[r.risk_id] in customer_ids),
            key=lambda r: r.risk_id,
        )
    )
    risk_customer_map = {r.risk_id: full.risk_customer_map[r.risk_id] for r in risk_items}
    return Ledger(
        customers=customers,
        contact_states=contact_states,
        risk_items=risk_items,
        risk_customer_map=risk_customer_map,
    )


def _build_rogue_requests(ledger: Ledger, span: tuple[date, ...]) -> tuple[RogueRequestSpec, ...]:
    """Ten scripted requests. Targets are the demo customers' own
    abandoned_checkout risk items, taken in sorted risk_id order so the
    script is identical on every run at this seed.

    Each target must be DISTINCT: `agents/mediated.py::request_id_for`
    derives `request_id = uuid5(NS_REQUEST, seed:agent_id:risk_id)`, so
    re-targeting one risk item would collide two requests onto one id.
    """
    targets = [
        r.risk_id
        for r in ledger.risk_items
        if r.source == "abandoned_checkout"
    ]
    targets.sort()
    if len(targets) < 10:
        raise ScenarioTooSmallError(
            "the rogue script needs 10 distinct abandoned_checkout risk items among the demo "
            "customers, found " + str(len(targets)) + ". Widen DEFAULT_CUSTOMER_COUNT."
        )
    owner = ledger.risk_customer_map

    w_first, w_burst, w_after = span[0], span[2], span[3]

    def at(window: date, hour: int, minute: int) -> datetime:
        return datetime(window.year, window.month, window.day, hour, minute, tzinfo=IST)

    specs: list[RogueRequestSpec] = []

    # --- STAGE 1: authorization. Registry answers; allocator never runs. ---
    specs.append(
        RogueRequestSpec(
            label="stage1_channel",
            stage=1,
            risk_id=targets[0],
            customer_id=owner[targets[0]],
            channel="voice",  # never declared
            intent="cart_recovery",
            incentive_bps=100,
            issued_at=at(w_first, 10, 0),
            proposed_send_after=at(w_first, 10, 0),
            expectation="DENIED on scope.channel_not_allowed - voice was never declared. Allocator never runs.",
        )
    )
    specs.append(
        RogueRequestSpec(
            label="stage1_incentive",
            stage=1,
            risk_id=targets[1],
            customer_id=owner[targets[1]],
            channel="whatsapp",
            intent="cart_recovery",
            incentive_bps=4000,  # 40% vs its declared 200 bps ceiling
            issued_at=at(w_first, 10, 1),
            proposed_send_after=at(w_first, 10, 1),
            expectation="DENIED on scope.incentive_ceiling_exceeded - 4000bps vs its declared 200. Allocator never runs.",
        )
    )

    # --- STAGE 2a: in scope, but inside the TCCCPR blackout. ---
    specs.append(
        RogueRequestSpec(
            label="stage2_quiet_hours",
            stage=2,
            risk_id=targets[2],
            customer_id=owner[targets[2]],
            channel="whatsapp",
            intent="cart_recovery",
            incentive_bps=200,
            issued_at=at(w_first, 23, 15),
            proposed_send_after=at(w_first, 23, 15),
            expectation="Scope PASSES. Deferred on policy.quiet_hours (TCCCPR 21:00-09:00 blackout). No strike.",
        )
    )

    # --- STAGE 2b: six correctly-scoped requests inside one minute. ---
    for i in range(6):
        risk_id = targets[3 + i]
        will_deny = i >= ROGUE_SCOPE.max_requests_per_hour
        specs.append(
            RogueRequestSpec(
                label="stage2_burst_" + str(i + 1),
                stage=2,
                risk_id=risk_id,
                customer_id=owner[risk_id],
                channel="whatsapp",
                intent="cart_recovery",
                incentive_bps=200,
                issued_at=at(w_burst, 9, 5) + timedelta(seconds=5 * i),
                proposed_send_after=at(w_burst, 10, 0),
                expectation=(
                    "DENIED on agent.rate_ceiling_exceeded (ceiling 3/hour) -> STRIKE"
                    if will_deny
                    else "In scope and under the rate ceiling - competes normally."
                ),
            )
        )

    # --- After revocation. ---
    specs.append(
        RogueRequestSpec(
            label="post_revocation",
            stage=2,
            risk_id=targets[9],
            customer_id=owner[targets[9]],
            channel="whatsapp",
            intent="cart_recovery",
            incentive_bps=200,
            issued_at=at(w_after, 9, 30),
            proposed_send_after=at(w_after, 10, 0),
            expectation="DENIED on scope.agent_revoked - the key can no longer produce a verifiable request.",
        )
    )
    return tuple(specs)


class ScenarioTooSmallError(RuntimeError):
    """The selected subset cannot support the scripted demo. Raised loudly
    rather than silently shortening the rogue script."""


def build_scenario(
    seed: int = DEFAULT_SEED,
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    window_count: int = DEFAULT_WINDOW_COUNT,
    wall_seconds_budget: float | None = None,
) -> DemoScenario:
    """Build the deterministic demo scenario. Pure: no I/O, no clock, no RNG
    beyond the committed generator's own seeded streams."""
    _population, _signals, full = build_dataset(seed)
    view = _build_ledger_view(full)

    actions: list[ContactAction] = []
    for agent in _AGENTS:
        actions.extend(agent.select_actions(view))

    amount_by_risk_id = {r.risk_id: r.amount_paise for r in full.risk_items}

    action_windows: dict[tuple[str, date], list[ContactAction]] = collections.defaultdict(list)
    for action in actions:
        action_windows[(action.customer_id, window_id_for(_adjusted_arrival(action)))].append(action)

    span = _select_span(action_windows, window_count)
    customer_ids = _select_customers(action_windows, span, amount_by_risk_id, customer_count)
    subset = _subset_ledger(full, frozenset(customer_ids))

    span_set = set(span)
    chosen = set(customer_ids)
    honest_actions = tuple(
        sorted(
            (
                a
                for a in actions
                if a.customer_id in chosen and window_id_for(_adjusted_arrival(a)) in span_set
            ),
            key=lambda a: (window_id_for(_adjusted_arrival(a)), a.agent_id, a.risk_id),
        )
    )
    if not honest_actions:
        raise ScenarioTooSmallError("no honest agent actions fall inside the selected window span")

    rogue_requests = _build_rogue_requests(subset, span)

    clock = DemoClock(
        first_window=span[0],
        last_window=span[-1],
        **({} if wall_seconds_budget is None else {"wall_seconds_budget": wall_seconds_budget}),
    )

    return DemoScenario(
        seed=seed,
        customer_ids=customer_ids,
        windows=span,
        ledger=subset,
        honest_actions=honest_actions,
        rogue_requests=rogue_requests,
        clock=clock,
        run_seed_risk_ids=frozenset(r.risk_id for r in subset.risk_items),
    )


# --- registry setup --------------------------------------------------------


def agent_registrations(seed: int) -> tuple[tuple[Agent, CapabilityScope, AgentKeypair], ...]:
    """The five agents this demo registers: the four unchanged Phase 2 agents
    with their existing `sim/arm_b.py` scopes, plus the rogue.

    Keypairs come from `sim/arm_b.py::_deterministic_keypair` — REUSED, not
    reimplemented, so the demo's signatures are reproducible at a given seed
    exactly as the Arm B evidence runner's are. That is what lets the replay
    be byte-identical down to the chain head hash rather than merely
    logically identical.
    """
    out: list[tuple[Agent, CapabilityScope, AgentKeypair]] = []
    for agent in _AGENTS:
        keypair = _deterministic_keypair(seed, agent.agent_id)
        out.append(
            (
                Agent(
                    agent_id=agent.agent_id,
                    public_key=keypair.public_key_b64,
                    publisher=HONEST_PUBLISHER,
                    state=AgentState.ACTIVE,
                    strike_count=0,
                ),
                _AGENT_SCOPES[agent.agent_id],
                keypair,
            )
        )
    rogue_keypair = _deterministic_keypair(seed, ROGUE_AGENT_ID)
    out.append(
        (
            Agent(
                agent_id=ROGUE_AGENT_ID,
                public_key=rogue_keypair.public_key_b64,
                publisher=ROGUE_PUBLISHER,
                state=AgentState.ACTIVE,
                strike_count=0,
            ),
            ROGUE_SCOPE,
            rogue_keypair,
        )
    )
    return tuple(out)


__all__ = [
    "DEFAULT_CUSTOMER_COUNT",
    "DEFAULT_SEED",
    "DEFAULT_WINDOW_COUNT",
    "HONEST_PUBLISHER",
    "ROGUE_AGENT_ID",
    "ROGUE_PUBLISHER",
    "ROGUE_SCOPE",
    "DemoScenario",
    "RogueRequestSpec",
    "ScenarioTooSmallError",
    "agent_registrations",
    "build_scenario",
]
