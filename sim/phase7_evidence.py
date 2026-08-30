"""Phase 7 evidence collection (spec §8.9).

    python -m sim.phase7_evidence

Writes results/phase7_*.json and results/arm_{a,b}_holdout_metrics_*.json,
results/arm_h_metrics_*.json. NEVER touches any Phase 4/6 result filename.

Honest scope note (see the Phase 7 session's final report for the full
account): Arm A-H and Arm H need no Postgres at all (pure Environment/
generator computation) and are run here for all five precommitted seeds.
Arm B-H needs the real SERIALIZABLE issuance transaction; a full
Postgres-backed run costs real wall-clock (~1-2 hours for a single
(seed, fraction) pair, measured directly in this session's own
tests/sim_arm_b_holdout/test_arm_b_holdout_postgres.py run: 1:48:01 for
two full runs). This script runs Arm B-H on the MEMORY backend for all
five seeds (mechanism/determinism evidence, fast) and records that the
Postgres-backed path was validated separately, for one seed, as a
dedicated test — not re-duplicated here for time reasons.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sim.arm_a_holdout import run_arm_a_holdout
from sim.arm_b import BACKEND_MEMORY, run_arm_b_holdout
from sim.arm_h import run_arm_h
from sim.cli import build_dataset
from sim.holdout import assign, customer_amounts_from_risk_items, membership_digest
from sim.natural import NATURAL_MODEL_VERSION, NATURAL_MULTIPLIER_BY_ROOT_CAUSE, observation_window_end, p_natural
from sim.natural_metrics import compute_natural_metrics
from sim.metrics import compute_metrics

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
_REPO_ROOT = Path(__file__).resolve().parent.parent

FINAL_SEEDS = (7, 42, 101, 2024, 31337)
FRACTIONS = (0.10, 0.20)


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _write(name: str, payload: dict) -> None:
    _RESULTS_DIR.mkdir(exist_ok=True)
    path = _RESULTS_DIR / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"wrote: {path}")


def decision_17_precommitment(seed: int, fraction: float) -> dict:
    """A TRUE closed-form prediction for Arm A-H specifically (its
    uncontacted set is exactly the held-out customers — knowable without
    running any natural draw), computed from ALREADY-COMMITTED inputs
    only (sim.natural's multiplier table, Population.hidden_response,
    the generator's own risk items) — never from a real natural-recovery
    outcome."""
    from sim.environment import _profile_by_customer

    population, signals, ledger = build_dataset(seed)
    profile_by_customer = _profile_by_customer(population, signals, ledger)
    customer_amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    held_out = assign(seed, fraction, customer_amounts)

    predicted_paise = 0.0
    n_items = 0
    for item in ledger.risk_items:
        cid = ledger.risk_customer_map[item.risk_id]
        if cid in held_out:
            profile = profile_by_customer[cid]
            predicted_paise += p_natural(profile, item.root_cause) * item.amount_paise
            n_items += 1

    return {
        "seed": seed, "fraction": fraction, "arm": "A-H",
        "predicted_natural_recovered_paise": round(predicted_paise),
        "n_uncontacted_items": n_items,
        "natural_model_version": NATURAL_MODEL_VERSION,
        "multiplier_table": NATURAL_MULTIPLIER_BY_ROOT_CAUSE,
    }


def run_arm_a_holdout_evidence(seed: int, fraction: float) -> dict:
    result = run_arm_a_holdout(seed, fraction)
    contact_metrics = compute_metrics(result.contact_outcomes)
    natural_metrics = compute_natural_metrics(result.natural_outcomes)
    n_optout = sum(1 for o in result.contact_outcomes if o.opt_out)

    payload = {
        "arm": "A-H", "seed": seed, "fraction": fraction,
        "assignment_version": 1,
        "holdout_customer_count": len(result.holdout_customer_ids),
        "holdout_customer_set_sha256": result.holdout_customer_set_sha256,
        "constants_commit_sha": _git_commit_sha(),
        "opted_out_count": n_optout,
        **contact_metrics,
        "natural": natural_metrics,
        "total_recovered_amount_paise": contact_metrics["recovered_amount_paise"] + natural_metrics["natural_recovered_amount_paise"],
    }
    _write(f"arm_a_holdout_metrics_{seed}_f{int(fraction*100)}.json", payload)
    return payload


def run_arm_h_evidence(seed: int) -> dict:
    result = run_arm_h(seed)
    natural_metrics = compute_natural_metrics(result.natural_outcomes)
    payload = {
        "arm": "H", "seed": seed,
        "constants_commit_sha": _git_commit_sha(),
        **natural_metrics,
    }
    _write(f"arm_h_metrics_{seed}.json", payload)
    return payload


def run_arm_b_holdout_evidence_memory(seed: int, fraction: float) -> dict:
    result = run_arm_b_holdout(seed=seed, fraction=fraction, backend=BACKEND_MEMORY)
    contact_metrics = compute_metrics(result.contact_outcomes)
    natural_metrics = compute_natural_metrics(result.natural_outcomes)
    n_optout = sum(1 for o in result.contact_outcomes if o.opt_out)

    payload = {
        "arm": "B-H", "seed": seed, "fraction": fraction, "backend": "memory",
        "backend_note": (
            "Memory backend: proves the mechanism (holdout filtering, natural recovery for "
            "every uncontacted item, opt-out labels) and its determinism. Opt-out ENFORCEMENT "
            "(cross-window denial) is Postgres-only (InMemoryMediationLedger.optouts_by_channel "
            "is a frozen {} stub) -- validated separately, for seed 42 at this same fraction, "
            "in tests/sim_arm_b_holdout/test_arm_b_holdout_postgres.py (real SERIALIZABLE "
            "issuance, 1:48:01 for two full runs, all 4 assertions passed)."
        ),
        "assignment_version": 1,
        "holdout_customer_count": len(result.holdout_customer_ids),
        "holdout_customer_set_sha256": result.holdout_customer_set_sha256,
        "constants_commit_sha": _git_commit_sha(),
        "opted_out_count": n_optout,
        **contact_metrics,
        "natural": natural_metrics,
        "total_recovered_amount_paise": contact_metrics["recovered_amount_paise"] + natural_metrics["natural_recovered_amount_paise"],
    }
    _write(f"arm_b_holdout_metrics_{seed}_f{int(fraction*100)}_memory.json", payload)
    return payload


def main() -> None:
    # Decision 17: precommitted BEFORE the evidence loop below writes
    # any world-v2 result — computed purely from committed constants.
    precommit_seed42 = decision_17_precommitment(42, 0.10)
    _write("phase7_decision17_precommitment_seed42_f10.json", precommit_seed42)
    print(f"Decision-17 precommitment (seed 42, f=0.10): predicted "
          f"{precommit_seed42['predicted_natural_recovered_paise']} paise over "
          f"{precommit_seed42['n_uncontacted_items']} uncontacted items")

    for seed in FINAL_SEEDS:
        run_arm_h_evidence(seed)
        for fraction in FRACTIONS:
            run_arm_a_holdout_evidence(seed, fraction)

    # Arm B-H: memory backend for all 5 seeds at the headline fraction
    # (fast, mechanism/determinism evidence); f=0.20 and the real
    # Postgres backend are exercised for seed 42 only, given the
    # ~1-2-hour-per-run cost measured directly in this session.
    for seed in FINAL_SEEDS:
        run_arm_b_holdout_evidence_memory(seed, 0.10)
    run_arm_b_holdout_evidence_memory(42, 0.20)

    print("Phase 7 evidence collection complete.")


if __name__ == "__main__":
    main()
