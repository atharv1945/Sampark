"""Credited-recovery arithmetic — Phase 7, spec §8.9.

    observed_recovery(r)  = r.amount_paise * 1[recovered]
    natural_rate_bps(s)   = round(10_000 * baseline.rate)          -- int, never a float in the ledger
    expected_natural(r)   = (r.amount_paise * natural_rate_bps) // 10_000   -- int, floor
    credited_recovery(g)  = observed_recovery(r) - expected_natural(r)      -- signed, NEVER clamped

`credited_recovery_paise` may be negative and must not be clamped: an item
that did not recover still consumed a contact against a positive natural
baseline; clamping at zero would bias the aggregate upward by exactly the
negative tail. `sampark/attribution/schema_proposal.sql`'s
`attribution_credits` table carries no non-negative CHECK on this column,
deliberately, matching this module.

`credit_id_for` is `uuid5`, never `uuid4` — the id IS the idempotency key,
mirroring `sampark.audit.chain.event_id_for`'s exact precedent and
rationale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sampark.attribution.baseline import BaselineRate
from sim.natural import NATURAL_MODEL_VERSION

# Frozen namespace for every deterministic credit_id, mirroring
# sampark.audit.chain.NS_AUDIT's exact precedent.
NS_ATTRIBUTION = uuid.UUID("6a1f9b2e-4c3d-4a1e-9b2e-6a1f9b2e4c3d")


def credit_id_for(grant_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(NS_ATTRIBUTION, str(grant_id))


@dataclass(frozen=True)
class Credit:
    credit_id: uuid.UUID
    grant_id: uuid.UUID
    observed_recovered_paise: int
    natural_rate_bps: int
    expected_natural_paise: int
    credited_recovery_paise: int
    baseline_stratum: str
    baseline_level: str
    baseline_holdout_n: int
    holdout_fraction_bps: int
    natural_model_version: int
    observed_at: datetime


def compute_credit(
    grant_id: uuid.UUID,
    observed_recovered_paise: int,
    amount_paise: int,
    baseline: BaselineRate,
    holdout_fraction: float,
    observed_at: datetime,
) -> Credit:
    """Pure arithmetic — no RNG, no I/O, no wall clock. `amount_paise` is
    the risk item's authoritative amount (used for `expected_natural`
    regardless of whether the item recovered — an unrecovered item still
    consumed a contact against a positive baseline)."""
    natural_rate_bps = round(10_000 * baseline.rate)
    expected_natural_paise = (amount_paise * natural_rate_bps) // 10_000
    credited_recovery_paise = observed_recovered_paise - expected_natural_paise

    return Credit(
        credit_id=credit_id_for(grant_id),
        grant_id=grant_id,
        observed_recovered_paise=observed_recovered_paise,
        natural_rate_bps=natural_rate_bps,
        expected_natural_paise=expected_natural_paise,
        credited_recovery_paise=credited_recovery_paise,
        baseline_stratum=baseline.stratum,
        baseline_level=baseline.level,
        baseline_holdout_n=baseline.n,
        holdout_fraction_bps=round(10_000 * holdout_fraction),
        natural_model_version=NATURAL_MODEL_VERSION,
        observed_at=observed_at,
    )
