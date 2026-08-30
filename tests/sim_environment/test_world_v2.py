"""Environment world v2 — Phase 7 (spec §8.9). Proves:

  1. world="v1" (default) is byte-identical to pre-Phase-7 behavior.
  2. The response-model RNG draw sequence is untouched by the mere
     existence of the new RNG namespaces (stream isolation).
  3. Opt-out draws satisfy their locked properties (zero without contact
     is structural; monotone in prior_contacts and fatigue_hazard).
  4. observe_natural() shares the exactly-once guard with observe() and
     is unavailable under world="v1".
  5. Everything is deterministic given (seed, world).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from agents.types import ContactAction
from sampark.contracts import RiskItem
from sim.environment import DuplicateObservationError, Environment, OPTOUT_BASE, OPTOUT_MAX, p_optout
from sim.population import HiddenResponseProfile


def _profile(person_id="p1", conversion_propensity=0.3, fatigue_hazard=0.2, price_sensitivity=0.5):
    return HiddenResponseProfile(
        person_id=person_id,
        conversion_propensity=conversion_propensity,
        fatigue_hazard=fatigue_hazard,
        price_sensitivity=price_sensitivity,
    )


def _risk_item(risk_id="r-1", source="failed_payment", root_cause="insufficient_funds", amount_paise=100_000):
    return RiskItem(
        risk_id=risk_id,
        source=source,
        amount_paise=amount_paise,
        root_cause=root_cause,
        detected_at=dt.datetime(2025, 9, 1, tzinfo=dt.timezone.utc),
    )


def _action(agent_id="agent-1", risk_id="r-1", customer_id="c-1", incentive_bps=0, channel="sms"):
    return ContactAction(
        agent_id=agent_id,
        risk_id=risk_id,
        customer_id=customer_id,
        channel=channel,
        intent="payment_retry",
        incentive_bps=incentive_bps,
        scheduled_at=dt.datetime(2025, 9, 1, 10, 0, tzinfo=dt.timezone.utc),
    )


class _FixedRandomSource:
    """A trivial deterministic RandomSource for isolation testing — always
    returns the same sequence regardless of call order elsewhere, so any
    perturbation in `.random()` call COUNT is directly observable by
    counting calls, not just by reading returned values."""

    def __init__(self, values):
        self._values = list(values)
        self._index = 0
        self.call_count = 0

    def random(self) -> float:
        value = self._values[self._index % len(self._values)]
        self._index += 1
        self.call_count += 1
        return value


# --- world="v1" is unaffected by the new machinery existing at all ---------


def test_world_v1_is_the_default():
    from sim.population import Population

    population = Population(people=(), hidden_response=(_profile(),))
    env = Environment({"c-1": _profile()}, _FixedRandomSource([0.0]))
    assert env._world == "v1"


def test_observe_under_world_v1_never_sets_opt_out():
    env = Environment({"c-1": _profile(fatigue_hazard=1.0)}, _FixedRandomSource([0.0, 0.0, 0.0]))
    outcome = env.observe(_action(), _risk_item())
    assert outcome.opt_out is False
    assert outcome.opt_out_channel is None


def test_observe_natural_raises_under_world_v1():
    env = Environment({"c-1": _profile()}, _FixedRandomSource([0.0]))
    with pytest.raises(RuntimeError):
        env.observe_natural(_risk_item(), "c-1", observed_at=dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc))


def test_build_world_v1_constructs_no_extra_rng():
    from sim.cli import build_dataset

    population, signals, ledger = build_dataset(42)
    env = Environment.build(population, signals, ledger, seed=42, world="v1")
    assert env._natural_rng is None
    assert env._optout_rng is None


def test_invalid_world_raises():
    with pytest.raises(ValueError):
        Environment({"c-1": _profile()}, _FixedRandomSource([0.0]), world="v3")


def test_world_v2_requires_both_extra_rngs():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError):
        Environment({"c-1": _profile()}, _FixedRandomSource([0.0]), world="v2", natural_rng=rng, optout_rng=None)


# --- stream isolation: the core Phase 4 protection claim --------------------


def test_response_model_rng_call_count_unaffected_by_world():
    """The single most important property in this module: self._rng
    (the response-model draw) is called EXACTLY ONCE per observe() call
    whether world is v1 or v2 — proving the opt-out draw uses a wholly
    separate Generator and never touches this one."""
    rng_v1 = _FixedRandomSource([0.1] * 10)
    env_v1 = Environment({"c-1": _profile()}, rng_v1, world="v1")
    env_v1.observe(_action(risk_id="r-1"), _risk_item(risk_id="r-1"))
    assert rng_v1.call_count == 1

    rng_v2 = _FixedRandomSource([0.1] * 10)
    optout_rng = np.random.default_rng(1)
    natural_rng = np.random.default_rng(2)
    env_v2 = Environment(
        {"c-1": _profile()}, rng_v2, world="v2", natural_rng=natural_rng, optout_rng=optout_rng
    )
    env_v2.observe(_action(risk_id="r-1"), _risk_item(risk_id="r-1"))
    assert rng_v2.call_count == 1


def test_recovered_outcome_identical_between_v1_and_v2_for_identical_rng_draws():
    """world="v2" existing must not change what `recovered` evaluates to,
    given the identical underlying response-model RNG draw sequence."""
    action = _action()
    item = _risk_item()
    profile = _profile(conversion_propensity=0.3)

    rng_v1 = _FixedRandomSource([0.05])  # below p_recover -> recovered True
    env_v1 = Environment({"c-1": profile}, rng_v1, world="v1")
    outcome_v1 = env_v1.observe(action, item)

    rng_v2 = _FixedRandomSource([0.05])
    env_v2 = Environment(
        {"c-1": profile},
        rng_v2,
        world="v2",
        natural_rng=np.random.default_rng(5),
        optout_rng=np.random.default_rng(6),
    )
    outcome_v2 = env_v2.observe(action, item)

    assert outcome_v1.recovered == outcome_v2.recovered
    assert outcome_v1.amount_recovered_paise == outcome_v2.amount_recovered_paise
    assert outcome_v1.incentive_paise == outcome_v2.incentive_paise


def test_full_environment_build_stream_isolation_at_real_scale():
    """End-to-end version of the same claim, against the real 20k-item
    dataset: the sequence of `recovered` outcomes Arm A would observe is
    byte-identical whether the Environment was built for world="v1" or
    world="v2", given the identical action replay order. This is what
    proves adding the new RNG namespaces changed nothing about the
    existing response-model draw stream."""
    from sim.arm_a import _AGENTS, _build_ledger_view, _sort_key
    from sim.cli import build_dataset

    seed = 42
    population, signals, ledger = build_dataset(seed)
    view = _build_ledger_view(ledger)
    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}

    actions = []
    for agent in _AGENTS:
        actions.extend(agent.select_actions(view))
    actions.sort(key=_sort_key)
    # Keep this fast: only the first 500 actions need to prove the point.
    actions = actions[:500]

    env_v1 = Environment.build(population, signals, ledger, seed, world="v1")
    outcomes_v1 = [env_v1.observe(a, risk_items_by_id[a.risk_id]) for a in actions]

    env_v2 = Environment.build(population, signals, ledger, seed, world="v2")
    outcomes_v2 = [env_v2.observe(a, risk_items_by_id[a.risk_id]) for a in actions]

    assert [o.recovered for o in outcomes_v1] == [o.recovered for o in outcomes_v2]
    assert [o.amount_recovered_paise for o in outcomes_v1] == [o.amount_recovered_paise for o in outcomes_v2]
    assert [o.incentive_paise for o in outcomes_v1] == [o.incentive_paise for o in outcomes_v2]


# --- opt-out draw properties -------------------------------------------------


def test_p_optout_zero_prior_contacts_still_nonnegative_and_bounded():
    p = p_optout(_profile(fatigue_hazard=0.5), prior_contacts=0)
    assert 0.0 <= p <= OPTOUT_MAX


def test_p_optout_monotone_in_prior_contacts():
    profile = _profile(fatigue_hazard=0.5)
    p0 = p_optout(profile, prior_contacts=0)
    p1 = p_optout(profile, prior_contacts=1)
    p2 = p_optout(profile, prior_contacts=2)
    assert p0 <= p1 <= p2


def test_p_optout_monotone_in_fatigue_hazard():
    low = p_optout(_profile(fatigue_hazard=0.1), prior_contacts=1)
    high = p_optout(_profile(fatigue_hazard=0.9), prior_contacts=1)
    assert high > low


def test_p_optout_capped_at_optout_max():
    p = p_optout(_profile(fatigue_hazard=1.0), prior_contacts=1000)
    assert p == OPTOUT_MAX


def test_p_optout_is_pure_no_rng_no_state():
    profile = _profile(fatigue_hazard=0.4)
    a = p_optout(profile, prior_contacts=3)
    b = p_optout(profile, prior_contacts=3)
    assert a == b


def test_observe_can_draw_opt_out_true_under_world_v2():
    """With fatigue_hazard=1.0 and OPTOUT_MAX capping the probability,
    a rigged optout_rng that always returns 0.0 must trigger opt_out."""
    profile = _profile(fatigue_hazard=1.0)
    optout_rng = _FixedRandomSource([0.0])
    env = Environment(
        {"c-1": profile},
        _FixedRandomSource([0.99]),  # keep recovered False, irrelevant here
        world="v2",
        natural_rng=np.random.default_rng(1),
        optout_rng=optout_rng,
    )
    outcome = env.observe(_action(channel="whatsapp"), _risk_item())
    assert outcome.opt_out is True
    assert outcome.opt_out_channel == "whatsapp"


def test_observe_opt_out_false_when_draw_exceeds_probability():
    profile = _profile(fatigue_hazard=0.01)
    optout_rng = _FixedRandomSource([0.999])
    env = Environment(
        {"c-1": profile},
        _FixedRandomSource([0.99]),
        world="v2",
        natural_rng=np.random.default_rng(1),
        optout_rng=optout_rng,
    )
    outcome = env.observe(_action(), _risk_item())
    assert outcome.opt_out is False


# --- observe_natural() -------------------------------------------------------


def test_observe_natural_returns_natural_outcome_with_expected_shape():
    env = Environment(
        {"c-1": _profile(conversion_propensity=0.3)},
        _FixedRandomSource([0.99]),
        world="v2",
        natural_rng=_FixedRandomSource([0.0]),  # forces recovered True
        optout_rng=np.random.default_rng(1),
    )
    horizon = dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc)
    outcome = env.observe_natural(_risk_item(root_cause="issuer_downtime"), "c-1", observed_at=horizon)
    assert outcome.risk_id == "r-1"
    assert outcome.customer_id == "c-1"
    assert outcome.recovered is True
    assert outcome.amount_recovered_paise == 100_000
    assert outcome.observed_at == horizon
    assert 0.0 < outcome.p_natural < 1.0


def test_observe_natural_shares_exactly_once_guard_with_observe():
    env = Environment(
        {"c-1": _profile()},
        _FixedRandomSource([0.99]),
        world="v2",
        natural_rng=np.random.default_rng(1),
        optout_rng=np.random.default_rng(2),
    )
    item = _risk_item()
    env.observe(_action(), item)
    with pytest.raises(DuplicateObservationError):
        env.observe_natural(item, "c-1", observed_at=dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc))


def test_observe_natural_twice_for_same_item_raises():
    env = Environment(
        {"c-1": _profile()},
        _FixedRandomSource([0.99]),
        world="v2",
        natural_rng=np.random.default_rng(1),
        optout_rng=np.random.default_rng(2),
    )
    item = _risk_item()
    horizon = dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc)
    env.observe_natural(item, "c-1", observed_at=horizon)
    with pytest.raises(DuplicateObservationError):
        env.observe_natural(item, "c-1", observed_at=horizon)


def test_observe_natural_is_deterministic_given_seed():
    from sim.cli import build_dataset

    seed = 42
    population, signals, ledger = build_dataset(seed)
    horizon = dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc)
    item = ledger.risk_items[0]
    customer_id = ledger.risk_customer_map[item.risk_id]

    env_a = Environment.build(population, signals, ledger, seed, world="v2")
    env_b = Environment.build(population, signals, ledger, seed, world="v2")

    outcome_a = env_a.observe_natural(item, customer_id, observed_at=horizon)
    outcome_b = env_b.observe_natural(item, customer_id, observed_at=horizon)

    assert outcome_a.recovered == outcome_b.recovered
    assert outcome_a.p_natural == outcome_b.p_natural
