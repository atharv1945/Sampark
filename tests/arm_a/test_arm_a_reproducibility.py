"""Phase 2 exit-criterion reproducibility: same seed -> byte-identical
metrics; different seed -> different metrics. Mirrors the structure of
tests/sim_generator/test_generator_reproducibility.py."""

from __future__ import annotations

import json

import pytest

from sim.arm_a import run_arm_a
from sim.metrics import compute_metrics

_SEED = 42
_OTHER_SEED = 7


@pytest.fixture(scope="module")
def run_pair():
    """Two independent Arm A runs from the same seed."""
    return run_arm_a(_SEED), run_arm_a(_SEED)


@pytest.fixture(scope="module")
def other_seed_outcomes():
    return run_arm_a(_OTHER_SEED)


def test_same_seed_produces_identical_outcomes(run_pair) -> None:
    outcomes_a, outcomes_b = run_pair
    assert outcomes_a == outcomes_b


def test_same_seed_produces_byte_identical_metrics_json(run_pair) -> None:
    outcomes_a, outcomes_b = run_pair
    metrics_a = compute_metrics(outcomes_a)
    metrics_b = compute_metrics(outcomes_b)
    assert json.dumps(metrics_a, sort_keys=True) == json.dumps(metrics_b, sort_keys=True)


def test_different_seed_produces_different_metrics(run_pair, other_seed_outcomes) -> None:
    outcomes_a, _ = run_pair
    metrics_a = compute_metrics(outcomes_a)
    metrics_other = compute_metrics(other_seed_outcomes)
    assert metrics_a != metrics_other
