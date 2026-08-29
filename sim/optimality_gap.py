"""Offline optimality-gap measurement — Phase 6, spec §8.7 / §18.1.

    python -m sim.optimality_gap --seed 42 --top-k-windows 5

Measures how far the budgeted-greedy allocator's PER-WINDOW admission
choice falls short of that window's own exact optimum, for a SAMPLE of
windows (spec §8.7: "run an exact solver offline over a sample of
windows, report the mean and worst-case gap").

--- What this measures, precisely (read before citing the number) ---

For one window, greedy admits every hard-policy-survivor with
`expected_net_paise > 0`, then grants AT MOST ONE candidate per customer,
subject to the merchant margin pool for that window
(`sampark.allocator.constants.MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW`,
which `sampark/budget/store.py` confirms resets independently per
`window_id` — so treating each window's capacity as this one constant,
rather than something carried over from a prior window, is exact, not
an approximation).

Choosing AT MOST ONE candidate per customer to maximize total
`expected_net_paise` subject to one shared capacity is a Multiple-Choice
Knapsack Problem (MCKP) — solved here by an exact, dependency-light
dynamic program (`_solve_mckp`), no external solver (CLAUDE.md §2: ask
before introducing a solver dependency; this repository has none and
none is added).

Two DELIBERATE simplifications, stated here so the reported number is
never mistaken for something it is not:

1. **Per-window optimum, not whole-horizon optimum.** Each window is
   solved independently, using the SAME admitted-candidate set greedy
   actually saw (via `outcome_observer`). Greedy's real inefficiency
   also comes from CROSS-window deferral dynamics (a candidate deferred
   this window competes again next window with an aging bonus) — this
   tool does not model that. A whole-horizon optimum would require
   solving a multi-period stochastic problem, is far more expensive, and
   is explicitly NOT what is computed here.
2. **No incentive downgrade CHOICE inside the DP.** Each admitted
   candidate is one atomic (value, weight) item; the DP does not
   consider downgrading an incentive for candidates IT selects to fit
   more of them in. The one exception: the candidate greedy actually
   GRANTED is recorded at its REAL, already-realized terms (its
   observed `effective_incentive_bps`, which may be a downgrade --
   `sampark.budget.margin.downgrade_to_fit` -- and the score that
   corresponds to it), so that the observed "achieved" total is always
   a genuinely feasible choice under the DP's own item definitions, and
   `achieved <= optimal` holds as an invariant rather than an
   assumption. The DP itself never explores downgrading a candidate it
   would not otherwise have chosen -- so it cannot discover an
   admission the real allocator's downgrade-and-retry logic might have
   found, making the reported optimum a LOWER BOUND on the true
   fully-general optimum (which would also let the DP itself choose to
   downgrade).

Capacity is discretized to the nearest `--granularity-paise` (default
100 paise = Rs 1) purely for DP tractability — a real merchant margin
pool of ~Rs 36.8 lakh per window at 1-paise granularity is a ~3.68e6
state DP per window; at Rs 1 granularity it is ~3.68e4 states, and the
resulting gap is exact up to that stated Rs 1 rounding, not a heuristic
approximation.

--- Sampling ---

Reports on the `--top-k-windows` windows (by admitted-ceiling / capacity
ratio, i.e. the windows MOST LIKELY to be capacity-constrained) out of
the run's full window range, not a random sample — a random sample would
mostly land on non-binding windows (Design Lock, Phase 4: the merchant
pool binds only a minority of windows) and report an uninformative ~0
gap almost everywhere. This targeting is stated explicitly, in the
output and here, precisely because it is not a random sample.
"""

from __future__ import annotations

import argparse
import collections
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence

from sampark.allocator.constants import MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.budget.margin import incentive_ceiling_paise
from sampark.budget.windows import window_id_for
from sim.arm_b import run_arm_b

_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


@dataclass(frozen=True)
class _WindowSnapshot:
    window_id: date
    # customer_id -> [(risk_id, expected_net_paise, requested_incentive_ceiling_paise), ...]
    admitted_by_customer: dict[str, list[tuple[str, float, int]]]
    # ALL risk_ids GRANTED in this window (one PER CUSTOMER can be granted
    # -- Design Lock: at most one grant per customer per window, but many
    # customers can each be granted in the same window, limited only by
    # the shared merchant margin pool), with the expected_net_paise each
    # was actually admitted/scored at.
    granted: list[tuple[str, float]]

    @property
    def achieved_expected_net_paise(self) -> float:
        return sum(value for _rid, value in self.granted)


def _collect_window_snapshots(seed: int, merchant_budget_paise_per_window: int) -> list[_WindowSnapshot]:
    by_window: dict[date, dict[str, list[tuple[str, float, int]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    granted_by_window: dict[date, list[tuple[str, float]]] = collections.defaultdict(list)

    def _observe(outcomes: tuple[AllocationOutcome, ...], decision_at: datetime) -> None:
        wid = window_id_for(decision_at)
        for outcome in outcomes:
            if outcome.score is None or outcome.score.expected_net_paise <= 0:
                continue  # never admitted -- hard-denied or NEGATIVE_EXPECTED_NET
            risk_id = outcome.candidate.risk_item.risk_id
            if outcome.outcome_kind is OutcomeKind.GRANTED:
                # The GRANTED outcome's OWN score/effective_incentive_bps
                # may reflect a margin downgrade (Design Lock section 8),
                # in which case both its value (higher, since less
                # incentive is paid out) and its true weight (lower, the
                # ACTUAL ceiling reserved) differ from the undowngraded
                # request -- use the REAL, internally-consistent pair, or
                # `achieved` (a real, feasible allocation) could exceed
                # what the DP -- which assumes every item costs its full
                # undowngraded ceiling -- believes is the optimum.
                assert outcome.effective_incentive_bps is not None
                weight = incentive_ceiling_paise(outcome.candidate.risk_item.amount_paise, outcome.effective_incentive_bps)
                granted_by_window[wid].append((risk_id, outcome.score.expected_net_paise))
            else:
                weight = outcome.candidate.requested_incentive_ceiling_paise
            by_window[wid][outcome.candidate.customer_id].append((risk_id, outcome.score.expected_net_paise, weight))

    run_arm_b(
        seed,
        backend="memory",
        merchant_budget_paise_per_window=merchant_budget_paise_per_window,
        outcome_observer=_observe,
    )

    snapshots = []
    for wid, admitted_by_customer in sorted(by_window.items()):
        snapshots.append(
            _WindowSnapshot(
                window_id=wid,
                admitted_by_customer={k: v for k, v in admitted_by_customer.items()},
                granted=list(granted_by_window.get(wid, [])),
            )
        )
    return snapshots


def _solve_mckp(
    groups: Sequence[Sequence[tuple[float, int]]], capacity_paise: int, granularity_paise: int
) -> float:
    """Exact multiple-choice knapsack: choose AT MOST ONE (value, weight)
    per group (or none) to maximize total value subject to total weight
    <= capacity_paise. `weight` is rounded UP to the nearest
    `granularity_paise` before the DP runs (rounding up keeps the DP's
    capacity usage conservative -- it never claims a combination fits
    that would not, in real paise, actually fit within `capacity_paise`).
    Pure Python, O(len(groups) * (capacity_paise // granularity_paise) *
    max_items_per_group) -- no external solver."""
    capacity_units = capacity_paise // granularity_paise
    dp = [0.0] * (capacity_units + 1)

    for group in groups:
        new_dp = list(dp)  # "choose none from this group" is always an option
        for value, weight in group:
            weight_units = -(-weight // granularity_paise)  # ceil division
            if weight_units > capacity_units:
                continue  # cannot possibly fit even alone
            for c in range(capacity_units, weight_units - 1, -1):
                candidate_value = dp[c - weight_units] + value
                if candidate_value > new_dp[c]:
                    new_dp[c] = candidate_value
        dp = new_dp

    return max(dp)


@dataclass(frozen=True)
class WindowGapResult:
    window_id: str
    admitted_count: int
    total_requested_ceiling_paise: int
    capacity_paise: int
    achieved_expected_net_paise: float
    optimal_expected_net_paise: float
    gap_ratio: float  # achieved / optimal, in (0, 1] -- 1.0 means greedy already matched the DP optimum


def compute_optimality_gap(
    seed: int,
    top_k_windows: int = 5,
    merchant_budget_paise_per_window: int = MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
    granularity_paise: int = 100,
) -> list[WindowGapResult]:
    snapshots = _collect_window_snapshots(seed, merchant_budget_paise_per_window)

    def _total_ceiling(snap: _WindowSnapshot) -> int:
        return sum(ceiling for options in snap.admitted_by_customer.values() for _rid, _v, ceiling in options)

    ranked = sorted(snapshots, key=_total_ceiling, reverse=True)
    sample = ranked[:top_k_windows]

    results: list[WindowGapResult] = []
    for snap in sample:
        groups = [
            [(value, ceiling) for _rid, value, ceiling in options]
            for options in snap.admitted_by_customer.values()
        ]
        optimal = _solve_mckp(groups, merchant_budget_paise_per_window, granularity_paise)
        achieved = snap.achieved_expected_net_paise
        gap_ratio = 1.0 if optimal <= 0 else achieved / optimal
        results.append(
            WindowGapResult(
                window_id=snap.window_id.isoformat(),
                admitted_count=sum(len(v) for v in snap.admitted_by_customer.values()),
                total_requested_ceiling_paise=_total_ceiling(snap),
                capacity_paise=merchant_budget_paise_per_window,
                achieved_expected_net_paise=achieved,
                optimal_expected_net_paise=optimal,
                gap_ratio=gap_ratio,
            )
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 6 offline optimality-gap measurement")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-k-windows", type=int, default=5)
    parser.add_argument("--granularity-paise", type=int, default=100)
    parser.add_argument(
        "--merchant-budget-paise", type=int, default=MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
        help="Per-window merchant margin capacity to solve under -- defaults to the frozen headline "
             "constant; pass MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2 to reproduce the "
             "merchant_margin_half ablation's capacity.",
    )
    parser.add_argument("--label", default="headline", help="A short tag recorded in the output JSON, e.g. 'headline' or 'merchant_margin_half'.")
    parser.add_argument("--json-out", type=Path, default=_RESULTS_DIR / "phase6_optimality_gap_seed42.json")
    args = parser.parse_args()

    results = compute_optimality_gap(
        args.seed,
        top_k_windows=args.top_k_windows,
        merchant_budget_paise_per_window=args.merchant_budget_paise,
        granularity_paise=args.granularity_paise,
    )

    print("=" * 78)
    print(f"SAMPARK Phase 6 optimality gap -- seed {args.seed}, top {args.top_k_windows} windows by admitted ceiling")
    print("PER-WINDOW gap (not whole-horizon); no incentive downgrade inside the DP (see module docstring)")
    print("=" * 78)
    for r in results:
        print(
            f"window {r.window_id}: admitted={r.admitted_count} "
            f"achieved={r.achieved_expected_net_paise:.1f} optimal={r.optimal_expected_net_paise:.1f} "
            f"gap_ratio={r.gap_ratio:.4f}"
        )

    ratios = [r.gap_ratio for r in results]
    mean_ratio = sum(ratios) / len(ratios) if ratios else float("nan")
    worst_ratio = min(ratios) if ratios else float("nan")
    print("-" * 78)
    print(f"mean gap_ratio: {mean_ratio:.4f}   worst-case gap_ratio: {worst_ratio:.4f}")
    print("=" * 78)

    payload = {
        "seed": args.seed,
        "label": args.label,
        "merchant_budget_paise_per_window": args.merchant_budget_paise,
        "sampling": (
            f"top {args.top_k_windows} windows by total requested incentive ceiling "
            "(targeted at the windows most likely to be margin-constrained, NOT a random sample)"
        ),
        "method": (
            "exact per-window multiple-choice knapsack (dependency-light dynamic program), "
            f"capacity discretized to the nearest {args.granularity_paise} paise; "
            "no incentive downgrade inside the DP (lower-bounds the true achievable optimum); "
            "PER-WINDOW optimum, not whole-horizon (cross-window deferral dynamics not modeled)"
        ),
        "granularity_paise": args.granularity_paise,
        "mean_gap_ratio": mean_ratio,
        "worst_case_gap_ratio": worst_ratio,
        "windows": [
            {
                "window_id": r.window_id,
                "admitted_count": r.admitted_count,
                "total_requested_ceiling_paise": r.total_requested_ceiling_paise,
                "capacity_paise": r.capacity_paise,
                "achieved_expected_net_paise": r.achieved_expected_net_paise,
                "optimal_expected_net_paise": r.optimal_expected_net_paise,
                "gap_ratio": r.gap_ratio,
            }
            for r in results
        ],
    }
    args.json_out.parent.mkdir(exist_ok=True)
    args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote: {args.json_out}")


if __name__ == "__main__":
    main()
