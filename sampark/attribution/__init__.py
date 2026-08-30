"""Attribution ledger — Phase 7, spec §8.9.

    Credited recovery = observed recovery - expected natural recovery

Resolves §8.9's arithmetic using the randomized-holdout natural-rate
estimate (never Arm H, never an allocator-declined item — Phase 7 design
lock, Decision 15). See baseline.py, credit.py, store.py.
"""

from __future__ import annotations

from sampark.attribution.baseline import (
    BaselineRate,
    NaturalBaselineEstimator,
    build_baseline_estimator,
)
from sampark.attribution.credit import NS_ATTRIBUTION, Credit, compute_credit, credit_id_for

__all__ = [
    "BaselineRate",
    "NaturalBaselineEstimator",
    "build_baseline_estimator",
    "Credit",
    "NS_ATTRIBUTION",
    "compute_credit",
    "credit_id_for",
]
