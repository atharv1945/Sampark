"""Phase 9A — sensitivity analysis (spec §11, spec §14 Round 7).

    python -m sim.sensitivity --dimension beta_fatigue
    python -m sim.sensitivity --dimension beta_incentive
    python -m sim.sensitivity --dimension all

Spec §11: *"Add a sensitivity analysis: sweep the fatigue-hazard parameter
across a plausible range and plot where SAMPARK stops winning. Publishing the
conditions under which your own system loses is the single highest-trust move
available to you."* Spec §14 Round 7 states the objection this answers: *"If
you tune the generator, you can make SAMPARK win by any margin you like."*

--- What is being varied, and what is NOT ---

This module varies GROUND-TRUTH WORLD parameters (`sim.environment`'s
`BETA_FATIGUE` / `BETA_INCENTIVE`) with the SYSTEM held completely fixed. That
is what makes it a sensitivity analysis and not an ablation:

  - sensitivity analysis  : vary the WORLD, hold the system fixed  <- this module
  - ablation              : vary the SYSTEM, hold the world fixed  <- sim/arm_b_cli.py
                            (aging_zero, fifo_under_cap, merchant_margin_half,
                             phase6_heuristic, phase6_model, phase7_*)
  - stress test           : push a SYSTEM parameter to an adversarial extreme
  - robustness check      : vary a NUISANCE parameter that should not matter
                            (seed, backend) and confirm it does not

An ablation cannot tell a reviewer whether the simulator is rigged. Only this
can. Nothing under `sampark/` is imported, read or modified here.

--- Why this sweep is a pure RE-OBSERVATION (the load-bearing property) ---

Under world v1, no realized outcome ever feeds back into a decision:

  1. `sim/arm_b.py` collects EVERY agent action up front, before the window
     loop starts (`for agent in _AGENTS: all_actions.extend(...)`).
  2. `environment.observe(...)` is called strictly AFTER `mediate_window` and
     after the grant has been issued and executed.
  3. `carried_forward = result.rescheduled_candidates` — deferral carry-forward
     is a function of the DECISION, not of the outcome.
  4. Nothing in `sim/arm_b.py` reads `outcome.recovered`; a recovered risk item
     is not removed from later windows.
  5. Scaling `beta_fatigue` by k is exactly equivalent to scaling every
     customer's `fatigue_hazard` by k. It touches no RNG stream: the population
     draw is unchanged, and `observe`'s response-model Generator makes the same
     draws in the same order. Only the threshold they are compared against moves.

Therefore every admission, ranking, grant, deferral, denial, audit event and
`prev_hash` is INVARIANT across the sweep. Arm A sends exactly 20,000 contacts
and Arm B exactly its frozen per-seed count at every point; only which contacts
SUCCEED changes. `tests/sim_sensitivity/test_decision_invariance.py` asserts
this directly rather than trusting the argument above.

Two consequences worth stating plainly:
  - The sweep needs no allocator re-run, no SERIALIZABLE issuance and no
    PostgreSQL, which is why it costs minutes rather than the ~12 hours a
    Postgres-backed grid would.
  - Because the denominator (contacts) is fixed, rupees-per-contact is a clean
    read on the response process alone.

--- Grid provenance ---

The grid, the anchor, the primary metric and six predictions were committed to
`results/phase9_precommitment.json` in commit 982c53e (eabdbd1 before the
co-author-trailer rewrite; same tree, new message), BEFORE this file existed
and before any result was observed — the same mechanism Phase 7 used for
Decision 17. `tests/sim_sensitivity/test_precommitment_binding.py` asserts the
constants below still equal that committed file, so the grid cannot drift after
results are seen.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from sim.arm_a import run_arm_a
from sim.arm_b import BACKEND_MEMORY, run_arm_b
from sim.environment import BETA_FATIGUE, BETA_INCENTIVE
from sim.metrics import compute_metrics

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRECOMMITMENT_PATH = _RESULTS_DIR / "phase9_precommitment.json"

# The five precommitted evidence seeds — identical to sim/gate.py's
# FINAL_SEEDS, deliberately re-stated rather than imported so a change to
# either one is a visible, reviewable diff in both places.
FINAL_SEEDS: tuple[int, ...] = (7, 42, 101, 2024, 31337)

DIMENSION_BETA_FATIGUE = "beta_fatigue"
DIMENSION_BETA_INCENTIVE = "beta_incentive"

# Grids, anchors: locked by results/phase9_precommitment.json.
BETA_FATIGUE_VALUES: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
BETA_INCENTIVE_VALUES: tuple[float, ...] = (2.0, 4.0, 8.0)

DIMENSIONS: dict[str, tuple[float, ...]] = {
    DIMENSION_BETA_FATIGUE: BETA_FATIGUE_VALUES,
    DIMENSION_BETA_INCENTIVE: BETA_INCENTIVE_VALUES,
}

ANCHORS: dict[str, float] = {
    DIMENSION_BETA_FATIGUE: BETA_FATIGUE,
    DIMENSION_BETA_INCENTIVE: BETA_INCENTIVE,
}


class AnchorReproductionError(RuntimeError):
    """A sweep point at the FROZEN value of its dimension failed to reproduce
    the committed Phase 4 evidence for that seed. The harness is wrong, or the
    parameterization is not actually default-preserving; either way every other
    point in the sweep is uninterpretable, so this is raised rather than
    written to a result file."""


@dataclass(frozen=True)
class SweepPoint:
    """One (dimension, value, seed) cell. Every field is measured, never
    derived from a previous run's cached number."""

    dimension: str
    value: float
    seed: int
    beta_fatigue: float
    beta_incentive: float
    a_contacts: int
    b_contacts: int
    a_recoveries: int
    b_recoveries: int
    a_recovered_paise: int
    b_recovered_paise: int
    a_incentive_paise: int
    b_incentive_paise: int
    a_per_contact_paise: float
    b_per_contact_paise: float
    uplift_ratio: float
    total_recovery_ratio: float  # B total rupees / A total rupees
    elapsed_s: float


def _betas_for(dimension: str, value: float) -> tuple[float, float]:
    """Exactly one coefficient moves per sweep point; the other stays at its
    frozen value. Varying two at once would make any observed change
    uninterpretable — the same rule sim/arm_b_cli.py's `_ablation_params`
    already enforces for the Phase 4 ablations."""
    if dimension == DIMENSION_BETA_FATIGUE:
        return value, BETA_INCENTIVE
    if dimension == DIMENSION_BETA_INCENTIVE:
        return BETA_FATIGUE, value
    raise ValueError(f"unknown sensitivity dimension: {dimension!r}")


def run_point(dimension: str, value: float, seed: int) -> SweepPoint:
    """Runs BOTH arms at one grid cell. Arm B uses the memory backend, which
    was verified to reproduce the postgres-backed committed evidence bit-for-bit
    at the anchor (tests/arm_b/test_memory_postgres_parity_at_anchor.py)."""
    beta_fatigue, beta_incentive = _betas_for(dimension, value)
    started = time.perf_counter()

    a_outcomes = run_arm_a(seed, beta_fatigue=beta_fatigue, beta_incentive=beta_incentive)
    a = compute_metrics(a_outcomes)

    b_result = run_arm_b(
        seed,
        backend=BACKEND_MEMORY,
        beta_fatigue=beta_fatigue,
        beta_incentive=beta_incentive,
    )
    b = compute_metrics(b_result.outcomes)

    elapsed = time.perf_counter() - started
    a_per = a["recovered_amount_per_contact_paise"]
    b_per = b["recovered_amount_per_contact_paise"]

    return SweepPoint(
        dimension=dimension,
        value=value,
        seed=seed,
        beta_fatigue=beta_fatigue,
        beta_incentive=beta_incentive,
        a_contacts=a["total_contacts"],
        b_contacts=b["total_contacts"],
        a_recoveries=a["total_recoveries"],
        b_recoveries=b["total_recoveries"],
        a_recovered_paise=a["recovered_amount_paise"],
        b_recovered_paise=b["recovered_amount_paise"],
        a_incentive_paise=a["incentive_spend_paise"],
        b_incentive_paise=b["incentive_spend_paise"],
        a_per_contact_paise=a_per,
        b_per_contact_paise=b_per,
        uplift_ratio=(b_per / a_per) if a_per else 0.0,
        total_recovery_ratio=(
            b["recovered_amount_paise"] / a["recovered_amount_paise"]
            if a["recovered_amount_paise"]
            else 0.0
        ),
        elapsed_s=round(elapsed, 2),
    )


def check_anchor(point: SweepPoint) -> dict:
    """At the frozen value of its dimension, a sweep point MUST reproduce the
    committed Phase 4 evidence for that seed exactly. This is the regression
    proof for the whole Phase 9 parameterization: if adding `beta_fatigue` /
    `beta_incentive` had perturbed anything, this is where it shows up."""
    frozen_a = json.loads((_RESULTS_DIR / f"arm_a_metrics_{point.seed}.json").read_text(encoding="utf-8"))
    frozen_b = json.loads((_RESULTS_DIR / f"arm_b_metrics_{point.seed}.json").read_text(encoding="utf-8"))

    checks = {
        "a_contacts": (point.a_contacts, frozen_a["total_contacts"]),
        "a_recoveries": (point.a_recoveries, frozen_a["total_recoveries"]),
        "a_recovered_paise": (point.a_recovered_paise, frozen_a["recovered_amount_paise"]),
        "a_incentive_paise": (point.a_incentive_paise, frozen_a["incentive_spend_paise"]),
        "b_contacts": (point.b_contacts, frozen_b["total_contacts"]),
        "b_recoveries": (point.b_recoveries, frozen_b["total_recoveries"]),
        "b_recovered_paise": (point.b_recovered_paise, frozen_b["recovered_amount_paise"]),
        "b_incentive_paise": (point.b_incentive_paise, frozen_b["incentive_spend_paise"]),
    }
    mismatches = {k: {"swept": got, "frozen": want} for k, (got, want) in checks.items() if got != want}
    if mismatches:
        raise AnchorReproductionError(
            f"seed {point.seed}, dimension {point.dimension} at its frozen value "
            f"{point.value} did not reproduce committed Phase 4 evidence: {mismatches!r}"
        )
    return {
        "seed": point.seed,
        "dimension": point.dimension,
        "anchor_value": point.value,
        "reproduces_committed_phase4_evidence": True,
        "compared_against": [
            f"results/arm_a_metrics_{point.seed}.json",
            f"results/arm_b_metrics_{point.seed}.json",
        ],
        "fields_compared": sorted(checks),
    }


def _git_commit_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def run_dimension(dimension: str, seeds: tuple[int, ...] = FINAL_SEEDS) -> dict:
    """Runs the full grid for one dimension across every seed, verifying the
    anchor for each seed as it goes."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"unknown sensitivity dimension: {dimension!r}")
    values = DIMENSIONS[dimension]
    anchor = ANCHORS[dimension]

    points: list[SweepPoint] = []
    anchor_checks: list[dict] = []
    for seed in seeds:
        for value in values:
            point = run_point(dimension, value, seed)
            points.append(point)
            if value == anchor:
                anchor_checks.append(check_anchor(point))
            print(
                f"  {dimension}={value:<5} seed={seed:<6} "
                f"A={point.a_per_contact_paise:>10.1f}  B={point.b_per_contact_paise:>10.1f}  "
                f"uplift={point.uplift_ratio:.4f}  ({point.elapsed_s:.0f}s)",
                flush=True,
            )

    return {
        "phase": 9,
        "dimension": dimension,
        "parameter": f"sim.environment.{dimension.upper()}",
        "frozen_value": anchor,
        "values": list(values),
        "seeds": list(seeds),
        "backend": BACKEND_MEMORY,
        "world": "v1",
        "constants_commit_sha": _git_commit_sha(),
        "precommitment": "results/phase9_precommitment.json",
        "anchor_checks": anchor_checks,
        "points": [asdict(p) for p in points],
    }


def aggregate(dimension_payload: dict) -> list[dict]:
    """Per-value aggregation over seeds, using EXACTLY sim/gate.py's gate
    definition: the mean of each arm's rupees-per-contact over the five
    precommitted seeds, and the ratio of those means. Per-seed wins are not
    required and are not computed here, matching the gate."""
    by_value: dict[float, list[dict]] = {}
    for row in dimension_payload["points"]:
        by_value.setdefault(row["value"], []).append(row)

    out = []
    for value in dimension_payload["values"]:
        rows = by_value[value]
        n = len(rows)
        mean_a = sum(r["a_per_contact_paise"] for r in rows) / n
        mean_b = sum(r["b_per_contact_paise"] for r in rows) / n
        total_a = sum(r["a_recovered_paise"] for r in rows)
        total_b = sum(r["b_recovered_paise"] for r in rows)
        ratios = [r["uplift_ratio"] for r in rows]
        out.append(
            {
                "value": value,
                "is_frozen_anchor": value == dimension_payload["frozen_value"],
                "n_seeds": n,
                "mean_a_per_contact_paise": mean_a,
                "mean_b_per_contact_paise": mean_b,
                "mean_uplift_ratio": mean_b / mean_a if mean_a else 0.0,
                "min_uplift_ratio": min(ratios),
                "max_uplift_ratio": max(ratios),
                "total_a_contacts": sum(r["a_contacts"] for r in rows),
                "total_b_contacts": sum(r["b_contacts"] for r in rows),
                "total_a_recovered_paise": total_a,
                "total_b_recovered_paise": total_b,
                "total_recovery_ratio": total_b / total_a if total_a else 0.0,
                "b_beats_a_on_per_contact": mean_b > mean_a,
            }
        )
    return out


def find_crossing(aggregated: list[dict]) -> dict:
    """The spec §11 deliverable: the value at which SAMPARK stops winning, or
    an explicit null if it does not stop winning anywhere in the tested range.
    Reported as a bracket between two tested points — never interpolated, since
    interpolating would invent a number that was not measured."""
    losing = [row for row in aggregated if not row["b_beats_a_on_per_contact"]]
    if not losing:
        return {
            "crossing_exists_in_tested_range": False,
            "crossing_bracket": None,
            "note": (
                "Arm B beats Arm A on rupees-per-contact at every tested value. "
                "No losing boundary exists inside this range; see the report's "
                "interpretation for what that implies."
            ),
        }
    ordered = sorted(aggregated, key=lambda r: r["value"])
    bracket = None
    for lower, upper in zip(ordered, ordered[1:]):
        if lower["b_beats_a_on_per_contact"] and not upper["b_beats_a_on_per_contact"]:
            bracket = [lower["value"], upper["value"]]
            break
        if not lower["b_beats_a_on_per_contact"] and upper["b_beats_a_on_per_contact"]:
            bracket = [lower["value"], upper["value"]]
            break
    return {
        "crossing_exists_in_tested_range": True,
        "crossing_bracket": bracket,
        "losing_values": [row["value"] for row in losing],
        "note": "Bracket between adjacent TESTED points; deliberately not interpolated.",
    }


def _is_monotone_non_decreasing(xs: list[float]) -> bool:
    return all(b >= a for a, b in zip(xs, xs[1:]))


def _is_monotone_non_increasing(xs: list[float]) -> bool:
    return all(b <= a for a, b in zip(xs, xs[1:]))


def evaluate_predictions(agg_fatigue: list[dict], agg_incentive: list[dict], payload_fatigue: dict) -> list[dict]:
    """Scores the six precommitted predictions. A prediction that fails is
    recorded as FAILED, with its measured value — never edited, never dropped
    (results/phase9_precommitment.json's own prohibitions)."""
    fatigue_sorted = sorted(agg_fatigue, key=lambda r: r["value"])
    uplifts = [r["mean_uplift_ratio"] for r in fatigue_sorted]
    at_zero = next(r for r in fatigue_sorted if r["value"] == 0.0)
    shortfalls = [1.0 - r["total_recovery_ratio"] for r in fatigue_sorted]

    # P5: contact counts identical across every point, per seed and per arm.
    contacts_by_seed_arm: dict[tuple[int, str], set[int]] = {}
    for row in payload_fatigue["points"]:
        contacts_by_seed_arm.setdefault((row["seed"], "A"), set()).add(row["a_contacts"])
        contacts_by_seed_arm.setdefault((row["seed"], "B"), set()).add(row["b_contacts"])
    p5_ok = all(len(v) == 1 for v in contacts_by_seed_arm.values())

    incentive_sorted = sorted(agg_incentive, key=lambda r: r["value"])
    incentive_uplifts = [r["mean_uplift_ratio"] for r in incentive_sorted]

    return [
        {
            "id": "P1",
            "claim": "mean uplift ratio is monotonically non-decreasing in beta_fatigue",
            "result": "PASS" if _is_monotone_non_decreasing(uplifts) else "FAIL",
            "measured": {"values": [r["value"] for r in fatigue_sorted], "mean_uplift_ratio": uplifts},
        },
        {
            "id": "P2",
            "claim": "mean uplift ratio at beta_fatigue = 0.0 lies in [1.30, 1.70]",
            "result": "PASS" if 1.30 <= at_zero["mean_uplift_ratio"] <= 1.70 else "FAIL",
            "measured": {"mean_uplift_ratio_at_zero": at_zero["mean_uplift_ratio"]},
        },
        {
            "id": "P3",
            "claim": "no crossing in beta_fatigue [0.0, 2.0]; Arm B never loses on rupees-per-contact",
            "result": "PASS" if all(r["b_beats_a_on_per_contact"] for r in fatigue_sorted) else "FAIL",
            "measured": find_crossing(agg_fatigue),
        },
        {
            "id": "P4",
            "claim": "Arm B recovers less TOTAL rupees at every beta_fatigue, and the shortfall shrinks monotonically as beta_fatigue rises",
            "result": (
                "PASS"
                if all(r["total_recovery_ratio"] < 1.0 for r in fatigue_sorted)
                and _is_monotone_non_increasing(shortfalls)
                else "FAIL"
            ),
            "measured": {
                "values": [r["value"] for r in fatigue_sorted],
                "total_recovery_ratio": [r["total_recovery_ratio"] for r in fatigue_sorted],
                "shortfall_fraction": shortfalls,
            },
        },
        {
            "id": "P5",
            "claim": "Arm A and Arm B contact counts are byte-identical at every tested value",
            "result": "PASS" if p5_ok else "FAIL",
            "measured": {
                f"seed_{seed}_arm_{arm}": sorted(v) for (seed, arm), v in sorted(contacts_by_seed_arm.items())
            },
        },
        {
            "id": "P6",
            "claim": "mean uplift ratio is monotonically non-increasing in beta_incentive",
            "result": "PASS" if _is_monotone_non_increasing(incentive_uplifts) else "FAIL",
            "measured": {
                "values": [r["value"] for r in incentive_sorted],
                "mean_uplift_ratio": incentive_uplifts,
            },
        },
    ]


def _write(name: str, payload: dict) -> Path:
    _RESULTS_DIR.mkdir(exist_ok=True)
    path = _RESULTS_DIR / name
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote: {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 9 sensitivity sweep (spec §11)")
    parser.add_argument(
        "--dimension",
        choices=(DIMENSION_BETA_FATIGUE, DIMENSION_BETA_INCENTIVE, "all"),
        default="all",
    )
    args = parser.parse_args()

    dimensions = (
        (DIMENSION_BETA_FATIGUE, DIMENSION_BETA_INCENTIVE) if args.dimension == "all" else (args.dimension,)
    )

    payloads: dict[str, dict] = {}
    for dimension in dimensions:
        print("=" * 78)
        print(f"SAMPARK Phase 9 sensitivity sweep — dimension: {dimension}")
        print(f"values: {DIMENSIONS[dimension]}   seeds: {FINAL_SEEDS}   backend: {BACKEND_MEMORY}")
        print("=" * 78, flush=True)
        payload = run_dimension(dimension)
        payload["aggregate"] = aggregate(payload)
        payload["crossing"] = find_crossing(payload["aggregate"])
        payloads[dimension] = payload
        _write(f"sensitivity_{dimension}.json", payload)

    if len(payloads) == 2:
        report = {
            "phase": 9,
            "artifact": "sensitivity sweep report",
            "constants_commit_sha": _git_commit_sha(),
            "precommitment": "results/phase9_precommitment.json",
            "seeds": list(FINAL_SEEDS),
            "backend": BACKEND_MEMORY,
            "world": "v1",
            "primary_metric": (
                "mean(Arm B recovered_amount_per_contact_paise) / "
                "mean(Arm A recovered_amount_per_contact_paise) over the five precommitted seeds"
            ),
            "dimensions": {
                d: {
                    "values": payloads[d]["values"],
                    "frozen_value": payloads[d]["frozen_value"],
                    "aggregate": payloads[d]["aggregate"],
                    "crossing": payloads[d]["crossing"],
                    "anchor_checks": payloads[d]["anchor_checks"],
                }
                for d in payloads
            },
            "predictions": evaluate_predictions(
                payloads[DIMENSION_BETA_FATIGUE]["aggregate"],
                payloads[DIMENSION_BETA_INCENTIVE]["aggregate"],
                payloads[DIMENSION_BETA_FATIGUE],
            ),
        }
        _write("phase9_sensitivity_report.json", report)
        print("\nPrecommitted predictions:")
        for p in report["predictions"]:
            print(f"  {p['id']}: {p['result']}  — {p['claim']}")


if __name__ == "__main__":
    main()
