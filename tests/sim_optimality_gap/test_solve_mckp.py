"""_solve_mckp -- exact multiple-choice knapsack correctness, checked
against brute force. No database, no simulator: pure combinatorics."""

from __future__ import annotations

import itertools

from sim.optimality_gap import _solve_mckp


def _brute_force_mckp(groups, capacity, granularity):
    """Every group contributes AT MOST one (value, weight) item (or
    none); enumerate all combinations, and among the feasible ones
    (weight-units sum <= capacity-units), return the max value. This is
    the reference: it must always agree with the DP for small inputs."""
    capacity_units = capacity // granularity
    choices_per_group = [[(0.0, 0)] + list(group) for group in groups]  # (0.0, 0) = "skip this group"
    best = 0.0
    for combo in itertools.product(*choices_per_group):
        total_weight_units = sum(-(-w // granularity) for _v, w in combo if w > 0)
        if total_weight_units <= capacity_units:
            total_value = sum(v for v, _w in combo)
            best = max(best, total_value)
    return best


def test_matches_brute_force_on_small_random_instances():
    import random

    rng = random.Random(1234)  # local, deterministic -- not the banned module-level np.random
    for _trial in range(20):
        n_groups = rng.randint(1, 5)
        groups = []
        for _ in range(n_groups):
            n_items = rng.randint(1, 3)
            groups.append([(rng.uniform(1, 100), rng.randint(1, 50)) for _ in range(n_items)])
        capacity = rng.randint(10, 100)
        granularity = 1
        assert _solve_mckp(groups, capacity, granularity) == _brute_force_mckp(groups, capacity, granularity)


def test_empty_groups_yield_zero():
    assert _solve_mckp([], 1000, 100) == 0.0


def test_single_item_exceeding_capacity_is_excluded():
    assert _solve_mckp([[(50.0, 10_000)]], 100, 100) == 0.0


def test_single_item_fitting_exactly_is_included():
    assert _solve_mckp([[(50.0, 100)]], 100, 100) == 50.0


def test_picks_the_better_of_two_options_in_one_group():
    result = _solve_mckp([[(10.0, 50), (20.0, 50)]], 100, 100)
    assert result == 20.0


def test_granularity_rounds_weight_up_never_down():
    # weight=101 at granularity=100 must consume 2 units (ceil), not 1 --
    # otherwise the DP would claim a combination fits that, in real
    # paise, does not.
    result = _solve_mckp([[(10.0, 101)]], 100, 100)  # capacity 1 unit, item needs 2 units
    assert result == 0.0
