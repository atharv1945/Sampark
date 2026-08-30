"""The property the whole sensitivity sweep rests on — Phase 9A.

`sim/sensitivity.py` claims that varying `beta_fatigue` / `beta_incentive`
changes which contacts SUCCEED but never which contacts HAPPEN, because under
world v1 no realized outcome feeds back into any decision. If that claim is
false, the sweep is not a pure re-observation and every interpretation drawn
from it is void.

The claim is asserted here directly against the real runner rather than argued
in a docstring. These tests are slow (each `run_arm_b` is roughly a minute in
memory) and that is the correct trade: this is the load-bearing invariant of
the phase.
"""

from __future__ import annotations

import pytest

from sim.arm_a import run_arm_a
from sim.arm_b import BACKEND_MEMORY, run_arm_b
from sim.environment import BETA_FATIGUE, BETA_INCENTIVE, p_recover
from sim.metrics import compute_metrics
from sim.population import HiddenResponseProfile

SEED = 42


@pytest.fixture(scope="module")
def arm_b_at_frozen_beta():
    return run_arm_b(SEED, backend=BACKEND_MEMORY, beta_fatigue=BETA_FATIGUE)


@pytest.fixture(scope="module")
def arm_b_at_zero_fatigue():
    return run_arm_b(SEED, backend=BACKEND_MEMORY, beta_fatigue=0.0)


def test_decisions_are_byte_identical_across_beta_fatigue(arm_b_at_frozen_beta, arm_b_at_zero_fatigue):
    """No admission, ranking, grant, deferral or denial may move."""
    assert arm_b_at_frozen_beta.decisions == arm_b_at_zero_fatigue.decisions


def test_contact_counts_are_identical_across_beta_fatigue(arm_b_at_frozen_beta, arm_b_at_zero_fatigue):
    frozen = compute_metrics(arm_b_at_frozen_beta.outcomes)
    zeroed = compute_metrics(arm_b_at_zero_fatigue.outcomes)
    assert frozen["total_contacts"] == zeroed["total_contacts"]


def test_outcomes_DO_differ_across_beta_fatigue(arm_b_at_frozen_beta, arm_b_at_zero_fatigue):
    """The negative control for the two tests above. If recoveries were also
    identical, `beta_fatigue` would simply not be wired to anything and the
    invariance tests would be passing vacuously."""
    frozen = compute_metrics(arm_b_at_frozen_beta.outcomes)
    zeroed = compute_metrics(arm_b_at_zero_fatigue.outcomes)
    assert frozen["total_recoveries"] != zeroed["total_recoveries"]


def test_removing_fatigue_cannot_reduce_recoveries(arm_b_at_frozen_beta, arm_b_at_zero_fatigue):
    """Directional: fatigue only ever subtracts from the recovery logit, so
    setting it to zero must not make customers harder to recover."""
    frozen = compute_metrics(arm_b_at_frozen_beta.outcomes)
    zeroed = compute_metrics(arm_b_at_zero_fatigue.outcomes)
    assert zeroed["total_recoveries"] >= frozen["total_recoveries"]


def test_arm_a_contact_count_is_invariant_across_beta():
    """Arm A's agents select every action before any observation, so its
    denominator is fixed at 20,000 whatever the world does."""
    frozen = compute_metrics(run_arm_a(SEED))
    swept = compute_metrics(run_arm_a(SEED, beta_fatigue=0.0, beta_incentive=8.0))
    assert frozen["total_contacts"] == swept["total_contacts"] == 20_000


# --- default preservation: the parameterization must change nothing ---------


def test_run_arm_b_without_beta_arguments_reproduces_the_committed_evidence():
    """The regression proof for the Phase 9 parameterization. If adding the
    two keyword-only parameters had perturbed anything, this fails."""
    import json
    from pathlib import Path

    frozen = json.loads(
        (Path(__file__).resolve().parents[2] / "results" / f"arm_b_metrics_{SEED}.json").read_text(
            encoding="utf-8"
        )
    )
    m = compute_metrics(run_arm_b(SEED, backend=BACKEND_MEMORY).outcomes)
    for key in (
        "total_contacts",
        "total_recoveries",
        "recovered_amount_paise",
        "incentive_spend_paise",
        "recovered_amount_per_contact_paise",
    ):
        assert m[key] == frozen[key], key


def test_explicit_frozen_betas_equal_the_default_path(arm_b_at_frozen_beta):
    """Passing the frozen values explicitly must be indistinguishable from
    passing nothing."""
    default = compute_metrics(run_arm_b(SEED, backend=BACKEND_MEMORY).outcomes)
    explicit = compute_metrics(arm_b_at_frozen_beta.outcomes)
    assert default == explicit


# --- p_recover's own algebra ------------------------------------------------


def _profile(cp=0.3, fatigue=0.4, price=0.5) -> HiddenResponseProfile:
    return HiddenResponseProfile(
        person_id="p", conversion_propensity=cp, fatigue_hazard=fatigue, price_sensitivity=price
    )


def test_p_recover_defaults_match_the_frozen_constants():
    profile = _profile()
    assert p_recover(profile, 200, 3) == p_recover(
        profile, 200, 3, beta_fatigue=BETA_FATIGUE, beta_incentive=BETA_INCENTIVE
    )


def test_scaling_beta_fatigue_is_equivalent_to_scaling_the_hidden_hazard():
    """The algebraic identity the sweep's interpretation depends on: moving
    `beta_fatigue` by k is exactly moving every customer's `fatigue_hazard`
    by k, so the sweep is a statement about the WORLD, not about the system."""
    k = 2.5
    scaled_beta = p_recover(_profile(fatigue=0.4), 200, 3, beta_fatigue=k)
    scaled_hazard = p_recover(_profile(fatigue=0.4 * k), 200, 3, beta_fatigue=1.0)
    assert scaled_beta == pytest.approx(scaled_hazard, rel=1e-12)


def test_zero_beta_fatigue_removes_the_contact_history_term_entirely():
    profile = _profile()
    first = p_recover(profile, 0, 0, beta_fatigue=0.0)
    tenth = p_recover(profile, 0, 10, beta_fatigue=0.0)
    assert first == pytest.approx(tenth, rel=1e-12)
    assert first == pytest.approx(profile.conversion_propensity, rel=1e-9)


def test_higher_beta_fatigue_never_raises_recovery_probability():
    profile = _profile()
    probs = [p_recover(profile, 200, 2, beta_fatigue=b) for b in (0.0, 0.5, 1.0, 2.0)]
    assert probs == sorted(probs, reverse=True)


def test_higher_beta_incentive_never_lowers_recovery_probability():
    profile = _profile()
    probs = [p_recover(profile, 300, 1, beta_incentive=b) for b in (0.0, 2.0, 4.0, 8.0)]
    assert probs == sorted(probs)
