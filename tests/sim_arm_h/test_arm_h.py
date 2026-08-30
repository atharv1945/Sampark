"""sim.arm_h — Arm H, Phase 7 (spec §11: "H — Holdout. No contact.")."""

from __future__ import annotations

from sim.arm_h import run_arm_h
from sim.cli import build_dataset

SEED = 42


def test_covers_the_full_population():
    _population, _signals, ledger = build_dataset(SEED)
    result = run_arm_h(SEED)
    assert len(result.natural_outcomes) == len(ledger.risk_items) == 20_000


def test_every_risk_item_appears_exactly_once():
    result = run_arm_h(SEED)
    risk_ids = [o.risk_id for o in result.natural_outcomes]
    assert len(risk_ids) == len(set(risk_ids))


def test_deterministic_across_repeated_calls():
    a = run_arm_h(SEED)
    b = run_arm_h(SEED)
    a_key = [(o.risk_id, o.recovered, o.amount_recovered_paise) for o in a.natural_outcomes]
    b_key = [(o.risk_id, o.recovered, o.amount_recovered_paise) for o in b.natural_outcomes]
    assert a_key == b_key


def test_different_seeds_produce_different_outcomes():
    a = run_arm_h(SEED)
    b = run_arm_h(7)
    a_recovered = sum(1 for o in a.natural_outcomes if o.recovered)
    b_recovered = sum(1 for o in b.natural_outcomes if o.recovered)
    assert a_recovered != b_recovered  # astronomically unlikely to tie by chance


def test_recovery_rate_is_plausible():
    """Sanity bound, not a precise regression pin: the implied natural
    rate should sit in the single-digit-percent range given the committed
    multiplier table (Phase 7 design lock, Decision 10) and P_BASE_MEAN's
    magnitude."""
    result = run_arm_h(SEED)
    rate = sum(1 for o in result.natural_outcomes if o.recovered) / len(result.natural_outcomes)
    assert 0.01 < rate < 0.15
