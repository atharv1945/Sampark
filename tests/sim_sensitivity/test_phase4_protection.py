"""Phase 4 protection, asserted in the test suite — Phase 9.

CLAUDE.md §3 makes six paths human-owned and frozen at `aa87123`. Until now
that protection was verified by running `git diff` by hand at the end of each
phase. Phase 9 adds keyword-only parameters to `sim/environment.py` and two new
`sim/` modules, so the protection is worth asserting where it runs every time.

Two independent mechanisms, deliberately:
  1. The frozen VALUES are pinned here, so an edit to the constants fails even
     in a checkout with no git history.
  2. `git diff aa87123 HEAD` over all six paths must be empty, which catches a
     change these tests do not know to look for (a new file under
     `policy/hard/`, a changed docstring, a reordered rule).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE4_SHA = "aa87123"

PROTECTED_PATHS = (
    "sampark/allocator/constants.py",
    "sampark/allocator/calibrated.py",
    "sampark/budget/issuance.py",
    "sampark/policy/hard/",
    "sampark/policy/soft/",
    "sampark/policy/types.py",
)


def test_frozen_allocator_constants_are_unchanged():
    """Pinned values, not a git check. These are the numbers every committed
    result file was produced against."""
    from sampark.allocator import constants as c

    assert c.CONTACT_CAP_24H == 1
    assert c.CONTACT_CAP_7D == 2
    assert c.QUIET_HOURS_START_HOUR == 21
    assert c.QUIET_HOURS_END_HOUR == 9
    assert c.FORWARD_HORIZON_DAYS == 30.0
    assert c.MAX_DEFERRAL_WINDOWS == 7
    assert c.AGING_BONUS_PAISE == 11_070
    assert c.GRANT_TTL_HOURS == 2.0
    assert c.MAX_SERIALIZATION_RETRIES == 5
    assert c.MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW == 3_679_105
    assert c.CUSTOMER_MARGIN_BPS == 500
    assert c.MEAN_AMOUNT_PAISE == 387_607.0
    assert c.LAMBDA_PER_CUSTOMER_DAY == 0.13569
    assert c.CHANNEL_COST_PAISE == {"sms": 20, "whatsapp": 40, "voice": 400}


def test_frozen_simulation_coefficients_are_unchanged():
    """Phase 9 made these into parameters. The MODULE-LEVEL values, which every
    default argument resolves to, must not have moved — otherwise the sweep's
    anchor would silently no longer be the frozen world."""
    from sim.environment import BETA_FATIGUE, BETA_INCENTIVE, OPTOUT_BASE, OPTOUT_MAX

    assert BETA_FATIGUE == 1.0
    assert BETA_INCENTIVE == 4.0
    assert OPTOUT_BASE == 0.06
    assert OPTOUT_MAX == 0.5


def test_frozen_natural_recovery_prior_is_unchanged():
    """A Phase 7 owner prior, precommitted against the observed total-rupee
    gap. Phase 9 does not sweep it; sweeping it post hoc would be retroactive
    tuning."""
    from sim.natural import NATURAL_MULTIPLIER_BY_ROOT_CAUSE as t

    assert t == {
        "issuer_downtime": 0.40,
        "insufficient_funds": 0.35,
        "authentication_drop": 0.25,
        "unknown": 0.15,
        "mandate_expired": 0.10,
        "price_hesitation": 0.10,
        "intent_lost": 0.05,
        "disputed": 0.05,
    }


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60
    )


@pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0, reason="not a git checkout"
)
def test_no_diff_against_the_phase4_baseline_in_any_protected_path():
    result = _git("diff", "--stat", f"{PHASE4_SHA}", "HEAD", "--", *PROTECTED_PATHS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        "Phase 4 protected files differ from the frozen baseline "
        f"{PHASE4_SHA}:\n{result.stdout}"
    )


@pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0, reason="not a git checkout"
)
def test_phase9_touched_no_file_under_sampark():
    """Phase 9's whole analytical layer lives in `sim/` and `tests/`. If a
    Phase 9 change ever reaches `sampark/`, this fails and forces the question
    to be asked out loud rather than absorbed."""
    result = _git("diff", "--name-only", "9849126", "HEAD", "--", "sampark/")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        "Phase 9 modified files under sampark/, which it is not supposed to:\n" + result.stdout
    )


def test_committed_phase4_gate_evidence_is_intact():
    """The headline the whole project rests on, re-read from the committed
    file rather than recomputed."""
    import json

    gate = json.loads((_REPO_ROOT / "results" / "gate_headline.json").read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["constants_commit_sha"] == "aa87123aafdc9d812f5a01c04766c60b9198a2ce"
    assert gate["mean_a_per_contact_paise"] == pytest.approx(89387.38057)
    assert gate["mean_b_per_contact_paise"] == pytest.approx(156957.36981923878)
    assert gate["min_uplift_ratio"] == pytest.approx(1.711386778074912)
    assert gate["max_uplift_ratio"] == pytest.approx(1.8821746334249845)
    assert gate["total_a_contacts"] == 100_000
    assert gate["total_b_contacts"] == 51_542


def test_activated_policy_file_stays_empty_for_protected_evidence():
    """A compiled rule that denied candidates the frozen 11 would admit would
    change Arm B's allocation and invalidate every committed evidence file.
    Phase 7 pinned this; Phase 9 must not be the phase that breaks it."""
    from sampark.policy.compiled import compiled_hard_rules

    assert compiled_hard_rules() == ()
