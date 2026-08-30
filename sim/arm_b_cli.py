"""Arm B — OFFICIAL evidence CLI, Design Lock §13, §20 (verification step 7).

    python -m sim.arm_b_cli --seed 42
    python -m sim.arm_b_cli --seed 42 --ablation aging_zero
    python -m sim.arm_b_cli --seed 42 --ablation merchant_margin_half
    python -m sim.arm_b_cli --seed 42 --ablation fifo_under_cap
    python -m sim.arm_b_cli --seed 42 --ablation phase6_heuristic
    python -m sim.arm_b_cli --seed 42 --ablation phase6_model

This is the OFFICIAL evidence runner (Phase 4C-2, Blocker 1). It ALWAYS
runs against the real, owner-authored PostgreSQL SERIALIZABLE issuance
transaction (sampark.budget.issuance.PostgresGrantIssuer) — there is no
flag to select the in-memory backend, deliberately: an escape hatch that
doesn't exist cannot be silently taken. If Postgres is unreachable or the
Phase 4 schema additions are missing, this script FAILS LOUDLY with a
non-zero exit and a clear message — it never falls back to
InMemoryGrantIssuer, which would invalidate the evidence (Phase 4C-1
preflight, Blocker 1). `run_arm_b(..., backend="memory")` still exists
and is exactly what fast unit tests use — see sim/arm_b.py.

`--ablation` selects exactly one of the four precommitted evaluation
conditions (Design Lock §14.4, Phase 4C-2 Blocker 2) — a closed set, not
a free-form override, so no new tuning knob exists:

    headline               — frozen constants, unchanged
    aging_zero             — AGING_BONUS_PAISE = 0
    merchant_margin_half   — merchant budget x 0.5 (computed, not typed in)
    fifo_under_cap         — allocator ranking replaced by chronological FIFO,
                              and (BY DESIGN — Design Lock §14.4, Phase 4C
                              hardening W8) admission bypasses the
                              expected_net > 0 gate entirely: this ablation
                              is deliberately value-blind, isolating pure
                              chronological THROTTLING (hard caps, still
                              fully enforced) from the value-AWARE allocator
                              it is being compared against — not "headline
                              ranking with a different sort key"
    phase6_heuristic        — Phase 6's model-agnostic scorer INTERFACE
                              (sampark.allocator.scorer), explicitly
                              constructed as HeuristicScorer — same frozen
                              formula as headline, routed through the new
                              seam instead of called directly. This is the
                              regression proof AND the required "paired
                              heuristic ablation" (Phase 6 contract) in one
                              artifact: it must reproduce headline exactly.
    phase6_model            — sampark.models.scorer.build_scorer(), i.e.
                              whatever model artifact is actually committed
                              (sampark/models/artifact_data.py). On THIS
                              dataset both the uplift and fatigue-hazard
                              models report available=False (see
                              sampark/models/uplift.py, fatigue_hazard.py),
                              so build_scorer() deterministically falls
                              back to the same HeuristicScorer as
                              phase6_heuristic — this ablation is expected
                              to reproduce headline too, and that is the
                              honest Phase 6 result on this dataset, not a
                              bug in the ablation.

`constants_commit_sha` records `git rev-parse HEAD` at run time — the
precommitment device (Design Lock §13.4) that lets a reader verify with
`git log` that the Design Lock's frozen constants (sampark/allocator/
constants.py, sampark/allocator/calibrated.py) predate this result. This
is only meaningful once those files are actually committed; until then
the recorded SHA is informational only, and this script says so.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from sampark.allocator.constants import AGING_BONUS_PAISE, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.allocator.scorer import HeuristicScorer
from sim.arm_b import BACKEND_POSTGRES, run_arm_b
from sim.cli import build_dataset
from sim.mediation_metrics import build_contact_records, compute_compliance_metrics, scope_violation_count
from sim.metrics import compute_metrics
from sim.persistence import PostgresConfigError

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
_REPO_ROOT = Path(__file__).resolve().parent.parent

HEADLINE = "headline"
AGING_ZERO = "aging_zero"
MERCHANT_MARGIN_HALF = "merchant_margin_half"
FIFO_UNDER_CAP = "fifo_under_cap"
PHASE6_HEURISTIC = "phase6_heuristic"
PHASE6_MODEL = "phase6_model"
PHASE7_HEURISTIC = "phase7_heuristic"
PHASE7_MODEL = "phase7_model"
PHASE7_MODEL_UPLIFT = "phase7_model_uplift"
ABLATIONS = (
    HEADLINE, AGING_ZERO, MERCHANT_MARGIN_HALF, FIFO_UNDER_CAP, PHASE6_HEURISTIC, PHASE6_MODEL,
    PHASE7_HEURISTIC, PHASE7_MODEL, PHASE7_MODEL_UPLIFT,
)

# W8: a short, self-contained note stamped into EVERY result file under
# "ablation_note" — a reader of just the JSON (not this module's
# docstring) must be able to see, without cross-referencing source, that
# fifo_under_cap is deliberately value-blind rather than assuming its
# admission semantics match every other ablation's.
_ABLATION_NOTES: dict[str, str] = {
    HEADLINE: "Frozen constants, unchanged.",
    AGING_ZERO: "AGING_BONUS_PAISE = 0; admission rule (expected_net > 0) unchanged.",
    MERCHANT_MARGIN_HALF: "Merchant margin budget halved; admission rule (expected_net > 0) unchanged.",
    FIFO_UNDER_CAP: (
        "Ranking replaced by chronological FIFO, and admission BYPASSES expected_net > 0 "
        "entirely (deliberate — Design Lock §14.4): isolates pure chronological throttling "
        "(hard caps, still fully enforced) from value-aware allocation."
    ),
    PHASE6_HEURISTIC: (
        "Phase 6 scorer interface (sampark.allocator.scorer), explicitly constructed as "
        "HeuristicScorer -- the same frozen formula as headline, routed through the new "
        "model-agnostic seam. Must reproduce headline exactly; this IS the regression proof."
    ),
    PHASE6_MODEL: (
        "sampark.models.scorer.build_scorer() against the committed model artifact "
        "(sampark/models/artifact_data.py). Both uplift and fatigue-hazard models report "
        "available=False on this dataset (no treatment/control split; no opt-out labels -- "
        "see sampark/models/uplift.py, fatigue_hazard.py), so this deterministically falls "
        "back to HeuristicScorer and reproduces headline too. That is the honest Phase 6 "
        "result on this dataset, not a bug in the ablation."
    ),
    PHASE7_HEURISTIC: (
        "Phase 7's paired heuristic ablation, mirroring phase6_heuristic exactly: "
        "explicitly constructed as HeuristicScorer, the same frozen formula as headline, "
        "run through the SAME sim.arm_b.run_arm_b (no holdout -- this ablation tests the "
        "SCORER seam only). Must reproduce headline exactly."
    ),
    PHASE7_MODEL: (
        "sampark.models.scorer.build_scorer(module_name='sampark.models.artifact_data_phase7', "
        "p_hat_mode='level') against the Phase 7 committed artifact (sim/train_phase7_models.py, "
        "seed 42, holdout fraction 0.10). On this dataset uplift reports available=False "
        "(most (source, root_cause) buckets fall below the 200-observation floor in the "
        "holdout control arm) even though fatigue-hazard reports available=True -- the "
        "all-or-nothing gate (both required) falls back to HeuristicScorer, reproducing "
        "headline exactly. This is the honest Phase 7 result, not a bug in the ablation. "
        "NOTE: this ablation runs the STANDARD (non-holdout) sim.arm_b.run_arm_b -- it "
        "tests whether the COMMITTED MODEL (trained separately, on Arm A-H) changes "
        "SCORING behavior when applied to headline candidates; it does not itself "
        "introduce a holdout into this run."
    ),
    PHASE7_MODEL_UPLIFT: (
        "Identical to phase7_model except p_hat_mode='uplift' (Phase 7 design lock, "
        "Decision 5: p_hat = treated - control, the causally correct quantity, shipped "
        "as a SEPARATE named ablation rather than silently changing phase7_model's "
        "formula). On this dataset the uplift component is unavailable regardless of "
        "p_hat_mode, so this ablation ALSO falls back to HeuristicScorer and reproduces "
        "headline exactly, for the same reason as phase7_model."
    ),
}


def _ablation_params(ablation: str) -> dict:
    """Deterministic mapping, ablation label -> the ONE parameter it
    changes. Every other parameter stays at its frozen default —
    Design Lock §14.4: "identical code" across all four Phase 4
    conditions; the two Phase 6 additions below change only WHICH
    Scorer computes expected_net, never aging/margin/fifo."""
    if ablation == HEADLINE:
        return {}
    if ablation == AGING_ZERO:
        return {"aging_bonus_paise": 0}
    if ablation == MERCHANT_MARGIN_HALF:
        return {"merchant_budget_paise_per_window": MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2}
    if ablation == FIFO_UNDER_CAP:
        return {"fifo_mode": True}
    if ablation == PHASE6_HEURISTIC:
        return {"scorer": HeuristicScorer()}
    if ablation == PHASE6_MODEL:
        from sampark.models.scorer import build_scorer  # local: only Phase 6 ablations need sklearn

        return {"scorer": build_scorer()}
    if ablation == PHASE7_HEURISTIC:
        return {"scorer": HeuristicScorer()}
    if ablation == PHASE7_MODEL:
        from sampark.models.scorer import build_scorer  # local: only Phase 6/7 ablations need sklearn

        return {"scorer": build_scorer(module_name="sampark.models.artifact_data_phase7", p_hat_mode="level")}
    if ablation == PHASE7_MODEL_UPLIFT:
        from sampark.models.scorer import build_scorer

        return {"scorer": build_scorer(module_name="sampark.models.artifact_data_phase7", p_hat_mode="uplift")}
    raise ValueError(f"unknown ablation: {ablation!r}")  # unreachable — argparse choices= guards this


def _result_path(seed: int, ablation: str) -> Path:
    if ablation == HEADLINE:
        return _RESULTS_DIR / f"arm_b_metrics_{seed}.json"
    return _RESULTS_DIR / f"arm_b_{ablation}_metrics_{seed}.json"


def _compute_compliance(seed: int, result) -> dict:
    """Phase 4C hardening (W4): the SAME compliance predicates
    sim/gate.py's `--with-compliance` runs for Arm A, run here for Arm
    B's ACTUAL executed-contact stream (`result.outcomes`, already held
    in memory — no second Postgres run). Design Lock §13.6: one set of
    predicates, two modes — Arm A measured in observation-only mode, Arm
    B measured the same way as a corroborating check that hard-policy
    enforcement actually worked. `build_dataset(seed)` is a second,
    CHEAP, deterministic, in-memory-only call (no Postgres) — the same
    technique sim/gate.py's `_compute_arm_a_compliance` already uses for
    Arm A."""
    _population, _signals, ledger = build_dataset(seed)
    risk_items_by_id = {item.risk_id: item for item in ledger.risk_items}
    risk_items_by_customer: dict[str, list] = {}
    for item in ledger.risk_items:
        risk_items_by_customer.setdefault(ledger.risk_customer_map[item.risk_id], []).append(item)
    risk_items_by_customer = {k: tuple(v) for k, v in risk_items_by_customer.items()}

    records = build_contact_records(result.outcomes, risk_items_by_id)
    metrics = compute_compliance_metrics(records, risk_items_by_customer)
    metrics["scope_violation_count"] = scope_violation_count(result.decisions)
    return metrics


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SAMPARK Phase 4 Arm B OFFICIAL evidence runner — always PostgreSQL, never falls back."
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--ablation", choices=ABLATIONS, default=HEADLINE)
    args = parser.parse_args()

    params = _ablation_params(args.ablation)
    aging_bonus = params.get("aging_bonus_paise", AGING_BONUS_PAISE)
    merchant_budget = params.get("merchant_budget_paise_per_window", MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW)
    fifo_mode = params.get("fifo_mode", False)
    scorer = params.get("scorer")  # None for every Phase 4 ablation -- byte-identical default

    print("=" * 70)
    print(f"SAMPARK Arm B OFFICIAL evidence run — backend={BACKEND_POSTGRES.upper()}")
    print(f"seed={args.seed}  ablation={args.ablation}")
    print(f"aging_bonus_paise={aging_bonus}  merchant_budget_paise_per_window={merchant_budget}  fifo_mode={fifo_mode}")
    print(f"scorer={type(scorer).__name__ if scorer is not None else 'default (HeuristicScorer)'}")
    print(f"ablation_note: {_ABLATION_NOTES[args.ablation]}")
    print("=" * 70)

    try:
        result = run_arm_b(
            args.seed,
            aging_bonus_paise=aging_bonus,
            backend=BACKEND_POSTGRES,
            merchant_budget_paise_per_window=merchant_budget,
            fifo_mode=fifo_mode,
            scorer=scorer,
        )
    except PostgresConfigError as exc:
        print(f"FATAL: Postgres is not configured — {exc}", file=sys.stderr)
        print("This is the OFFICIAL evidence runner: it does not fall back to InMemory. Fix the environment and re-run.", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001 — any Postgres/connection failure must abort loudly, not degrade silently
        print(f"FATAL: the PostgreSQL-backed Arm B run failed — {type(exc).__name__}: {exc}", file=sys.stderr)
        print("This is the OFFICIAL evidence runner: it does not fall back to InMemory. Fix Postgres and re-run.", file=sys.stderr)
        raise SystemExit(1) from exc

    assert result.backend == BACKEND_POSTGRES  # the one invariant this script exists to guarantee

    metrics = compute_metrics(result.outcomes)
    compliance = _compute_compliance(args.seed, result)
    commit_sha = _git_commit_sha()
    metrics_with_header = {
        "arm": "B",
        "seed": args.seed,
        "ablation": args.ablation,
        "backend": result.backend,
        "aging_bonus_paise": aging_bonus,
        "merchant_budget_paise_per_window": merchant_budget,
        "fifo_mode": fifo_mode,
        "scorer": type(scorer).__name__ if scorer is not None else "HeuristicScorer",
        "constants_commit_sha": commit_sha,
        "ablation_note": _ABLATION_NOTES[args.ablation],
        "compliance": compliance,
        **metrics,
    }

    _RESULTS_DIR.mkdir(exist_ok=True)
    out_path = _result_path(args.seed, args.ablation)
    out_path.write_text(json.dumps(metrics_with_header, indent=2), encoding="utf-8")

    print(f"seed: {args.seed}")
    print(f"ablation: {args.ablation}")
    print(f"backend: {result.backend}  (PostgreSQL — confirmed, not InMemory)")
    print(f"constants_commit_sha: {commit_sha}")
    print(f"recovery_unit: {metrics['recovery_unit']}")
    print(f"total_contacts: {metrics['total_contacts']}")
    print(f"total_recoveries: {metrics['total_recoveries']}")
    print(f"recovered_amount_paise: {metrics['recovered_amount_paise']}")
    print(f"recovered_amount_per_contact_paise: {metrics['recovered_amount_per_contact_paise']}")
    print(f"incentive_spend_paise: {metrics['incentive_spend_paise']}")
    print(f"decisions_logged: {len(result.decisions)}")
    print(f"compliance: {compliance}")
    print(f"wrote: {out_path}")


if __name__ == "__main__":
    main()
