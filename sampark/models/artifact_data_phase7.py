"""GENERATED FILE — DO NOT EDIT BY HAND.

Produced by sim/train_phase7_models.py::main() from Arm A-H's outcome
log (sim.arm_a_holdout.run_arm_a_holdout) at seed 42, holdout
fraction 0.1 only. Re-run `python -m sim.train_phase7_models
--seed 42 --fraction 0.1` to regenerate; do not
hand-edit these values.

A SEPARATE artifact from sampark/models/artifact_data.py (Phase 6) —
phase6_model's committed evidence is untouched by this file's existence
or content.

Deterministic: sampark.models.uplift.train_uplift_model_holdout and
sampark.models.fatigue_hazard.train_fatigue_hazard_model_holdout both
run structural, non-random data-adequacy checks before fitting
anything, so re-running this script against the same (seed, fraction)
always reproduces this exact file.
"""

from __future__ import annotations

TRAINING_SEED: int = 42
HOLDOUT_FRACTION: float = 0.1

UPLIFT_AVAILABLE: bool = False
UPLIFT_UNAVAILABLE_REASON: str | None = "one or more (source, root_cause) buckets fall below the 200-observation floor in at least one arm (bucket -> (n_treated, n_control)): {('abandoned_checkout', 'price_hesitation'): (1522, 141), ('abandoned_checkout', 'unknown'): (153, 25), ('failed_payment', 'insufficient_funds'): (889, 81), ('failed_payment', 'issuer_downtime'): (1773, 177), ('failed_payment', 'unknown'): (149, 17), ('mandate_failure', 'authentication_drop'): (1107, 113), ('mandate_failure', 'insufficient_funds'): (1132, 112), ('mandate_failure', 'issuer_downtime'): (1053, 119), ('mandate_failure', 'mandate_expired'): (1097, 116), ('mandate_failure', 'unknown'): (156, 17), ('overdue_invoice', 'disputed'): (1474, 171), ('overdue_invoice', 'intent_lost'): (1405, 162), ('overdue_invoice', 'price_hesitation'): (1412, 183), ('overdue_invoice', 'unknown'): (159, 17)}"

FATIGUE_HAZARD_AVAILABLE: bool = True
FATIGUE_HAZARD_UNAVAILABLE_REASON: str | None = None

UPLIFT_TREATED_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {}
UPLIFT_CONTROL_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {}

FATIGUE_HAZARD_BY_BUCKET: dict[tuple[str, str, int], float] = {
    ("abandoned_checkout", "intent_lost", 0): 0.009043635541487679,
    ("abandoned_checkout", "intent_lost", 1): 0.019844067681792536,
    ("abandoned_checkout", "price_hesitation", 0): 0.009043635541487679,
    ("abandoned_checkout", "price_hesitation", 1): 0.019844067681792536,
    ("abandoned_checkout", "unknown", 0): 0.009043635541487679,
    ("abandoned_checkout", "unknown", 1): 0.019844067681792536,
    ("failed_payment", "authentication_drop", 0): 0.009043635541487679,
    ("failed_payment", "authentication_drop", 1): 0.020606378153115456,
    ("failed_payment", "insufficient_funds", 0): 0.009043635541487679,
    ("failed_payment", "insufficient_funds", 1): 0.020606378153115456,
    ("failed_payment", "issuer_downtime", 0): 0.009043635541487679,
    ("failed_payment", "issuer_downtime", 1): 0.020606378153115456,
    ("failed_payment", "unknown", 0): 0.009043635541487679,
    ("failed_payment", "unknown", 1): 0.020606378153115456,
    ("mandate_failure", "authentication_drop", 0): 0.009043635541487679,
    ("mandate_failure", "authentication_drop", 1): 0.027766193529729685,
    ("mandate_failure", "insufficient_funds", 0): 0.009043635541487679,
    ("mandate_failure", "insufficient_funds", 1): 0.027766193529729685,
    ("mandate_failure", "issuer_downtime", 0): 0.009043635541487679,
    ("mandate_failure", "issuer_downtime", 1): 0.027766193529729685,
    ("mandate_failure", "mandate_expired", 0): 0.009043635541487679,
    ("mandate_failure", "mandate_expired", 1): 0.027766193529729685,
    ("mandate_failure", "unknown", 0): 0.009043635541487679,
    ("mandate_failure", "unknown", 1): 0.027766193529729685,
    ("overdue_invoice", "disputed", 0): 0.009043635541487679,
    ("overdue_invoice", "disputed", 1): 0.026262940062813208,
    ("overdue_invoice", "intent_lost", 0): 0.009043635541487679,
    ("overdue_invoice", "intent_lost", 1): 0.026262940062813208,
    ("overdue_invoice", "price_hesitation", 0): 0.009043635541487679,
    ("overdue_invoice", "price_hesitation", 1): 0.026262940062813208,
    ("overdue_invoice", "unknown", 0): 0.009043635541487679,
    ("overdue_invoice", "unknown", 1): 0.026262940062813208
}

