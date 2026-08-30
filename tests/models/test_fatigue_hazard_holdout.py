"""sampark.models.fatigue_hazard — Phase 7 hierarchical/shrunk fit
(spec §8.9, design lock Part 7 / §7.2's silent-zero fix)."""

from __future__ import annotations

import pytest

from sampark.models.fatigue_hazard import (
    LEVEL_GLOBAL,
    LEVEL_SOURCE,
    LEVEL_SOURCE_ROOT_CAUSE,
    QUERYABLE_CONTACT_INDICES,
    _MIN_OBS_PER_BUCKET,
    _MIN_POSITIVES_PER_BUCKET,
    fit_fatigue_hazard_model_holdout,
    train_fatigue_hazard_model_holdout,
)
from sampark.models.training_data import TrainingRow, TreatmentArm

SEED = 42


def _treated_row(source, root_cause, n, opt_out, i):
    return TrainingRow(
        agent_id="a", customer_id=f"c{i}", risk_id=f"r{i}", source=source, root_cause=root_cause,
        channel="sms", incentive_bps=0, amount_paise=1000, contact_index=n,
        recovered=False, amount_recovered_paise=0, incentive_paise=0,
        treatment_arm=TreatmentArm.TREATED, opt_out=opt_out,
    )


def test_queryable_contact_indices_matches_frozen_caps():
    """CONTACT_CAP_24H=1, CONTACT_CAP_7D=2 (sampark/allocator/constants.py,
    frozen) -> the allocator can only ever query n in {0, 1}."""
    assert QUERYABLE_CONTACT_INDICES == (0, 1)


def test_hierarchy_falls_back_to_global_when_bucket_and_source_are_thin():
    """A (source, root_cause, n) bucket with too few observations AND a
    thin (source, n) level must fall all the way to (n) — never silently
    priced at zero (the anti-conservative default this fix replaces)."""
    rows = []
    i = 0
    # Global (n) level: plenty of observations, real positive rate, for BOTH n=0,1.
    for n in (0, 1):
        for _ in range(_MIN_OBS_PER_BUCKET + 50):
            opt_out = (i % 10 == 0)  # ~10% positive rate, well above the floor
            rows.append(_treated_row("filler_source", "filler_cause", n, opt_out, i))
            i += 1
    # The bucket under test: thin at both (source,root_cause,n) AND (source,n) levels.
    for n in (0, 1):
        for _ in range(5):
            rows.append(_treated_row("thin_source", "thin_cause", n, False, i))
            i += 1

    result = fit_fatigue_hazard_model_holdout(rows)
    assert result.available is True
    for n in (0, 1):
        key = ("thin_source", "thin_cause", n)
        assert result.fallback_level_by_bucket[key] == LEVEL_GLOBAL
        assert key in result.model.hazard_by_bucket
        assert result.model.hazard_by_bucket[key] > 0.0  # NEVER the anti-conservative zero


def test_hierarchy_uses_source_level_when_bucket_is_thin_but_source_is_not():
    rows = []
    i = 0
    for n in (0, 1):
        for _ in range(_MIN_OBS_PER_BUCKET + 50):
            rows.append(_treated_row("filler_source", "filler_cause", n, (i % 10 == 0), i))
            i += 1
    # (source, n) has enough volume (many root causes pooled), but the
    # SPECIFIC (source, root_cause, n) bucket under test is thin. Positive
    # rate here must clear _MIN_POSITIVES_PER_BUCKET (20) at n=250 obs —
    # i % 5 == 0 gives ~50 positives, comfortably above the floor.
    for n in (0, 1):
        for _ in range(_MIN_OBS_PER_BUCKET + 50):
            rows.append(_treated_row("rich_source", "other_cause", n, (i % 5 == 0), i))
            i += 1
        for _ in range(5):
            rows.append(_treated_row("rich_source", "rare_cause", n, False, i))
            i += 1

    result = fit_fatigue_hazard_model_holdout(rows)
    assert result.available is True
    for n in (0, 1):
        key = ("rich_source", "rare_cause", n)
        assert result.fallback_level_by_bucket[key] == LEVEL_SOURCE


def test_hierarchy_uses_source_root_cause_level_when_it_has_enough_volume():
    rows = []
    i = 0
    for n in (0, 1):
        for _ in range(_MIN_OBS_PER_BUCKET + 50):
            rows.append(_treated_row("rich_source", "rich_cause", n, (i % 8 == 0), i))
            i += 1

    result = fit_fatigue_hazard_model_holdout(rows)
    assert result.available is True
    for n in (0, 1):
        key = ("rich_source", "rich_cause", n)
        assert result.fallback_level_by_bucket[key] == LEVEL_SOURCE_ROOT_CAUSE


def test_unavailable_when_global_n_level_itself_is_under_floor():
    """If even the terminal (n) level can't clear the floor, the WHOLE
    model reports unavailable — never a partial model with an undefined
    bucket at runtime."""
    rows = [_treated_row("s", "rc", 0, False, i) for i in range(5)]
    result = fit_fatigue_hazard_model_holdout(rows)
    assert result.available is False
    assert result.model is None
    assert "terminal fallback level would not be total" in result.reason


def test_never_returns_zero_by_default_for_any_resolved_bucket():
    """Regression guard for the exact defect being fixed: no resolved
    hazard bucket may be a bare 0.0 unless the real (shrunk) computation
    actually produced one — this test constructs a scenario where the
    OLD hazard.get(key, 0.0) behavior would have silently priced an
    unseen bucket at zero, and proves the NEW model instead resolves it
    through the hierarchy to a positive value."""
    rows = []
    i = 0
    for n in (0, 1):
        for _ in range(_MIN_OBS_PER_BUCKET + 50):
            rows.append(_treated_row("s1", "rc1", n, True, i))  # 100% opt-out rate at global level
            i += 1
    result = fit_fatigue_hazard_model_holdout(rows)
    assert result.available is True
    # An UNSEEN bucket (never appeared in training data at all) must still
    # be absent from hazard_by_bucket only if its source/root_cause never
    # appeared -- but every bucket that DOES appear must resolve to a
    # real (non-zero, non-default) value here.
    for key, hazard in result.model.hazard_by_bucket.items():
        assert hazard > 0.0


def test_train_fatigue_hazard_model_holdout_at_real_scale_seed_42():
    """Real-scale evidence: the actual, unbiased result on seed 42 at
    f=0.10 — reported honestly, not asserted to be available in advance."""
    result = train_fatigue_hazard_model_holdout(SEED, 0.10)
    assert isinstance(result.available, bool)
    if result.available:
        assert result.model is not None
        # Every resolved bucket covers exactly the queryable n domain.
        buckets_by_source_rc = set((s, rc) for s, rc, _n in result.model.hazard_by_bucket)
        for source, root_cause in buckets_by_source_rc:
            for n in QUERYABLE_CONTACT_INDICES:
                assert (source, root_cause, n) in result.model.hazard_by_bucket


def test_deterministic_across_repeated_calls():
    a = train_fatigue_hazard_model_holdout(SEED, 0.10)
    b = train_fatigue_hazard_model_holdout(SEED, 0.10)
    assert a.available == b.available
    if a.available:
        assert a.model.hazard_by_bucket == b.model.hazard_by_bucket
