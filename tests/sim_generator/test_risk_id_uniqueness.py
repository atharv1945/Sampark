"""risk_id invariants — Phase 1 data-integrity fix.

risk_id was originally derived only from generation-order position
(risk-000000, risk-000001, ...), which made two different seeds produce
the same ids for the same positions. It is now seed-scoped
(risk-{seed}-{position}). This file checks the four required invariants
directly, independent of the Postgres loader (tests/sim_generator/
test_postgres_load.py checks what the loader does when they're violated).
"""

from __future__ import annotations

import pytest

from sim.cli import build_dataset

_SEED_A = 42
_SEED_B = 43


@pytest.fixture(scope="module")
def signals_a():
    _, signals, _ = build_dataset(_SEED_A)
    return signals


@pytest.fixture(scope="module")
def signals_a_again():
    _, signals, _ = build_dataset(_SEED_A)
    return signals


@pytest.fixture(scope="module")
def signals_b():
    _, signals, _ = build_dataset(_SEED_B)
    return signals


def test_same_seed_produces_identical_risk_ids(signals_a, signals_a_again):
    ids_first = [s.signal_id for s in signals_a]
    ids_second = [s.signal_id for s in signals_a_again]
    assert ids_first == ids_second


def test_different_seeds_produce_no_risk_id_collisions(signals_a, signals_b):
    ids_a = {s.signal_id for s in signals_a}
    ids_b = {s.signal_id for s in signals_b}
    assert ids_a.isdisjoint(ids_b)


def test_same_seed_and_same_item_position_gives_same_risk_id(signals_a, signals_a_again):
    assert signals_a[0].signal_id == signals_a_again[0].signal_id
    assert signals_a[12345].signal_id == signals_a_again[12345].signal_id


def test_same_seed_and_different_item_position_gives_different_risk_id(signals_a):
    assert signals_a[0].signal_id != signals_a[1].signal_id
    assert signals_a[0].signal_id != signals_a[19999].signal_id


def test_risk_id_is_unique_within_one_seeded_dataset(signals_a):
    ids = [s.signal_id for s in signals_a]
    assert len(ids) == len(set(ids))


def test_risk_id_embeds_the_seed(signals_a, signals_b):
    assert all(f"risk-{_SEED_A}-" in s.signal_id for s in signals_a)
    assert all(f"risk-{_SEED_B}-" in s.signal_id for s in signals_b)
