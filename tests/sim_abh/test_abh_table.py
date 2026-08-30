"""A/B/H table — Phase 9B.

These tests assert INVARIANTS of the aggregation, not the aggregation's own
output. The distinction matters: a test that hard-codes "total_b_contacts ==
46377" passes whenever the code is self-consistent, including when it is
self-consistently wrong. Every test below either cross-checks the table against
the raw committed evidence it claims to summarise, or asserts a property that
must hold for any correct implementation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim.abh_table import (
    CrossFamilyDeltaError,
    MissingEvidenceError,
    _assert_no_cross_family_delta,
    all_holdout_validity,
    build_abh_row,
    build_table,
    load_arm_a_holdout,
    load_arm_b_holdout,
    load_arm_h,
    mechanism_decomposition,
    model_availability_block,
)

_RESULTS = Path(__file__).resolve().parents[2] / "results"
SEEDS_F10 = (7, 42, 101, 2024, 31337)


@pytest.fixture(scope="module")
def table():
    return build_table()


# --- the aggregation must equal the raw evidence it summarises --------------


def test_aggregate_contacts_equal_the_sum_of_the_raw_committed_files(table):
    expected_a = sum(load_arm_a_holdout(s, 0.10)["total_contacts"] for s in SEEDS_F10)
    expected_b = sum(load_arm_b_holdout(s, 0.10)["total_contacts"] for s in SEEDS_F10)
    assert table["aggregate_f10"]["total_a_contacts"] == expected_a
    assert table["aggregate_f10"]["total_b_contacts"] == expected_b


def test_aggregate_recovery_equals_the_sum_of_the_raw_committed_files(table):
    expected_a = sum(load_arm_a_holdout(s, 0.10)["total_recovered_amount_paise"] for s in SEEDS_F10)
    expected_b = sum(load_arm_b_holdout(s, 0.10)["total_recovered_amount_paise"] for s in SEEDS_F10)
    assert table["aggregate_f10"]["total_a_recovered_paise"] == expected_a
    assert table["aggregate_f10"]["total_b_recovered_paise"] == expected_b


def test_each_arms_total_equals_contacted_plus_natural(table):
    """The total-recovery identity, per arm, per seed. This is the arithmetic
    that makes the world-v2 arms comparable at all."""
    for row in table["per_seed_f10"]:
        for arm in ("A-H", "B-H"):
            a = row["arms"][arm]
            assert (
                a["contacted_recovered_paise"]["value"] + a["natural_recovered_paise"]["value"]
                == a["total_recovered_paise"]["value"]
            ), f"seed {row['seed']} arm {arm}"


def test_per_contact_is_contacted_recovery_over_contacts(table):
    for row in table["per_seed_f10"]:
        for arm in ("A-H", "B-H"):
            a = row["arms"][arm]
            expected = a["contacted_recovered_paise"]["value"] / a["contacts"]["value"]
            assert a["per_contact_paise"]["value"] == pytest.approx(expected, rel=1e-12)


# --- A, B and H labels cannot be silently swapped ---------------------------


def test_arms_carry_their_own_arm_field_from_the_underlying_file():
    """If the loaders were wired to the wrong files, these `arm` fields would
    disagree. This is the cheapest possible guard against a swapped label."""
    assert load_arm_a_holdout(42, 0.10)["arm"] == "A-H"
    assert load_arm_b_holdout(42, 0.10)["arm"] == "B-H"
    assert load_arm_h(42)["arm"] == "H"


def test_h_sends_zero_contacts(table):
    """Arm H is defined by sending nothing. If it ever reports a contact, it is
    not Arm H."""
    assert table["headline"]["arms"]["H"]["contacts"]["value"] == 0


def test_mediated_arm_sends_strictly_fewer_contacts_than_unmediated(table):
    """Directional invariant: mediation can only deny or defer, never create a
    contact. A table where B-H sent more than A-H would mean the arms were
    transposed."""
    for row in table["per_seed_f10"]:
        assert row["arms"]["B-H"]["contacts"]["value"] < row["arms"]["A-H"]["contacts"]["value"]


def test_both_arms_share_the_identical_holdout_set(table):
    """The arms are only comparable because they hold out the SAME customers.
    build_abh_row raises if they do not; this asserts it actually holds."""
    for row in table["per_seed_f10"]:
        a = load_arm_a_holdout(row["seed"], 0.10)
        b = load_arm_b_holdout(row["seed"], 0.10)
        assert a["holdout_customer_set_sha256"] == b["holdout_customer_set_sha256"]
        assert row["holdout_customer_set_sha256"] == a["holdout_customer_set_sha256"]


def test_mismatched_holdout_sets_are_refused(monkeypatch):
    import sim.abh_table as m

    real = m.load_arm_b_holdout

    def poisoned(seed, fraction):
        payload = dict(real(seed, fraction))
        payload["holdout_customer_set_sha256"] = "0" * 64
        return payload

    monkeypatch.setattr(m, "load_arm_b_holdout", poisoned)
    with pytest.raises(MissingEvidenceError, match="DIFFERENT holdout"):
        m.build_abh_row(42, 0.10)


# --- the two experimental families must never be differenced ---------------


def test_cross_family_delta_is_refused():
    _assert_no_cross_family_delta("v2", "v2", "within-family delta")  # must not raise
    with pytest.raises(CrossFamilyDeltaError):
        _assert_no_cross_family_delta("v1", "v2", "total recovered")


def test_world_v1_gate_is_reported_but_not_differenced(table):
    block = table["world_v1_gate_reported_beside_not_differenced"]
    assert block["world"] == "v1"
    assert "NOT differenced" in block["note"]
    assert table["headline"]["world"] == "v2"
    # No delta key anywhere in the v1 block.
    assert not any("delta" in k or "pct" in k for k in block)


# --- provenance labelling ---------------------------------------------------


def test_every_headline_metric_carries_a_provenance_label(table):
    valid = {"observed", "estimated", "attributed", "ground_truth", "interval"}
    for arm_name, arm in table["headline"]["arms"].items():
        for key, value in arm.items():
            if isinstance(value, dict) and "value" in value:
                assert value.get("provenance") in valid, f"{arm_name}.{key} has no provenance"


def test_arm_h_is_labelled_ground_truth_and_the_holdout_is_labelled_estimated(table):
    v = table["headline"]["holdout_validity"]
    assert v["holdout_estimate"]["provenance"] == "estimated"
    assert v["arm_h_ground_truth"]["provenance"] == "ground_truth"


def test_arm_b_natural_pool_carries_the_selection_bias_warning(table):
    """Arm B-H's uncontacted pool mixes the randomized holdout with
    allocator-declined items. It is the single most misreadable number in the
    table and must never appear unlabelled."""
    warning = table["headline"]["arms"]["B-H"]["natural_recovered_paise"]["WARNING"]
    assert "NOT a natural-recovery baseline" in warning
    assert "allocator-declined" in warning


def test_arm_b_natural_pool_is_strictly_larger_than_the_randomized_holdout(table):
    """The mixture claim, verified numerically rather than asserted in prose."""
    for row in table["per_seed_f10"]:
        randomized = row["arms"]["A-H"]["natural_recovered_paise"]["n_items"]
        mixed = row["arms"]["B-H"]["natural_recovered_paise"]["n_items"]
        assert mixed > randomized, f"seed {row['seed']}"


# --- attribution arithmetic -------------------------------------------------


def test_credited_recovery_identity_holds(table):
    att = table["attribution"]
    assert (
        att["total_observed_recovered_paise"]["value"] - att["total_expected_natural_paise"]["value"]
        == att["total_credited_recovery_paise"]["value"]
    )
    assert att["aggregate_identity_holds"] is True
    assert att["arithmetic_check_passes"] is True


def test_negative_credits_are_present_and_not_clamped(table):
    """If credits were ever clamped at zero this count would be zero and the
    aggregate would be biased upward by exactly the tail."""
    att = table["attribution"]
    assert att["n_negative_credits"]["value"] > 0
    assert att["negative_tail_paise"]["value"] < 0


# --- model availability must stay honest ------------------------------------


def test_uplift_is_reported_unavailable_and_model_contribution_is_zero():
    block = model_availability_block()
    assert block["uplift_model"]["implemented"] is True
    assert block["uplift_model"]["available_on_this_dataset"] is False
    assert block["scorer_actually_used_in_every_committed_run"] == "HeuristicScorer"
    assert block["measured_model_contribution_to_headline"]["value_pct"] == 0.0


def test_model_contribution_zero_is_backed_by_identical_committed_gates():
    """The zero is not a claim — it is what the committed evidence says."""
    heuristic = json.loads((_RESULTS / "gate_phase6_heuristic.json").read_text(encoding="utf-8"))
    model = json.loads((_RESULTS / "gate_phase6_model.json").read_text(encoding="utf-8"))
    for key in ("mean_b_per_contact_paise", "total_b_contacts", "total_b_recovered_paise"):
        assert heuristic[key] == model[key], key


# --- holdout validity extension --------------------------------------------


def test_holdout_validity_covers_every_seed_and_fraction():
    v = all_holdout_validity()
    assert v["n_cells"] == 10
    seen = {(c["seed"], c["fraction"]) for c in v["cells"]}
    assert seen == {(s, f) for s in SEEDS_F10 for f in (0.10, 0.20)}


def test_containment_count_matches_the_cells_it_summarises():
    v = all_holdout_validity()
    recomputed = sum(1 for c in v["cells"] if c["arm_h_within_holdout_ci"])
    assert v["n_cells_ground_truth_inside_ci"] == recomputed
    assert v["containment_rate"] == pytest.approx(recomputed / v["n_cells"])


def test_f20_holdout_is_larger_than_f10_for_every_seed():
    """A structural property of the holdout assignment: doubling the fraction
    must not shrink the held-out population."""
    v = {(c["seed"], c["fraction"]): c for c in all_holdout_validity()["cells"]}
    for seed in SEEDS_F10:
        assert v[(seed, 0.20)]["holdout_estimate"]["n"] > v[(seed, 0.10)]["holdout_estimate"]["n"]


# --- mechanism decomposition ------------------------------------------------


def test_mechanism_decomposition_is_anchored_on_the_committed_gate():
    decomp = mechanism_decomposition()
    gate = json.loads((_RESULTS / "gate_headline.json").read_text(encoding="utf-8"))
    baseline = next(r for r in decomp["rows"] if r["configuration"] == "Arm A (unmediated)")
    headline = next(r for r in decomp["rows"] if r["configuration"] == "headline")
    assert baseline["mean_per_contact_paise"] == gate["mean_a_per_contact_paise"]
    assert headline["mean_per_contact_paise"] == gate["mean_b_per_contact_paise"]
    assert headline["uplift_vs_a"] == pytest.approx(
        gate["mean_b_per_contact_paise"] / gate["mean_a_per_contact_paise"]
    )


def test_ranking_contributes_more_than_caps_alone():
    """The decomposition's headline claim, asserted as an ordering rather than
    a magnitude so it cannot silently invert."""
    decomp = mechanism_decomposition()
    by_name = {r["configuration"]: r for r in decomp["rows"]}
    fifo = by_name["fifo_under_cap"]["uplift_vs_a"]
    headline = by_name["headline"]["uplift_vs_a"]
    assert 1.0 < fifo < headline


def test_missing_evidence_raises_rather_than_defaulting():
    import sim.abh_table as m

    with pytest.raises(MissingEvidenceError):
        m._load("this_file_does_not_exist_phase9.json")
