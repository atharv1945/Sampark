"""sim.arm_a_holdout — Arm A-H, Phase 7 (spec §8.9, §11)."""

from __future__ import annotations

from sim.arm_a import run_arm_a
from sim.arm_a_holdout import run_arm_a_holdout
from sim.holdout import assign, customer_amounts_from_risk_items
from sim.cli import build_dataset

SEED = 42


def test_zero_fraction_holds_out_nobody():
    result = run_arm_a_holdout(seed=SEED, fraction=0.0)
    assert result.holdout_customer_ids == frozenset()
    assert result.natural_outcomes == ()


def test_contact_plus_natural_outcomes_cover_every_risk_item():
    _population, _signals, ledger = build_dataset(SEED)
    result = run_arm_a_holdout(seed=SEED, fraction=0.10)
    contacted_ids = {o.risk_id for o in result.contact_outcomes}
    natural_ids = {o.risk_id for o in result.natural_outcomes}
    assert contacted_ids & natural_ids == set()  # disjoint — exactly-once
    assert contacted_ids | natural_ids == {item.risk_id for item in ledger.risk_items}


def test_no_contact_action_targets_a_held_out_customer():
    result = run_arm_a_holdout(seed=SEED, fraction=0.10)
    for outcome in result.contact_outcomes:
        assert outcome.customer_id not in result.holdout_customer_ids


def test_every_natural_outcome_belongs_to_a_held_out_customer():
    result = run_arm_a_holdout(seed=SEED, fraction=0.10)
    for outcome in result.natural_outcomes:
        assert outcome.customer_id in result.holdout_customer_ids


def test_held_out_customer_count_matches_holdout_assign():
    _population, _signals, ledger = build_dataset(SEED)
    amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    expected = assign(SEED, 0.10, amounts)
    result = run_arm_a_holdout(seed=SEED, fraction=0.10)
    assert result.holdout_customer_ids == expected


def test_recovery_sequence_at_zero_fraction_matches_frozen_arm_a_exactly():
    """Real-scale check: at fraction=0.0 every action reaches observe() in
    the identical order as the frozen sim.arm_a.run_arm_a, and — by the
    stream-isolation property proven in tests/sim_environment/test_world_v2.py
    — the resulting recovered/amount_recovered_paise/incentive_paise
    sequence must be identical, even though this runner builds its
    Environment for world="v2" (only opt_out/opt_out_channel may
    differ, which is expected and tested separately)."""
    frozen_outcomes = run_arm_a(SEED)
    holdout_outcomes = run_arm_a_holdout(seed=SEED, fraction=0.0).contact_outcomes

    assert len(frozen_outcomes) == len(holdout_outcomes) == 20_000
    frozen_key = [(o.risk_id, o.recovered, o.amount_recovered_paise, o.incentive_paise) for o in frozen_outcomes]
    holdout_key = [
        (o.risk_id, o.recovered, o.amount_recovered_paise, o.incentive_paise) for o in holdout_outcomes
    ]
    assert frozen_key == holdout_key


def test_deterministic_across_repeated_calls():
    a = run_arm_a_holdout(seed=SEED, fraction=0.10)
    b = run_arm_a_holdout(seed=SEED, fraction=0.10)
    assert [o.recovered for o in a.contact_outcomes] == [o.recovered for o in b.contact_outcomes]
    assert [o.recovered for o in a.natural_outcomes] == [o.recovered for o in b.natural_outcomes]
    assert a.holdout_customer_ids == b.holdout_customer_ids
