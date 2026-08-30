"""Five-seed evidence gate — Phase 4C-2, Blocker 3.

    python -m sim.gate
    python -m sim.gate --ablation aging_zero
    python -m sim.gate --with-compliance

Reads the precommitted seeds' Arm A / Arm B result files from `results/`
and computes the real, boolean gate:

    mean(B recovered_amount_per_contact_paise)
  > mean(A recovered_amount_per_contact_paise)

over EXACTLY the five precommitted seeds — 7, 42, 101, 2024, 31337,
hardcoded below, never configurable. Per-seed wins are explicitly NOT
required (Design Lock §13.3): the mean comparison is the whole gate.

This module only READS already-generated `results/*.json` files — it
does not run Arm A, Arm B, or touch PostgreSQL (except optionally, for
`--with-compliance`'s Arm A recount — see below). Running the five
seeds themselves is `sim/arm_a_cli.py` / `sim/arm_b_cli.py`'s job, not
this one's.

`--with-compliance` additionally re-runs Arm A (fast, in-memory, no
Postgres — seconds, not the ~10 minutes a Postgres-backed Arm B run
takes) to report REAL observed quiet-hour/contact-cap/interlock
violation counts for the unmediated baseline, via
sim.mediation_metrics.compute_compliance_metrics.

Arm B's corresponding compliance/fact-unavailable counts (Phase 4C
hardening, W4) are NOT re-derived here and NEVER asserted as "0 by
construction" — that claim is only honest if it was actually measured.
`sim/arm_b_cli.py` computes them once, at run time, from the
`result.outcomes` it already holds in memory (no second Postgres run),
and stamps them into every `arm_b_*_metrics_*.json` file under a
"compliance" key. This module's `load_seed_row` REQUIRES that key to be
present — a result file missing it fails loudly (GateInputError)
instead of silently defaulting to zero.
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path

FINAL_SEEDS: tuple[int, ...] = (7, 42, 101, 2024, 31337)

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"

_ABLATION_TO_ARM_B_FILENAME = {
    "headline": "arm_b_metrics_{seed}.json",
    "aging_zero": "arm_b_aging_zero_metrics_{seed}.json",
    "merchant_margin_half": "arm_b_merchant_margin_half_metrics_{seed}.json",
    "fifo_under_cap": "arm_b_fifo_under_cap_metrics_{seed}.json",
    # Phase 6: additive evidence streams, never overwriting the four
    # Phase 4 filenames above -- sim/arm_b_cli.py's _result_path already
    # writes any non-headline ablation to its own "arm_b_{ablation}_..."
    # file, so adding these two keys here costs nothing to the Phase 4
    # record and only teaches this gate how to read them back.
    "phase6_heuristic": "arm_b_phase6_heuristic_metrics_{seed}.json",
    "phase6_model": "arm_b_phase6_model_metrics_{seed}.json",
    # Phase 7: same additive pattern — new filenames only, never touching
    # the four Phase 4 entries above or the two Phase 6 entries.
    "phase7_heuristic": "arm_b_phase7_heuristic_metrics_{seed}.json",
    "phase7_model": "arm_b_phase7_model_metrics_{seed}.json",
    "phase7_model_uplift": "arm_b_phase7_model_uplift_metrics_{seed}.json",
}


class GateInputError(RuntimeError):
    """A required result file is missing, malformed, or does not match
    the seed/arm/backend/ablation it was requested for — raised instead
    of proceeding with silently wrong, stale, or mismatched data.

    Phase 4C hardening (W2): before this, `load_seed_row` checked only
    `seed` and `arm` — a memory-backend result file (no `backend` field
    at all — every file produced before Phase 4C-2's Blocker 1 CLI
    rewrite) or a result from the wrong ablation would be silently
    accepted into the evidence gate as long as its `seed`/`arm` fields
    happened to match. `sim/arm_b_cli.py` is the only writer of these
    files and it always stamps `backend`/`ablation`/`constants_commit_sha`
    — a file missing any of them did not come from the official evidence
    CLI, or predates it, and must be rejected rather than trusted on the
    strength of "the CLI will have overwritten it by now.\""""

REQUIRED_ARM_B_BACKEND = "postgres"


@dataclass(frozen=True)
class SeedRow:
    seed: int
    a_contacts: int
    b_contacts: int
    a_recovered_paise: int
    b_recovered_paise: int
    a_per_contact_paise: float
    b_per_contact_paise: float
    a_incentive_paise: int
    b_incentive_paise: int
    uplift_ratio: float  # b_per_contact / a_per_contact
    constants_commit_sha: str
    b_compliance: dict  # W4: Arm B's ENFORCED compliance, stamped by arm_b_cli.py itself — required, never defaulted


_REQUIRED_METRIC_FIELDS = (
    "total_contacts",
    "recovered_amount_paise",
    "recovered_amount_per_contact_paise",
    "incentive_spend_paise",
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise GateInputError(f"missing required result file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GateInputError(f"{path} is not valid JSON: {exc}") from exc


def _require_seed(data: dict, expected_seed: int, path: Path) -> None:
    actual = data.get("seed")
    if actual != expected_seed:
        raise GateInputError(
            f"seed mismatch in {path}: file's own seed field is {actual!r}, "
            f"but it was requested for seed {expected_seed!r}"
        )


def _require_metric_fields(data: dict, path: Path) -> None:
    """Stale/incompatible-schema guard: a result file from an older
    metrics shape (a field renamed or dropped since) must fail loudly
    here, not raise an opaque KeyError three lines later or, worse,
    silently coerce a missing field to a falsy default."""
    missing = [f for f in _REQUIRED_METRIC_FIELDS if f not in data]
    if missing:
        raise GateInputError(f"{path} is missing required field(s) {missing} — stale or incompatible result schema")


def load_seed_row(seed: int, ablation: str = "headline", results_dir: Path = _RESULTS_DIR) -> SeedRow:
    if ablation not in _ABLATION_TO_ARM_B_FILENAME:
        raise ValueError(f"unknown ablation: {ablation!r}")

    a_path = results_dir / f"arm_a_metrics_{seed}.json"
    b_path = results_dir / _ABLATION_TO_ARM_B_FILENAME[ablation].format(seed=seed)

    a_data = _load_json(a_path)
    b_data = _load_json(b_path)
    _require_seed(a_data, seed, a_path)
    _require_seed(b_data, seed, b_path)
    _require_metric_fields(a_data, a_path)
    _require_metric_fields(b_data, b_path)

    if a_data.get("arm") != "A":
        raise GateInputError(f"{a_path} does not have arm == 'A' (got {a_data.get('arm')!r})")
    if b_data.get("arm") != "B":
        raise GateInputError(f"{b_path} does not have arm == 'B' (got {b_data.get('arm')!r})")

    # W2 hardening: a result file that did not come from the OFFICIAL
    # evidence CLI (sim/arm_b_cli.py — the only writer that stamps
    # backend/ablation/constants_commit_sha) must never silently enter
    # the gate. This is what actually catches a stale memory-backend
    # seed-42 file left over from before Phase 4C-2's Blocker 1 CLI
    # rewrite: such a file has NO "backend" key at all.
    b_backend = b_data.get("backend")
    if b_backend != REQUIRED_ARM_B_BACKEND:
        raise GateInputError(
            f"{b_path}: backend must be {REQUIRED_ARM_B_BACKEND!r} for the evidence gate, got {b_backend!r} — "
            "this is not an official evidence-CLI result (memory-backend, pre-Blocker-1, or hand-edited)"
        )
    b_ablation = b_data.get("ablation")
    if b_ablation != ablation:
        raise GateInputError(
            f"{b_path}: ablation field is {b_ablation!r}, but this row was requested for ablation "
            f"{ablation!r} — refusing a mismatched or stale result file rather than trusting the filename"
        )
    commit_sha = b_data.get("constants_commit_sha")
    if not commit_sha:
        raise GateInputError(
            f"{b_path}: missing or empty constants_commit_sha — cannot verify this result was produced "
            "under the frozen, committed Phase 4 constants (Design Lock §13.4's precommitment device)"
        )

    # W4 hardening: Arm B's ENFORCED compliance/fact-unavailable metrics
    # must be present in the result file itself (arm_b_cli.py computes
    # and stamps them at run time, from result.outcomes it already
    # holds — see that module). A missing "compliance" key means this
    # file predates that change and must fail loudly, NEVER silently
    # default to "0 by construction" — that claim is only true if it was
    # actually measured.
    b_compliance = b_data.get("compliance")
    if b_compliance is None:
        raise GateInputError(
            f"{b_path}: missing 'compliance' field — Arm B compliance/fact-unavailable metrics were not "
            "stamped into this result file (stale pre-W4 result, or a hand-edited file)"
        )

    a_per_contact = a_data["recovered_amount_per_contact_paise"]
    b_per_contact = b_data["recovered_amount_per_contact_paise"]

    return SeedRow(
        seed=seed,
        a_contacts=a_data["total_contacts"],
        b_contacts=b_data["total_contacts"],
        a_recovered_paise=a_data["recovered_amount_paise"],
        b_recovered_paise=b_data["recovered_amount_paise"],
        a_per_contact_paise=a_per_contact,
        b_per_contact_paise=b_per_contact,
        a_incentive_paise=a_data["incentive_spend_paise"],
        b_incentive_paise=b_data["incentive_spend_paise"],
        uplift_ratio=(b_per_contact / a_per_contact) if a_per_contact else float("nan"),
        constants_commit_sha=commit_sha,
        b_compliance=b_compliance,
    )


def load_all_seed_rows(
    seeds: tuple[int, ...] = FINAL_SEEDS, ablation: str = "headline", results_dir: Path = _RESULTS_DIR
) -> tuple[SeedRow, ...]:
    return tuple(load_seed_row(seed, ablation, results_dir) for seed in seeds)


@dataclass(frozen=True)
class GateResult:
    ablation: str
    rows: tuple[SeedRow, ...]
    mean_a_per_contact_paise: float
    mean_b_per_contact_paise: float
    min_uplift_ratio: float
    max_uplift_ratio: float
    uplift_stdev: float
    total_a_contacts: int
    total_b_contacts: int
    total_a_recovered_paise: int
    total_b_recovered_paise: int
    total_a_incentive_paise: int
    total_b_incentive_paise: int
    constants_commit_sha: str  # the ONE sha every row was verified to share
    b_compliance_rows: tuple[dict, ...]  # [{"seed": int, "compliance": dict}, ...] — read, never invented
    gate_passed: bool  # the ONE real boolean: mean(B ₹/contact) > mean(A ₹/contact)


def _require_consistent_constants_commit_sha(rows: tuple[SeedRow, ...]) -> str:
    """W2 hardening: every seed's Arm B result feeding one gate
    aggregate must have been produced under the SAME committed Phase 4
    constants — mixing a pre-freeze run for one seed with a post-freeze
    run for another would silently corrupt the precommitment guarantee
    (Design Lock §13.4) the gate exists to make checkable."""
    shas = {r.constants_commit_sha for r in rows}
    if len(shas) > 1:
        by_seed = ", ".join(f"seed {r.seed}={r.constants_commit_sha}" for r in rows)
        raise GateInputError(
            f"inconsistent constants_commit_sha across seeds — all seeds feeding one gate aggregate "
            f"must share the SAME committed constants: {by_seed}"
        )
    return next(iter(shas))


def compute_gate(rows: tuple[SeedRow, ...], ablation: str) -> GateResult:
    constants_commit_sha = _require_consistent_constants_commit_sha(rows)
    a_values = [r.a_per_contact_paise for r in rows]
    b_values = [r.b_per_contact_paise for r in rows]
    uplifts = [r.uplift_ratio for r in rows]
    mean_a = statistics.mean(a_values)
    mean_b = statistics.mean(b_values)
    return GateResult(
        ablation=ablation,
        rows=rows,
        mean_a_per_contact_paise=mean_a,
        mean_b_per_contact_paise=mean_b,
        min_uplift_ratio=min(uplifts),
        max_uplift_ratio=max(uplifts),
        uplift_stdev=statistics.pstdev(uplifts) if len(uplifts) > 1 else 0.0,
        total_a_contacts=sum(r.a_contacts for r in rows),
        total_b_contacts=sum(r.b_contacts for r in rows),
        total_a_recovered_paise=sum(r.a_recovered_paise for r in rows),
        total_b_recovered_paise=sum(r.b_recovered_paise for r in rows),
        total_a_incentive_paise=sum(r.a_incentive_paise for r in rows),
        total_b_incentive_paise=sum(r.b_incentive_paise for r in rows),
        constants_commit_sha=constants_commit_sha,
        b_compliance_rows=tuple({"seed": r.seed, "compliance": r.b_compliance} for r in rows),
        gate_passed=mean_b > mean_a,  # the real, computed boolean — never inferred by hand
    )


def _print_report(result: GateResult, compliance: dict | None) -> None:
    print("=" * 78)
    print(f"SAMPARK Phase 4 evidence gate — ablation: {result.ablation}")
    print(f"seeds: {', '.join(str(r.seed) for r in result.rows)}")
    print("=" * 78)
    header = f"{'seed':>8} {'A contacts':>11} {'B contacts':>11} {'A ₹paise/c':>12} {'B ₹paise/c':>12} {'uplift':>8}"
    print(header)
    for r in result.rows:
        print(
            f"{r.seed:>8} {r.a_contacts:>11} {r.b_contacts:>11} "
            f"{r.a_per_contact_paise:>12.1f} {r.b_per_contact_paise:>12.1f} {r.uplift_ratio:>8.3f}"
        )
    print("-" * 78)
    print(f"mean A ₹/contact (paise): {result.mean_a_per_contact_paise:.2f}")
    print(f"mean B ₹/contact (paise): {result.mean_b_per_contact_paise:.2f}")
    print(f"min uplift: {result.min_uplift_ratio:.4f}   max uplift: {result.max_uplift_ratio:.4f}   stdev: {result.uplift_stdev:.4f}")
    print(f"total contacts:  A={result.total_a_contacts}  B={result.total_b_contacts}")
    print(f"total recovered (paise): A={result.total_a_recovered_paise}  B={result.total_b_recovered_paise}")
    print(f"total incentive spend (paise): A={result.total_a_incentive_paise}  B={result.total_b_incentive_paise}")
    print(f"constants_commit_sha (all seeds, verified identical): {result.constants_commit_sha}")
    print("-" * 78)
    print("Arm B ENFORCED compliance (read from each seed's own result file, not re-derived here):")
    for row in result.b_compliance_rows:
        print(f"  seed {row['seed']}: {row['compliance']}")
    if compliance is not None:
        print("-" * 78)
        print("Arm A observed compliance (unmediated, --with-compliance):")
        for seed, row in compliance.items():
            print(f"  seed {seed}: {row}")
    else:
        print("-" * 78)
        print("Arm A observed compliance: not computed (pass --with-compliance to recompute it)")
    print("=" * 78)
    print(f"GATE (mean B ₹/contact > mean A ₹/contact): {'PASS' if result.gate_passed else 'FAIL'}")
    print("=" * 78)


def _compute_arm_a_compliance(seeds: tuple[int, ...]) -> dict:
    from sim.arm_a import run_arm_a
    from sim.cli import build_dataset
    from sim.mediation_metrics import build_contact_records, compute_compliance_metrics

    out: dict[int, dict] = {}
    for seed in seeds:
        _population, _signals, ledger = build_dataset(seed)
        risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}
        risk_items_by_customer: dict[str, list] = {}
        for item in ledger.risk_items:
            risk_items_by_customer.setdefault(ledger.risk_customer_map[item.risk_id], []).append(item)
        risk_items_by_customer = {k: tuple(v) for k, v in risk_items_by_customer.items()}

        outcomes = run_arm_a(seed)
        records = build_contact_records(outcomes, risk_items_by_id)
        metrics = compute_compliance_metrics(records, risk_items_by_customer)
        out[seed] = {
            "quiet_hour_violations": metrics["quiet_hour_violations"],
            "contact_cap_24h_breaches": metrics["contact_cap_24h_breaches"],
            "contact_cap_7d_breaches": metrics["contact_cap_7d_breaches"],
            "conflicting_action_incidents": metrics["conflicting_action_incidents"],
            "interlock_dispute_open_violations": metrics["interlock_dispute_open_violations"],
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 4 five-seed evidence gate")
    parser.add_argument("--ablation", choices=tuple(_ABLATION_TO_ARM_B_FILENAME), default="headline")
    parser.add_argument("--with-compliance", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None, help="Also write the gate result as JSON")
    args = parser.parse_args()

    rows = load_all_seed_rows(FINAL_SEEDS, args.ablation)
    result = compute_gate(rows, args.ablation)
    compliance = _compute_arm_a_compliance(FINAL_SEEDS) if args.with_compliance else None

    _print_report(result, compliance)

    if args.json_out is not None:
        payload = {
            "ablation": result.ablation,
            "seeds": [r.seed for r in result.rows],
            "mean_a_per_contact_paise": result.mean_a_per_contact_paise,
            "mean_b_per_contact_paise": result.mean_b_per_contact_paise,
            "min_uplift_ratio": result.min_uplift_ratio,
            "max_uplift_ratio": result.max_uplift_ratio,
            "uplift_stdev": result.uplift_stdev,
            "total_a_contacts": result.total_a_contacts,
            "total_b_contacts": result.total_b_contacts,
            "total_a_recovered_paise": result.total_a_recovered_paise,
            "total_b_recovered_paise": result.total_b_recovered_paise,
            "total_a_incentive_paise": result.total_a_incentive_paise,
            "total_b_incentive_paise": result.total_b_incentive_paise,
            "constants_commit_sha": result.constants_commit_sha,
            "gate_passed": result.gate_passed,
            "per_seed": [
                {
                    "seed": r.seed, "a_contacts": r.a_contacts, "b_contacts": r.b_contacts,
                    "a_recovered_paise": r.a_recovered_paise, "b_recovered_paise": r.b_recovered_paise,
                    "a_per_contact_paise": r.a_per_contact_paise, "b_per_contact_paise": r.b_per_contact_paise,
                    "uplift_ratio": r.uplift_ratio, "b_compliance": r.b_compliance,
                }
                for r in result.rows
            ],
            "arm_a_compliance": compliance,
            "arm_b_compliance": {row["seed"]: row["compliance"] for row in result.b_compliance_rows},
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote: {args.json_out}")

    raise SystemExit(0 if result.gate_passed else 1)


if __name__ == "__main__":
    main()
