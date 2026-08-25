"""GENERATED FILE — DO NOT EDIT BY HAND.

Produced by sim/calibration.py::main() from Arm A's outcome log at
seed 42 only (Design Lock §14.1). Re-run
`python -m sim.calibration` to regenerate; do not hand-edit these
values.

Calibrated from data — never chosen, never tuned against Arm B results
(Design Lock, "Do not tune parameters after seeing results").
"""

from __future__ import annotations

CALIBRATION_SEED: int = 42

# exp(slope) of OLS of log(empirical recovery rate at contact index n)
# on n, over buckets with >= 200 observations.
DECAY: float = 0.8479767239071947

# Global first-contact (n=0) recovery rate.
P_BASE_MEAN: float = 0.28556889883981273

# First-contact recovery rate per (source, root_cause), falling back to
# the source-level rate below 100 observations.
P_BASE_BY_BUCKET: dict[tuple[str, str], float] = {
    ("abandoned_checkout", "intent_lost"): 0.2825520833333333,
    ("abandoned_checkout", "price_hesitation"): 0.2576530612244898,
    ("abandoned_checkout", "unknown"): 0.27791563275434245,
    ("failed_payment", "authentication_drop"): 0.29108910891089107,
    ("failed_payment", "insufficient_funds"): 0.23722627737226276,
    ("failed_payment", "issuer_downtime"): 0.2736625514403292,
    ("failed_payment", "unknown"): 0.2741812642802742,
    ("mandate_failure", "authentication_drop"): 0.28289473684210525,
    ("mandate_failure", "insufficient_funds"): 0.2861736334405145,
    ("mandate_failure", "issuer_downtime"): 0.3079470198675497,
    ("mandate_failure", "mandate_expired"): 0.32792207792207795,
    ("mandate_failure", "unknown"): 0.30462020360219266,
    ("overdue_invoice", "disputed"): 0.27071823204419887,
    ("overdue_invoice", "intent_lost"): 0.26878612716763006,
    ("overdue_invoice", "price_hesitation"): 0.3147632311977716,
    ("overdue_invoice", "unknown"): 0.28545780969479356
}

# Source-level first-contact recovery rate, used as P_BASE_BY_BUCKET's
# fallback and as its own fallback to P_BASE_MEAN below
# 30 observations.
P_BASE_SOURCE_FALLBACK: dict[str, float] = {
    "abandoned_checkout": 0.27791563275434245,
    "failed_payment": 0.2741812642802742,
    "mandate_failure": 0.30462020360219266,
    "overdue_invoice": 0.28545780969479356
}


def p_base(source: str, root_cause: str) -> float:
    """P_BASE[source, root_cause] with the documented fallback chain."""
    key = (source, root_cause)
    if key in P_BASE_BY_BUCKET:
        return P_BASE_BY_BUCKET[key]
    if source in P_BASE_SOURCE_FALLBACK:
        return P_BASE_SOURCE_FALLBACK[source]
    return P_BASE_MEAN
