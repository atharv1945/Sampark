"""fatigue — the corrected, forward-looking fatigue term, Design Lock §6.

Derivation (Design Lock §6.1): let n be the customer's contact index,
D = DECAY. Without this contact, the customer's next m contacts occur
at indices n, n+1, ..., n+m-1; with it, they occur at n+1, ..., n+m.
The expected future recovery lost telescopes to a closed form:

    loss = p_bar * A_fwd * D**n * (1 - D**m)

which is exact, not the first-order (1 - DECAY) * forward_value
approximation an earlier draft used.

FORWARD_HORIZON_DAYS is a FIXED 30-day rolling horizon, not "days
remaining in the simulated month" — using the simulator's own month
length here would let the allocator exploit the evaluation boundary
(fatigue -> 0 as the month ends). This is deliberately conservative: it
prices fatigue higher throughout and is expected to cost Arm B some
total rupees recovered relative to a declining-horizon variant, in
exchange for not gaming the measurement window (Design Lock §6.4).

`m` counts in FRACTIONAL future items (Poisson-rate * days), so `D**m`
uses real-exponent power — well-defined since D in (0, 1).
"""

from __future__ import annotations

from typing import Sequence

from sampark.allocator import calibrated
from sampark.allocator.constants import FORWARD_HORIZON_DAYS, LAMBDA_PER_CUSTOMER_DAY, MEAN_AMOUNT_PAISE


def fatigue_cost_paise(n: int, other_open_amounts_paise: Sequence[int]) -> float:
    if n < 0:
        raise ValueError(f"contact index n must be >= 0, got {n!r}")

    other_open_count = len(other_open_amounts_paise)
    future_count = LAMBDA_PER_CUSTOMER_DAY * FORWARD_HORIZON_DAYS
    m = other_open_count + future_count

    if m <= 0:
        return 0.0

    v_forward = sum(other_open_amounts_paise) + future_count * MEAN_AMOUNT_PAISE
    a_fwd = v_forward / m

    decay_n = calibrated.DECAY**n
    decay_m = calibrated.DECAY**m
    return calibrated.P_BASE_MEAN * decay_n * (1.0 - decay_m) * a_fwd
