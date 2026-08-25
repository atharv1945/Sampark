"""sim/gate.py — Phase 4C-2 Blocker 3.

Tests the aggregation MATH against synthetic fixture result files —
does not require the real five-seed run to exist.
"""

from __future__ import annotations

import json

import pytest

from sim.gate import FINAL_SEEDS, GateInputError, compute_gate, load_all_seed_rows, load_seed_row

_DEFAULT_SHA = "deadbeef" * 5
_DEFAULT_COMPLIANCE = {
    "quiet_hour_violations": 0,
    "contact_cap_24h_breaches": 0,
    "contact_cap_7d_breaches": 0,
    "interlock_dispute_open_violations": 0,
    "fact_unavailable_counts": {},
    "scope_violation_count": 0,
}


def _write(path, **fields):
    path.write_text(json.dumps(fields), encoding="utf-8")


def _seed_a(tmp_path, seed, contacts, recovered_paise, incentive_paise=0):
    _write(
        tmp_path / f"arm_a_metrics_{seed}.json",
        arm="A", seed=seed, total_contacts=contacts, total_recoveries=1,
        recovered_amount_paise=recovered_paise, incentive_spend_paise=incentive_paise,
        recovered_amount_per_contact_paise=recovered_paise / contacts,
    )


def _seed_b(
    tmp_path, seed, contacts, recovered_paise, incentive_paise=0, filename=None,
    ablation="headline", backend="postgres", constants_commit_sha=_DEFAULT_SHA, compliance=None,
):
    name = filename or f"arm_b_metrics_{seed}.json"
    fields = dict(
        arm="B", seed=seed, total_contacts=contacts, total_recoveries=1,
        recovered_amount_paise=recovered_paise, incentive_spend_paise=incentive_paise,
        recovered_amount_per_contact_paise=recovered_paise / contacts,
        ablation=ablation,
    )
    if backend is not None:
        fields["backend"] = backend
    if constants_commit_sha is not None:
        fields["constants_commit_sha"] = constants_commit_sha
    if compliance is not False:  # False = deliberately omit the key entirely
        fields["compliance"] = _DEFAULT_COMPLIANCE if compliance is None else compliance
    _write(tmp_path / name, **fields)


def test_final_seeds_are_exactly_the_five_precommitted_ones():
    assert FINAL_SEEDS == (7, 42, 101, 2024, 31337)


def test_gate_passes_when_mean_b_exceeds_mean_a(tmp_path):
    for seed, a_amt, b_amt in [(7, 100_000, 150_000), (42, 100_000, 150_000), (101, 100_000, 150_000), (2024, 100_000, 150_000), (31337, 100_000, 150_000)]:
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=a_amt)
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=b_amt)

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result = compute_gate(rows, "headline")

    assert result.gate_passed is True
    assert result.mean_b_per_contact_paise > result.mean_a_per_contact_paise


def test_gate_fails_when_mean_b_does_not_exceed_mean_a(tmp_path):
    for seed in FINAL_SEEDS:
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=150_000)
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=100_000)

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result = compute_gate(rows, "headline")
    assert result.gate_passed is False


def test_gate_does_not_require_every_seed_to_individually_win(tmp_path):
    """Four seeds where B wins big, one where A wins slightly — mean(B)
    must still exceed mean(A) and the gate must pass, per-seed losses
    notwithstanding (Design Lock §13.3)."""
    wins = [(7, 100_000, 200_000), (42, 100_000, 200_000), (101, 100_000, 200_000), (2024, 100_000, 200_000)]
    loss = (31337, 100_000, 90_000)
    for seed, a_amt, b_amt in wins:
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=a_amt)
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=b_amt)
    _seed_a(tmp_path, loss[0], contacts=10, recovered_paise=loss[1])
    _seed_b(tmp_path, loss[0], contacts=10, recovered_paise=loss[2])

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result = compute_gate(rows, "headline")
    assert result.gate_passed is True
    assert any(r.uplift_ratio < 1.0 for r in rows)  # the one losing seed is still present, unmasked


def test_report_includes_all_required_fields(tmp_path):
    for seed in FINAL_SEEDS:
        _seed_a(tmp_path, seed, contacts=20, recovered_paise=200_000, incentive_paise=5_000)
        _seed_b(tmp_path, seed, contacts=15, recovered_paise=250_000, incentive_paise=8_000)

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result = compute_gate(rows, "headline")

    assert len(result.rows) == 5
    for r in result.rows:
        assert r.seed in FINAL_SEEDS
        assert r.a_contacts and r.b_contacts
        assert r.a_recovered_paise and r.b_recovered_paise
        assert r.a_per_contact_paise and r.b_per_contact_paise
        assert r.uplift_ratio > 0
    assert result.mean_a_per_contact_paise > 0
    assert result.mean_b_per_contact_paise > 0
    assert result.min_uplift_ratio <= result.max_uplift_ratio
    assert result.uplift_stdev >= 0
    assert result.total_a_contacts == 100
    assert result.total_b_contacts == 75
    assert result.total_a_incentive_paise == 25_000
    assert result.total_b_incentive_paise == 40_000


def test_missing_result_file_fails_clearly(tmp_path):
    _seed_a(tmp_path, 7, contacts=10, recovered_paise=100_000)
    # arm_b_metrics_7.json deliberately never written

    with pytest.raises(GateInputError, match="missing required result file"):
        load_seed_row(7, results_dir=tmp_path)


def test_missing_one_of_five_seeds_fails_clearly(tmp_path):
    for seed in FINAL_SEEDS[:-1]:
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=100_000)
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=150_000)
    # the fifth seed's files are absent

    with pytest.raises(GateInputError):
        load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)


def test_mismatched_seed_in_file_content_fails_clearly(tmp_path):
    """The file arm_a_metrics_42.json exists but its OWN `seed` field
    says something else — must fail, not silently trust the filename."""
    _write(
        tmp_path / "arm_a_metrics_42.json",
        arm="A", seed=999, total_contacts=10, total_recoveries=1,
        recovered_amount_paise=100_000, incentive_spend_paise=0,
        recovered_amount_per_contact_paise=10_000.0,
    )
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000)

    with pytest.raises(GateInputError, match="seed mismatch"):
        load_seed_row(42, results_dir=tmp_path)


def test_wrong_arm_label_fails_clearly(tmp_path):
    """arm_a_metrics_42.json accidentally contains arm == 'B' data."""
    _write(
        tmp_path / "arm_a_metrics_42.json",
        arm="B", seed=42, total_contacts=10, total_recoveries=1,
        recovered_amount_paise=100_000, incentive_spend_paise=0,
        recovered_amount_per_contact_paise=10_000.0,
    )
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000)

    with pytest.raises(GateInputError):
        load_seed_row(42, results_dir=tmp_path)


def test_ablation_selects_the_correct_arm_b_filename(tmp_path):
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(
        tmp_path, 42, contacts=10, recovered_paise=150_000,
        filename="arm_b_aging_zero_metrics_42.json", ablation="aging_zero",
    )

    row = load_seed_row(42, ablation="aging_zero", results_dir=tmp_path)
    assert row.b_recovered_paise == 150_000

    with pytest.raises(GateInputError):
        load_seed_row(42, ablation="headline", results_dir=tmp_path)  # arm_b_metrics_42.json absent


def test_constants_commit_sha_present_and_consistent_across_seeds(tmp_path):
    sha = "abc123" * 6 + "abcd"
    for seed in FINAL_SEEDS:
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=100_000)
        path = tmp_path / f"arm_b_metrics_{seed}.json"
        _write(
            path, arm="B", seed=seed, total_contacts=10, total_recoveries=1,
            recovered_amount_paise=150_000, incentive_spend_paise=0,
            recovered_amount_per_contact_paise=15_000.0, constants_commit_sha=sha,
        )

    shas = set()
    for seed in FINAL_SEEDS:
        data = json.loads((tmp_path / f"arm_b_metrics_{seed}.json").read_text())
        assert data["constants_commit_sha"] is not None
        shas.add(data["constants_commit_sha"])
    assert len(shas) == 1, "all five seeds must have been run under the SAME committed constants"


# --- W2: result-artifact validation hardening -------------------------


def test_memory_backend_result_cannot_enter_the_gate(tmp_path):
    """The exact real-world defect this hardening closes: a stale,
    pre-Blocker-1 Arm B result file has NO 'backend' field at all (every
    file sim/arm_b_cli.py ever wrote before the Postgres-only rewrite
    looked like this). It must never be silently accepted just because
    its seed/arm fields happen to match."""
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _write(
        tmp_path / "arm_b_metrics_42.json",
        arm="B", seed=42, total_contacts=10, total_recoveries=1,
        recovered_amount_paise=150_000, incentive_spend_paise=0,
        recovered_amount_per_contact_paise=15_000.0,
        # deliberately NO "backend", "ablation", "constants_commit_sha", "compliance" —
        # this is exactly the shape of results/arm_b_metrics_42.json before Blocker 1.
    )

    with pytest.raises(GateInputError, match="backend"):
        load_seed_row(42, results_dir=tmp_path)


def test_wrong_backend_value_fails_clearly(tmp_path):
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, backend="memory")

    with pytest.raises(GateInputError, match="backend"):
        load_seed_row(42, results_dir=tmp_path)


def test_missing_ablation_field_fails_clearly(tmp_path):
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _write(
        tmp_path / "arm_b_metrics_42.json",
        arm="B", seed=42, total_contacts=10, total_recoveries=1,
        recovered_amount_paise=150_000, incentive_spend_paise=0,
        recovered_amount_per_contact_paise=15_000.0,
        backend="postgres", constants_commit_sha=_DEFAULT_SHA, compliance=_DEFAULT_COMPLIANCE,
        # no "ablation" key
    )

    with pytest.raises(GateInputError, match="ablation"):
        load_seed_row(42, ablation="headline", results_dir=tmp_path)


def test_wrong_ablation_value_fails_clearly(tmp_path):
    """A file genuinely produced under a DIFFERENT ablation (e.g.
    fifo_under_cap) must not silently satisfy a headline request even if
    the filename happened to be right."""
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, ablation="fifo_under_cap")

    with pytest.raises(GateInputError, match="ablation"):
        load_seed_row(42, ablation="headline", results_dir=tmp_path)


def test_missing_constants_commit_sha_fails_clearly(tmp_path):
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, constants_commit_sha=None)

    with pytest.raises(GateInputError, match="constants_commit_sha"):
        load_seed_row(42, results_dir=tmp_path)


def test_stale_schema_missing_metric_field_fails_clearly(tmp_path):
    """A result file missing a required metric field entirely (renamed
    or dropped in some future schema change) must fail with a clear
    message, not an opaque KeyError three lines later."""
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _write(
        tmp_path / "arm_b_metrics_42.json",
        arm="B", seed=42, total_contacts=10, total_recoveries=1,
        # recovered_amount_paise deliberately omitted
        incentive_spend_paise=0, recovered_amount_per_contact_paise=15_000.0,
        backend="postgres", ablation="headline", constants_commit_sha=_DEFAULT_SHA,
        compliance=_DEFAULT_COMPLIANCE,
    )

    with pytest.raises(GateInputError, match="missing required field"):
        load_seed_row(42, results_dir=tmp_path)


def test_inconsistent_constants_commit_sha_across_seeds_fails_the_gate(tmp_path):
    """Design Lock §13.4's precommitment device is worthless if two
    seeds in the SAME gate aggregate were run under different commits —
    compute_gate must catch this, not just load_seed_row per file."""
    for i, seed in enumerate(FINAL_SEEDS):
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=100_000)
        sha = _DEFAULT_SHA if i < len(FINAL_SEEDS) - 1 else "c0ffee00" * 5
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=150_000, constants_commit_sha=sha)

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    with pytest.raises(GateInputError, match="inconsistent constants_commit_sha"):
        compute_gate(rows, "headline")


def test_consistent_constants_commit_sha_across_seeds_passes(tmp_path):
    for seed in FINAL_SEEDS:
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=100_000)
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=150_000)

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result = compute_gate(rows, "headline")
    assert result.constants_commit_sha == _DEFAULT_SHA


# --- W4: Arm B compliance metrics must be read, never invented ---------


def test_nonzero_arm_b_compliance_is_persisted_and_read_back(tmp_path):
    """(1) a nonzero compliance fixture, (2) verify it round-trips
    through load_seed_row unmodified."""
    nonzero_compliance = {
        "quiet_hour_violations": 0,  # Arm B's hard policy denies these outright — genuinely 0
        "contact_cap_24h_breaches": 0,
        "contact_cap_7d_breaches": 0,
        "interlock_dispute_open_violations": 0,
        "fact_unavailable_counts": {"fact_unavailable.consent_scope": 137, "fact_unavailable.rto_flag": 42},
        "scope_violation_count": 0,
    }
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, compliance=nonzero_compliance)

    row = load_seed_row(42, results_dir=tmp_path)
    assert row.b_compliance == nonzero_compliance
    assert row.b_compliance["fact_unavailable_counts"]["fact_unavailable.consent_scope"] == 137


def test_gate_aggregation_reads_stored_compliance_not_invented_zeros(tmp_path):
    """(3) verify gate aggregation reads it — compute_gate's
    b_compliance_rows must carry the SAME per-seed dicts load_seed_row
    read from disk, not a fabricated placeholder."""
    per_seed_compliance = {}
    for seed in FINAL_SEEDS:
        compliance = dict(_DEFAULT_COMPLIANCE, scope_violation_count=seed % 3)
        per_seed_compliance[seed] = compliance
        _seed_a(tmp_path, seed, contacts=10, recovered_paise=100_000)
        _seed_b(tmp_path, seed, contacts=10, recovered_paise=150_000, compliance=compliance)

    rows = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result = compute_gate(rows, "headline")

    assert len(result.b_compliance_rows) == 5
    for row in result.b_compliance_rows:
        assert row["compliance"] == per_seed_compliance[row["seed"]]


def test_missing_compliance_field_fails_rather_than_defaulting_to_zero(tmp_path):
    """(4) verify missing compliance fields fail rather than default to
    zero — the exact bug this hardening closes: `sim/gate.py` used to
    PRINT "0 by hard-policy construction" without ever having measured
    anything. A result file with no "compliance" key must now be
    rejected, not silently treated as all-zeros."""
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, compliance=False)  # omit the key entirely

    with pytest.raises(GateInputError, match="compliance"):
        load_seed_row(42, results_dir=tmp_path)


def test_repeated_gate_computation_is_identical(tmp_path):
    for seed in FINAL_SEEDS:
        _seed_a(tmp_path, seed, contacts=17, recovered_paise=173_291)
        _seed_b(tmp_path, seed, contacts=13, recovered_paise=219_007)

    rows1 = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    rows2 = load_all_seed_rows(FINAL_SEEDS, results_dir=tmp_path)
    result1 = compute_gate(rows1, "headline")
    result2 = compute_gate(rows2, "headline")

    assert result1 == result2
