"""Exactly-once action/outcome invariant (Phase 2 accounting
clarification): recovery_unit is the RiskItem (sim/metrics.py), so a
risk item must be economically resolved at most once. This proves:

1. across all four agents combined, an eligible RiskItem never produces
   more than one ContactAction — structurally guaranteed by the agents'
   pairwise-disjoint source scopes, proven directly here rather than
   just observed incidentally on one fixture.
2. each ContactAction produces exactly one ContactOutcome (the
   run_arm_a replay loop is 1:1 by construction).
3. observing the same risk_id a second time is rejected outright by
   Environment.observe (DuplicateObservationError), rather than
   silently producing a second economic recovery.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from agents import CartRecoveryAgent, MandateRecoveryAgent, PaymentRetryAgent, ReceivablesAgent
from agents.types import ContactAction, LedgerView
from sampark.contracts import Customer, RiskItem
from sim.environment import DuplicateObservationError, Environment
from sim.population import HiddenResponseProfile

_AGENTS = (
    PaymentRetryAgent(),
    CartRecoveryAgent(),
    MandateRecoveryAgent(),
    ReceivablesAgent(),
)

_DETECTED_AT = dt.datetime(2025, 9, 1, tzinfo=dt.timezone.utc)


def _multi_source_view() -> LedgerView:
    items = (
        RiskItem(
            risk_id="fp-1",
            source="failed_payment",
            amount_paise=10_000,
            root_cause="insufficient_funds",
            detected_at=_DETECTED_AT,
        ),
        RiskItem(
            risk_id="ac-1",
            source="abandoned_checkout",
            amount_paise=15_000,
            root_cause="price_hesitation",
            detected_at=_DETECTED_AT,
        ),
        RiskItem(
            risk_id="mf-1",
            source="mandate_failure",
            amount_paise=30_000,
            root_cause="mandate_expired",
            detected_at=_DETECTED_AT,
        ),
        RiskItem(
            risk_id="oi-1",
            source="overdue_invoice",
            amount_paise=50_000,
            root_cause="disputed",
            detected_at=_DETECTED_AT,
        ),
    )
    risk_items_by_source: dict[str, list[RiskItem]] = {}
    for item in items:
        risk_items_by_source.setdefault(item.source, []).append(item)
    customer_id_by_risk_id = {item.risk_id: f"cust-{item.risk_id}" for item in items}
    return LedgerView(
        customers_by_id={
            cid: Customer(customer_id=cid) for cid in customer_id_by_risk_id.values()
        },
        risk_items_by_source={s: tuple(v) for s, v in risk_items_by_source.items()},
        customer_id_by_risk_id=customer_id_by_risk_id,
    )


# --- invariant 1: at most one ContactAction per eligible RiskItem ------


def test_agent_sources_are_pairwise_disjoint() -> None:
    """The structural reason a risk item can never be double-actioned:
    each risk item has exactly one source, and no two of the four
    agents share a source."""
    sources = [agent.source for agent in _AGENTS]
    assert len(sources) == len(set(sources))


def test_merged_actions_across_all_agents_never_repeat_a_risk_id() -> None:
    view = _multi_source_view()
    actions: list[ContactAction] = []
    for agent in _AGENTS:
        actions.extend(agent.select_actions(view))

    risk_ids = [a.risk_id for a in actions]
    assert len(risk_ids) == len(set(risk_ids))
    assert len(actions) == 4  # one per fixture item — none dropped, none duplicated


# --- invariant 2: exactly one ContactOutcome per ContactAction ---------


def _profile(person_id: str = "person-0") -> HiddenResponseProfile:
    return HiddenResponseProfile(
        person_id=person_id,
        conversion_propensity=0.4,
        fatigue_hazard=0.1,
        price_sensitivity=0.3,
    )


def _risk_item(risk_id: str = "r-1") -> RiskItem:
    return RiskItem(
        risk_id=risk_id,
        source="failed_payment",
        amount_paise=10_000,
        root_cause="insufficient_funds",
        detected_at=_DETECTED_AT,
    )


def _action(risk_id: str = "r-1", customer_id: str = "cust-1") -> ContactAction:
    return ContactAction(
        agent_id="payment_retry_agent",
        risk_id=risk_id,
        customer_id=customer_id,
        channel="sms",
        intent="payment_retry",
        incentive_bps=0,
        scheduled_at=_DETECTED_AT,
    )


def test_each_action_produces_exactly_one_outcome() -> None:
    profiles = {"cust-1": _profile("person-0"), "cust-2": _profile("person-1")}
    rng = np.random.default_rng(np.random.SeedSequence(1))
    env = Environment(profiles, rng)

    actions = [
        _action(risk_id="r-1", customer_id="cust-1"),
        _action(risk_id="r-2", customer_id="cust-2"),
    ]
    outcomes = [env.observe(a, _risk_item(a.risk_id)) for a in actions]

    assert len(outcomes) == len(actions)
    assert [o.risk_id for o in outcomes] == [a.risk_id for a in actions]


# --- invariant 3: replaying the same risk_id is rejected ----------------


def test_observing_the_same_risk_id_twice_is_rejected() -> None:
    profiles = {"cust-1": _profile()}
    rng = np.random.default_rng(np.random.SeedSequence(1))
    env = Environment(profiles, rng)

    action = _action(risk_id="r-1", customer_id="cust-1")
    env.observe(action, _risk_item("r-1"))  # first observation succeeds

    with pytest.raises(DuplicateObservationError):
        env.observe(action, _risk_item("r-1"))  # replay of the same risk_id is rejected


def test_rejected_replay_leaves_true_contact_count_unchanged() -> None:
    """The rejected call must not sneak in a second fatigue increment or
    a second RNG draw — it must be a true no-op besides raising."""
    profiles = {"cust-1": _profile()}
    rng = np.random.default_rng(np.random.SeedSequence(1))
    env = Environment(profiles, rng)

    action = _action(risk_id="r-1", customer_id="cust-1")
    env.observe(action, _risk_item("r-1"))
    contacts_after_first = env._true_contacts["cust-1"]

    with pytest.raises(DuplicateObservationError):
        env.observe(action, _risk_item("r-1"))

    assert env._true_contacts["cust-1"] == contacts_after_first
