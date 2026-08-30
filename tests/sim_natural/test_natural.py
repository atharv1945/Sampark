"""sim.natural — natural-recovery process, Phase 7 (spec §8.9, world v2)."""

from __future__ import annotations

import pytest

from sampark.rootcause import load_taxonomy
from sim.natural import (
    NATURAL_MODEL_VERSION,
    NATURAL_MULTIPLIER_BY_ROOT_CAUSE,
    InvalidMultiplierTableError,
    _validate_multiplier_table,
    p_natural,
)
from sim.population import HiddenResponseProfile


def _profile(conversion_propensity: float) -> HiddenResponseProfile:
    return HiddenResponseProfile(
        person_id="p", conversion_propensity=conversion_propensity, fatigue_hazard=0.2, price_sensitivity=0.5
    )


def test_committed_table_covers_exactly_the_taxonomy_domain():
    taxonomy_values = set(load_taxonomy().taxonomy)
    assert set(NATURAL_MULTIPLIER_BY_ROOT_CAUSE.keys()) == taxonomy_values


def test_committed_table_passes_its_own_validator():
    _validate_multiplier_table(NATURAL_MULTIPLIER_BY_ROOT_CAUSE)  # must not raise


def test_every_multiplier_below_one():
    """Contacting must never lower recovery probability in this world."""
    assert all(v < 1.0 for v in NATURAL_MULTIPLIER_BY_ROOT_CAUSE.values())


def test_every_multiplier_in_locked_range():
    assert all(0.05 <= v <= 0.40 for v in NATURAL_MULTIPLIER_BY_ROOT_CAUSE.values())


def test_locked_ordering_holds():
    """Non-strict between tiers (matches _validate_multiplier_table's own
    `<` check, which permits a tied floor — the committed table's tier 3
    (price_hesitation/intent_lost/mandate_expired) and tier 4 (disputed)
    both bottom out at the locked floor 0.05, tied on purpose)."""
    t = NATURAL_MULTIPLIER_BY_ROOT_CAUSE
    assert min(t["issuer_downtime"], t["insufficient_funds"]) > t["authentication_drop"]
    assert t["authentication_drop"] > max(t["price_hesitation"], t["intent_lost"], t["mandate_expired"])
    assert min(t["price_hesitation"], t["intent_lost"], t["mandate_expired"]) >= t["disputed"]


def test_unknown_is_strictly_interior():
    t = NATURAL_MULTIPLIER_BY_ROOT_CAUSE
    mapped = [v for k, v in t.items() if k != "unknown"]
    assert min(mapped) < t["unknown"] < max(mapped)


def test_implied_global_rate_below_first_contact_p_base_mean():
    """The substantive claim: a contact is worth meaningfully more than the
    do-nothing baseline. P_BASE_MEAN is the calibrated first-contact rate
    (sampark/allocator/calibrated.py); the natural rate at the SAME
    conversion_propensity must sit well below it for every root cause."""
    from sampark.allocator.calibrated import P_BASE_MEAN

    profile = _profile(P_BASE_MEAN)
    for cause in NATURAL_MULTIPLIER_BY_ROOT_CAUSE:
        assert p_natural(profile, cause) < P_BASE_MEAN


def test_p_natural_is_deterministic():
    profile = _profile(0.3)
    a = p_natural(profile, "insufficient_funds")
    b = p_natural(profile, "insufficient_funds")
    assert a == b


def test_p_natural_monotone_in_conversion_propensity():
    low = p_natural(_profile(0.1), "insufficient_funds")
    high = p_natural(_profile(0.9), "insufficient_funds")
    assert high > low


def test_p_natural_unknown_root_cause_raises():
    with pytest.raises(KeyError):
        p_natural(_profile(0.3), "not_a_real_root_cause")


def test_p_natural_clips_to_open_unit_interval():
    profile = _profile(1.0)
    for cause in NATURAL_MULTIPLIER_BY_ROOT_CAUSE:
        value = p_natural(profile, cause)
        assert 0.0 < value < 1.0


# --- validator negative cases (constructed tables, never mutating the committed one) ---


def _valid_base() -> dict[str, float]:
    return dict(NATURAL_MULTIPLIER_BY_ROOT_CAUSE)


def test_validator_rejects_missing_key():
    table = _valid_base()
    del table["disputed"]
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_validator_rejects_extra_key():
    table = _valid_base()
    table["not_a_taxonomy_value"] = 0.2
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_validator_rejects_out_of_range_low():
    table = _valid_base()
    table["disputed"] = 0.01
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_validator_rejects_out_of_range_high():
    table = _valid_base()
    table["issuer_downtime"] = 0.99
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_validator_rejects_value_at_or_above_one():
    table = _valid_base()
    table["issuer_downtime"] = 1.0
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_validator_rejects_ordering_violation():
    table = _valid_base()
    table["disputed"] = 0.39  # now exceeds the transient-failure tier
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_validator_rejects_unknown_at_maximum():
    table = _valid_base()
    table["unknown"] = table["issuer_downtime"]
    with pytest.raises(InvalidMultiplierTableError):
        _validate_multiplier_table(table)


def test_module_version_is_committed_int():
    assert isinstance(NATURAL_MODEL_VERSION, int)
    assert NATURAL_MODEL_VERSION >= 1


def test_no_rng_import_in_module():
    import ast
    import inspect

    import sim.natural as natural_module

    tree = ast.parse(inspect.getsource(natural_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert "random" not in imported_names
    assert "numpy" not in imported_names
