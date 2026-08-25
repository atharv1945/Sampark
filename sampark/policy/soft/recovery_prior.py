"""recovery_prior — p_hat, the calibrated current-contact recovery
probability, Design Lock §6.2.

    p_hat = P_BASE[source, root_cause] * DECAY ** n

`n` is SAMPARK's own count of contacts to this customer strictly
before decision_at — never HiddenResponseProfile, never Environment
internals, never a Phase 6 model. P_BASE and DECAY come from
sampark/allocator/calibrated.py, generated once from Arm A's seed-42
log (sim/calibration.py) and frozen thereafter.
"""

from __future__ import annotations

from sampark.allocator import calibrated


def p_hat(source: str, root_cause: str, n: int) -> float:
    if n < 0:
        raise ValueError(f"contact index n must be >= 0, got {n!r}")
    return calibrated.p_base(source, root_cause) * (calibrated.DECAY**n)
