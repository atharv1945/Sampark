"""sampark.models — Phase 6 intelligence layer.

Spec §18.1, Phase 6: "Uplift (T-learner) + fatigue hazard, calibration,
allocator upgrade, offline optimality-gap measurement." Exit criterion:
"Models beat the heuristic — or are honestly reported as not doing so,
with the ablation committed."

Nothing in this package sits on the money path directly — a
`sampark.allocator.scorer.Scorer` implementation
(`sampark.models.scorer.ModelBackedScorer`) is the one seam it connects
to, and that seam is model-agnostic by construction
(`sampark/allocator/scorer.py`). Training reads ONLY Arm A's public
outcome log (never `sim.environment` internals, never
`Population.hidden_response` — the same boundary
`sim/calibration.py` already observes for Phase 4's calibrated
constants).

Every training entry point in this package can return an "unavailable"
result instead of a fitted model. That is not a bug path — CLAUDE.md
§14 and the Phase 6 contract both require refusing to fit rather than
manufacturing labels or treatment/control variation the dataset does
not contain. See `sampark.models.uplift` and
`sampark.models.fatigue_hazard` for the specific checks each model
requires before it will fit anything.
"""

from __future__ import annotations
