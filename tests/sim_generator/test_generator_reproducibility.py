"""Phase 1 exit criterion: seeded, deterministic, reproducible across two
runs — spec §18.1.
"""

from __future__ import annotations

import pytest

from sim.cli import build_dataset

_SEED = 42
_OTHER_SEED = 7


@pytest.fixture(scope="module")
def run_pair():
    """Two independent builds from the same seed — the actual
    "reproducible across two runs" check."""
    return build_dataset(_SEED), build_dataset(_SEED)


@pytest.fixture(scope="module")
def other_seed_run():
    return build_dataset(_OTHER_SEED)


def test_same_seed_produces_identical_signals(run_pair):
    (_, signals_a, _), (_, signals_b, _) = run_pair
    assert signals_a == signals_b


def test_same_seed_produces_identical_hidden_response_profiles(run_pair):
    (population_a, _, _), (population_b, _, _) = run_pair
    assert population_a.hidden_response == population_b.hidden_response
    assert population_a.people == population_b.people


def test_same_seed_produces_identical_ledger(run_pair):
    (_, _, ledger_a), (_, _, ledger_b) = run_pair

    assert [c.model_dump() for c in ledger_a.customers] == [
        c.model_dump() for c in ledger_b.customers
    ]
    assert [r.model_dump() for r in ledger_a.risk_items] == [
        r.model_dump() for r in ledger_b.risk_items
    ]
    assert ledger_a.risk_customer_map == ledger_b.risk_customer_map
    assert {
        cust_id: state.model_dump() for cust_id, state in ledger_a.contact_states.items()
    } == {cust_id: state.model_dump() for cust_id, state in ledger_b.contact_states.items()}


def test_different_seed_produces_different_output(run_pair, other_seed_run):
    (_, signals_a, _), _ = run_pair
    _, signals_other, _ = other_seed_run
    assert signals_a != signals_other


def test_no_stdlib_random_module_is_used_by_the_generator():
    """spec/CLAUDE.md requirement, checked mechanically rather than by
    convention: every draw must go through a seeded numpy Generator."""
    import sim.generator as generator_module
    import sim.population as population_module

    for module in (generator_module, population_module):
        assert "random" not in module.__dict__, (
            f"{module.__name__} imports the stdlib random module"
        )
