"""sim.arm_b — Arm B-H (memory backend), Phase 7 (spec §8.9, §11).

Memory-backend only here (fast, no Postgres) — proves the holdout-filtering
and natural-recovery MECHANISM. Opt-out ENFORCEMENT (cross-window denial)
is Postgres-only (sim/optout_writeback.py's docstring) and is covered
separately in tests/sim_arm_b_holdout/test_arm_b_holdout_postgres.py
(module-marked `postgres`).
"""

from __future__ import annotations

import pytest

from sim.arm_b import BACKEND_MEMORY, run_arm_b, run_arm_b_holdout
from sim.cli import build_dataset
from sim.holdout import assign, customer_amounts_from_risk_items

SEED = 42


@pytest.fixture(scope="module")
def result_f10():
    return run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY)


@pytest.fixture(scope="module")
def result_f00():
    return run_arm_b_holdout(seed=SEED, fraction=0.0, backend=BACKEND_MEMORY)


def test_zero_fraction_holds_out_nobody(result_f00):
    assert result_f00.holdout_customer_ids == frozenset()


def test_contact_plus_natural_outcomes_cover_every_risk_item(result_f10):
    _population, _signals, ledger = build_dataset(SEED)
    contacted_ids = {o.risk_id for o in result_f10.contact_outcomes}
    natural_ids = {o.risk_id for o in result_f10.natural_outcomes}
    assert contacted_ids & natural_ids == set()
    assert contacted_ids | natural_ids == {item.risk_id for item in ledger.risk_items}


def test_no_contact_action_targets_a_held_out_customer(result_f10):
    for outcome in result_f10.contact_outcomes:
        assert outcome.customer_id not in result_f10.holdout_customer_ids


def test_every_held_out_customer_risk_item_is_natural_not_contacted(result_f10):
    _population, _signals, ledger = build_dataset(SEED)
    natural_customer_ids = {o.customer_id for o in result_f10.natural_outcomes}
    for customer_id in result_f10.holdout_customer_ids:
        items = [item for item in ledger.risk_items if ledger.risk_customer_map[item.risk_id] == customer_id]
        if items:  # a held-out customer with >=1 risk item must appear only in natural_outcomes
            assert customer_id in natural_customer_ids


def test_natural_outcomes_include_allocator_declined_items_not_just_holdout(result_f10):
    """Phase 7 design lock, Decision 1 (Option 2): natural recovery applies
    to EVERY uncontacted item, not only held-out customers' items. With
    ~10% of customers held out but roughly HALF of all items uncontacted
    (matching headline Arm B's admission rate), most natural outcomes must
    belong to NON-held-out customers whose candidate was declined."""
    non_holdout_natural = [
        o for o in result_f10.natural_outcomes if o.customer_id not in result_f10.holdout_customer_ids
    ]
    assert len(non_holdout_natural) > 0
    assert len(non_holdout_natural) > len(result_f10.natural_outcomes) / 2


def test_held_out_customer_count_matches_holdout_assign(result_f10):
    _population, _signals, ledger = build_dataset(SEED)
    amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    expected = assign(SEED, 0.10, amounts)
    assert result_f10.holdout_customer_ids == expected


def test_total_recovered_paise_exceeds_contact_only_recovery(result_f10):
    """World v2's whole point: natural recovery adds strictly positive
    recovered rupees beyond the contact-only figure."""
    contact_recovered = sum(o.amount_recovered_paise for o in result_f10.contact_outcomes)
    natural_recovered = sum(o.amount_recovered_paise for o in result_f10.natural_outcomes)
    assert natural_recovered > 0
    assert contact_recovered + natural_recovered > contact_recovered


def test_contact_recovery_at_zero_fraction_matches_frozen_headline_arm_b(result_f00):
    """Real-scale regression-adjacent check: at fraction=0.0, with every
    other parameter at its Phase 4 default, the CONTACT outcome sequence's
    recovered/amount_recovered_paise/incentive_paise must match the frozen
    headline run_arm_b(seed, backend='memory') exactly, risk_id for
    risk_id — proving world='v2' with an empty holdout does not perturb
    a single admission/ranking/grant decision (by construction: natural
    recovery is drawn strictly after this comparison's outcomes already
    exist, and stream isolation is proven separately in
    tests/sim_environment/test_world_v2.py)."""
    headline = run_arm_b(seed=SEED, backend=BACKEND_MEMORY)

    headline_key = sorted((o.risk_id, o.recovered, o.amount_recovered_paise, o.incentive_paise) for o in headline.outcomes)
    holdout_key = sorted(
        (o.risk_id, o.recovered, o.amount_recovered_paise, o.incentive_paise) for o in result_f00.contact_outcomes
    )
    assert headline_key == holdout_key


def test_deterministic_across_repeated_calls(result_f10):
    b = run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY)
    a_contact = sorted((o.risk_id, o.recovered) for o in result_f10.contact_outcomes)
    b_contact = sorted((o.risk_id, o.recovered) for o in b.contact_outcomes)
    a_natural = sorted((o.risk_id, o.recovered) for o in result_f10.natural_outcomes)
    b_natural = sorted((o.risk_id, o.recovered) for o in b.natural_outcomes)
    assert a_contact == b_contact
    assert a_natural == b_natural
    assert result_f10.holdout_customer_ids == b.holdout_customer_ids
