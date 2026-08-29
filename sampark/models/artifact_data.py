"""GENERATED FILE — DO NOT EDIT BY HAND.

Produced by sim/train_phase6_models.py::main() from Arm A's outcome log
at seed 42 only (mirrors Design Lock section 14.1's Phase 4
calibration rule). Re-run `python -m sim.train_phase6_models` to
regenerate; do not hand-edit these values.

Deterministic: sampark.models.uplift.train_uplift_model and
sampark.models.fatigue_hazard.train_fatigue_hazard_model both run a
structural, non-random data-adequacy check before fitting anything, so
re-running this script against the same seed always reproduces this
exact file.
"""

from __future__ import annotations

TRAINING_SEED: int = 42

UPLIFT_AVAILABLE: bool = False
UPLIFT_UNAVAILABLE_REASON: str | None = 'no untreated (never-contacted) control population exists for any risk source: every eligible RiskItem is contacted by its matching Phase 2 agent exactly once (max uncontacted fraction observed: 0.0000). A T-learner requires real treated/control variation; this dataset has none until a holdout arm exists (spec section 8.9, Phase 7).'

FATIGUE_HAZARD_AVAILABLE: bool = False
FATIGUE_HAZARD_UNAVAILABLE_REASON: str | None = "agents.types.ContactOutcome carries no opt-out-related field (fields: ('outcome_id', 'agent_id', 'customer_id', 'risk_id', 'channel', 'incentive_bps', 'contacted_at', 'recovered', 'amount_recovered_paise', 'incentive_paise')); sampark.contracts.ContactState.optouts_by_channel exists on the contract but sim/ledger.py constructs every ContactState with optouts_by_channel={} and no generator code ever writes to it, so it carries no real signal either."

UPLIFT_TREATED_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {}
UPLIFT_CONTROL_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {}

FATIGUE_HAZARD_BY_BUCKET: dict[tuple[str, str, int], float] = {}

