"""Arm A CLI entry point — Phase 2 exit criterion.

    python -m sim.arm_a_cli --seed 42

Runs Arm A to completion and writes results/arm_a_metrics_<seed>.json.
Generated run output is not committed (results/*.json is gitignored) —
the same pattern Phase 1 established for the generator's own dataset
output: the code is committed, the run output is not.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.arm_a import run_arm_a
from sim.metrics import compute_metrics

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def main() -> None:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 2 Arm A baseline runner")
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()

    outcomes = run_arm_a(args.seed)
    metrics = compute_metrics(outcomes)
    metrics_with_header = {"arm": "A", "seed": args.seed, **metrics}

    _RESULTS_DIR.mkdir(exist_ok=True)
    out_path = _RESULTS_DIR / f"arm_a_metrics_{args.seed}.json"
    out_path.write_text(json.dumps(metrics_with_header, indent=2), encoding="utf-8")

    print(f"seed: {args.seed}")
    print(f"recovery_unit: {metrics['recovery_unit']}")
    print(f"total_contacts: {metrics['total_contacts']}")
    print(f"total_recoveries: {metrics['total_recoveries']}")
    print(f"recovered_amount_paise: {metrics['recovered_amount_paise']}")
    print(
        "recovered_amount_per_contact_paise: "
        f"{metrics['recovered_amount_per_contact_paise']}"
    )
    print(f"incentive_spend_paise: {metrics['incentive_spend_paise']}")
    print(f"wrote: {out_path}")


if __name__ == "__main__":
    main()
