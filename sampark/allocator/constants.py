"""Frozen Phase 4 constants — Design Lock §14.3.

These are DECIDED constants, fixed before any Arm B run and never
altered after seeing results (Design Lock: "Do not tune parameters
after seeing results"). They are distinct from
sampark/allocator/calibrated.py, which holds CALIBRATED constants
(derived from Arm A's seed-42 log by sim/calibration.py).

Do not edit these values without a new owner decision. If they change,
the change belongs in a fresh design-lock revision, not a silent edit
here.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# --- Contact budget (Design Lock §3, §14.3) --------------------------------

CONTACT_CAP_24H: int = 1
CONTACT_CAP_7D: int = 2

QUIET_HOURS_START_HOUR: int = 21  # 21:00 IST
QUIET_HOURS_END_HOUR: int = 9  # 09:00 IST

# --- Fatigue / expected-net scoring (Design Lock §6) -----------------------

FORWARD_HORIZON_DAYS: float = 30.0

# --- Fairness / aging (Design Lock §7) --------------------------------------

MAX_DEFERRAL_WINDOWS: int = 7

# round(0.10 * P_BASE_MEAN * MEAN_AMOUNT_PAISE) = round(0.10 * 0.285569 * 387607)
AGING_BONUS_PAISE: int = 11_070

# --- Channel cost (Design Lock §14.3) — near-inert at these amounts --------

CHANNEL_COST_PAISE: dict[str, int] = {
    "sms": 20,
    "whatsapp": 40,
    "voice": 400,
}

# --- Grant lifecycle (Design Lock §9, §14.3) --------------------------------

GRANT_TTL_HOURS: float = 2.0  # simulated hours

# --- Issuance transaction retry (Design Lock §11) --------------------------

MAX_SERIALIZATION_RETRIES: int = 5

# --- Margin budgets (Design Lock §14.3) -------------------------------------

# Arm A's mean daily CEILING exposure (110,373,160 paise / 30 days),
# seed 42. Arm B gets exactly the margin authority Arm A consumed.
MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW: int = 3_679_105

# 500 bps is the highest existing agent capability ceiling
# (cart_recovery_agent, agents/cart_recovery.py). Structurally
# non-binding at CONTACT_CAP_24H = 1 (Design Lock §1.3, §18.1) — a
# customer can receive at most one grant per window, so this pool can
# never be exhausted by a second draw in the shipped configuration.
CUSTOMER_MARGIN_BPS: int = 500

# --- Observable ledger statistics (Design Lock §14.2, seed 42) -------------
# Used by the fatigue term's forward-value estimate (sampark/policy/soft/
# fatigue.py). These are OBSERVED, not calibrated or tuned — they are
# summary statistics of the committed generator's output at seed 42.

MEAN_AMOUNT_PAISE: float = 387_607.0
LAMBDA_PER_CUSTOMER_DAY: float = 0.13569
