"""sim/gate.py knows how to read back Phase 6's two new ablations --
same synthetic-fixture technique as tests/arm_b/test_gate.py, not the
real five-seed run.
"""

from __future__ import annotations

import json

from sim.gate import load_seed_row

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


def _seed_a(tmp_path, seed, contacts, recovered_paise):
    _write(
        tmp_path / f"arm_a_metrics_{seed}.json",
        arm="A", seed=seed, total_contacts=contacts, total_recoveries=1,
        recovered_amount_paise=recovered_paise, incentive_spend_paise=0,
        recovered_amount_per_contact_paise=recovered_paise / contacts,
    )


def _seed_b(tmp_path, seed, contacts, recovered_paise, filename, ablation):
    _write(
        tmp_path / filename,
        arm="B", seed=seed, total_contacts=contacts, total_recoveries=1,
        recovered_amount_paise=recovered_paise, incentive_spend_paise=0,
        recovered_amount_per_contact_paise=recovered_paise / contacts,
        ablation=ablation, backend="postgres", constants_commit_sha=_DEFAULT_SHA,
        compliance=_DEFAULT_COMPLIANCE,
    )


def test_gate_reads_phase6_heuristic_ablation(tmp_path):
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, filename="arm_b_phase6_heuristic_metrics_42.json", ablation="phase6_heuristic")

    row = load_seed_row(42, ablation="phase6_heuristic", results_dir=tmp_path)
    assert row.b_recovered_paise == 150_000


def test_gate_reads_phase6_model_ablation(tmp_path):
    _seed_a(tmp_path, 42, contacts=10, recovered_paise=100_000)
    _seed_b(tmp_path, 42, contacts=10, recovered_paise=150_000, filename="arm_b_phase6_model_metrics_42.json", ablation="phase6_model")

    row = load_seed_row(42, ablation="phase6_model", results_dir=tmp_path)
    assert row.b_recovered_paise == 150_000
