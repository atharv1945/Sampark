"""sim/environment.py — the ground-truth response model (Phase 2 locked
formula). Uses hand-built fixtures directly, at unit scale, rather than
the full generator — the full-dataset determinism check lives in
tests/arm_a/test_arm_a_reproducibility.py."""

from __future__ import annotations

import datetime as dt

from agents.types import ContactAction
from sampark.contracts import RiskItem
from sim.environment import Environment, p_recover
from sim.population import HiddenResponseProfile


class _FixedRandomSource:
    """Stand-in for np.random.Generator exposing only .random(), always
    returning a fixed value — makes recovered/not-recovered deterministic
    regardless of the computed probability, for tests that need one
    branch or the other with certainty rather than statistical
    convergence."""

    def __init__(self, value: float) -> None:
        self._value = value

    def random(self) -> float:
        return self._value


def _profile(
    conversion: float = 0.3, fatigue: float = 0.2, price: float = 0.5
) -> HiddenResponseProfile:
    return HiddenResponseProfile(
        person_id="person-0",
        conversion_propensity=conversion,
        fatigue_hazard=fatigue,
        price_sensitivity=price,
    )


def _action(
    incentive_bps: int = 0,
    customer_id: str = "cust-1",
    risk_id: str = "r-1",
    agent_id: str = "payment_retry_agent",
) -> ContactAction:
    return ContactAction(
        agent_id=agent_id,
        risk_id=risk_id,
        customer_id=customer_id,
        channel="sms",
        intent="payment_retry",
        incentive_bps=incentive_bps,
        scheduled_at=dt.datetime(2025, 9, 1, tzinfo=dt.timezone.utc),
    )


def _risk_item(risk_id: str = "r-1", amount: int = 10_000) -> RiskItem:
    return RiskItem(
        risk_id=risk_id,
        source="failed_payment",
        amount_paise=amount,
        root_cause="insufficient_funds",
        detected_at=dt.datetime(2025, 9, 1, tzinfo=dt.timezone.utc),
    )


# --- determinism --------------------------------------------------------


def test_same_rng_state_produces_the_same_outcome() -> None:
    profiles = {"cust-1": _profile()}
    outcome_a = Environment(profiles, _FixedRandomSource(0.1)).observe(_action(), _risk_item())
    outcome_b = Environment(profiles, _FixedRandomSource(0.1)).observe(_action(), _risk_item())
    assert outcome_a == outcome_b


# --- p_recover monotonicity (locked test requirements 9 and 10) --------


def test_increasing_prior_contacts_cannot_increase_response_probability() -> None:
    profile = _profile(conversion=0.5, fatigue=0.3, price=0.5)
    p0 = p_recover(profile, incentive_bps=0, prior_contacts=0)
    p1 = p_recover(profile, incentive_bps=0, prior_contacts=1)
    p5 = p_recover(profile, incentive_bps=0, prior_contacts=5)
    assert p0 >= p1 >= p5


def test_increasing_price_sensitivity_cannot_decrease_incentive_benefit() -> None:
    low_sensitivity = _profile(conversion=0.5, fatigue=0.0, price=0.1)
    high_sensitivity = _profile(conversion=0.5, fatigue=0.0, price=0.9)

    benefit_low = p_recover(low_sensitivity, incentive_bps=500, prior_contacts=0) - p_recover(
        low_sensitivity, incentive_bps=0, prior_contacts=0
    )
    benefit_high = p_recover(high_sensitivity, incentive_bps=500, prior_contacts=0) - p_recover(
        high_sensitivity, incentive_bps=0, prior_contacts=0
    )
    assert benefit_high >= benefit_low


# --- incentive economics (locked test requirement 11) -------------------


def test_incentive_is_zero_when_not_recovered() -> None:
    profile = _profile(conversion=0.9, fatigue=0.0, price=0.9)
    env = Environment({"cust-1": profile}, _FixedRandomSource(0.999999))  # forces recovered=False
    outcome = env.observe(_action(incentive_bps=500), _risk_item(amount=10_000))

    assert outcome.recovered is False
    assert outcome.amount_recovered_paise == 0
    assert outcome.incentive_paise == 0


def test_incentive_paise_computed_from_recovered_amount() -> None:
    profile = _profile(conversion=0.9, fatigue=0.0, price=0.9)
    env = Environment({"cust-1": profile}, _FixedRandomSource(0.0))  # forces recovered=True
    outcome = env.observe(_action(incentive_bps=500), _risk_item(amount=10_000))

    assert outcome.recovered is True
    assert outcome.amount_recovered_paise == 10_000
    assert outcome.incentive_paise == 500  # 10_000 * 500 / 10_000


def test_true_contact_count_accumulates_across_repeated_observes_for_same_customer() -> None:
    """Fatigue accumulates per CUSTOMER across distinct risk items (e.g.
    a customer with a failed payment AND an overdue invoice, each
    contacted once) — not by re-observing the same risk item, which
    Environment now rejects outright (see
    test_observing_the_same_risk_id_twice_is_rejected in
    tests/arm_a/test_exactly_once_invariant.py)."""
    profile = _profile(conversion=0.5, fatigue=0.5, price=0.0)
    env = Environment({"cust-1": profile}, _FixedRandomSource(0.5))

    # incentive_bps=0 and price=0.0 isolate the fatigue term: each
    # successive call's probability must be non-increasing. Three
    # distinct risk_ids for the same customer_id — a legitimate repeated
    # contact, unlike replaying one risk_id.
    outcome_1 = env.observe(_action(risk_id="r-1"), _risk_item("r-1"))
    outcome_2 = env.observe(_action(risk_id="r-2"), _risk_item("r-2"))
    outcome_3 = env.observe(_action(risk_id="r-3"), _risk_item("r-3"))

    # With a fixed draw of 0.5, a monotonically non-increasing probability
    # can only go from "recovers" to "stops recovering", never back.
    recoveries = [outcome_1.recovered, outcome_2.recovered, outcome_3.recovered]
    assert recoveries == sorted(recoveries, reverse=True)


def test_fatigue_accumulates_across_different_agents_for_the_same_customer() -> None:
    """Cross-agent fatigue (Phase 2 review criterion 6): _true_contacts is
    keyed by customer_id only — sim/environment.py's increment never
    reads action.agent_id — so contacts from two DIFFERENT agents against
    the same customer must accumulate into the same true contact count,
    and the response probability degrades accordingly regardless of
    which agent produced which contact. The prior fatigue test
    (test_true_contact_count_accumulates_across_repeated_observes_for_same_customer)
    only ever used one hardcoded agent_id; this closes that gap."""
    profile = _profile(conversion=0.5, fatigue=0.5, price=0.0)
    env = Environment({"cust-1": profile}, _FixedRandomSource(0.5))

    outcome_1 = env.observe(
        _action(risk_id="r-1", agent_id="payment_retry_agent"), _risk_item("r-1")
    )
    assert env._true_contacts["cust-1"] == 1

    outcome_2 = env.observe(
        _action(risk_id="r-2", agent_id="cart_recovery_agent"), _risk_item("r-2")
    )
    assert env._true_contacts["cust-1"] == 2

    outcome_3 = env.observe(
        _action(risk_id="r-3", agent_id="mandate_recovery_agent"), _risk_item("r-3")
    )
    assert env._true_contacts["cust-1"] == 3

    # incentive_bps=0 and price=0.0 isolate the fatigue term. With a fixed
    # draw of 0.5, a monotonically non-increasing probability can only go
    # from "recovers" to "stops recovering", never back — proving the
    # three cross-agent contacts genuinely degraded the same customer's
    # response, not three independent per-agent counters.
    recoveries = [outcome_1.recovered, outcome_2.recovered, outcome_3.recovered]
    assert recoveries == sorted(recoveries, reverse=True)
