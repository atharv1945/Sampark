"""Randomized customer-level holdout assignment — Phase 7, spec §8.9.

Deterministic, no RNG: membership is a SHA-256 hash-rank within amount-at-
risk quintile strata, keyed by (ASSIGNMENT_VERSION, seed, customer_id).
Pure — no numpy, no I/O, no database access, no `random`, no Python `hash()`
(unstable across processes with hash randomization).

Unit of assignment is the CUSTOMER, not the risk item (Phase 7 design lock,
Decision 2): `CONTACT_CAP_24H = 1` means holding out risk item X of customer
C would free C's contact slot for sibling item Y, so a risk-item-level
holdout would let the control unit change a treated unit's treatment —
SUTVA violated at exactly the point the mediation thesis is about.
Customer-level assignment removes that interference entirely and matches
`contact_slot_claims (customer_id, window_id)`, the system's own contention
key.

Stratification is by total amount-at-risk quintile (not a plain Bernoulli
draw): `amount_paise` is lognormal with sigma up to 0.9, capped at
Rs 1,00,000, so the top decile dominates recovery. A plain hash-Bernoulli at
5,000 units would routinely produce arms with materially different mean
amount-at-risk, and any natural-rate/uplift estimate would inherit that
imbalance. Rank-within-stratum also guarantees exact stratum counts, which
Bernoulli cannot.

Nesting (holdout(0.10) subset-of holdout(0.20), required for the two-fraction
interference sensitivity to be a clean comparison rather than two unrelated
draws): the design-lock document's original hash key included
`fraction_bps`. That was a self-contradiction, caught during implementation
— baking fraction_bps into the rank key makes the f=0.10 and f=0.20
orderings UNRELATED, which breaks nesting rather than delivering it. This
implementation excludes fraction_bps from the hash: the rank ORDER within
each stratum depends only on (version, seed, customer_id) and is identical
across every fraction; only the PREFIX LENGTH taken from that fixed order
depends on fraction. Nesting then holds by construction, exactly because
ranked[:k(0.20)] is a superset of ranked[:k(0.10)] for the same ranked list.
`holdout_fraction_bps` is still recorded as metadata on the audit digest
event (sampark.audit — Phase 7) — it is just not a hash input.

f=0.0 returns the empty set, which is the world-v1 placebo path: every
holdout-aware caller must behave identically to a pre-Phase-7 call when
given an empty holdout set.
"""

from __future__ import annotations

import hashlib
from typing import Mapping, Sequence

ASSIGNMENT_VERSION = 1
N_STRATA = 5

_HASH_PREFIX = "sampark-holdout"


def _rank_key(seed: int, customer_id: str) -> int:
    """SHA-256(f"{_HASH_PREFIX}:v{ASSIGNMENT_VERSION}:{seed}:{customer_id}")[:8]
    as a big-endian unsigned int. Never Python's `hash()` (unstable across
    processes/runs under hash randomization) and never a `random`/`numpy.random`
    draw — this is a pure function of its three logical inputs."""
    digest = hashlib.sha256(
        f"{_HASH_PREFIX}:v{ASSIGNMENT_VERSION}:{seed}:{customer_id}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big")


def strata(customer_amounts: Mapping[str, int]) -> tuple[tuple[str, ...], ...]:
    """N_STRATA amount-at-risk quintile strata. Customers are sorted by
    `(total_amount_paise, customer_id)` — the trailing customer_id breaks ties
    deterministically — then split into N_STRATA groups as evenly as possible,
    with any remainder distributed to the lowest-indexed (lowest-amount)
    strata first. Deterministic; no randomness anywhere in this function."""
    ordered = sorted(customer_amounts.keys(), key=lambda cid: (customer_amounts[cid], cid))
    n = len(ordered)
    base, remainder = divmod(n, N_STRATA)

    result: list[tuple[str, ...]] = []
    start = 0
    for i in range(N_STRATA):
        size = base + (1 if i < remainder else 0)
        result.append(tuple(ordered[start : start + size]))
        start += size
    return tuple(result)


def assign(seed: int, fraction: float, customer_amounts: Mapping[str, int]) -> frozenset[str]:
    """Deterministic customer-level holdout assignment.

    `customer_amounts` maps every eligible customer_id to their total
    amount-at-risk in paise (sum of `RiskItem.amount_paise` over all their
    risk items) — the caller's job, not this function's; this module never
    reads a `Ledger` or any generator internals directly.

    Within each amount-quintile stratum (see `strata`), customers are ranked
    by `_rank_key(seed, customer_id)` and the first
    `floor(len(stratum) * fraction)` are held out — computed as exact integer
    arithmetic on `fraction_bps` (never `int(len * fraction)`, which is
    exposed to float-precision truncation error, e.g. 0.1 * 1000 can evaluate
    to 99.999999999 and silently drop one customer).

    `fraction == 0.0` returns the empty set without touching `customer_amounts`
    beyond validating the type — the world-v1 placebo path.
    """
    if not (0.0 <= fraction < 1.0):
        raise ValueError(f"fraction must be in [0.0, 1.0), got {fraction!r}")

    fraction_bps = round(fraction * 10_000)
    if fraction_bps == 0:
        return frozenset()

    held_out: list[str] = []
    for stratum in strata(customer_amounts):
        ranked = sorted(stratum, key=lambda cid: _rank_key(seed, cid))
        k = (len(ranked) * fraction_bps) // 10_000
        held_out.extend(ranked[:k])
    return frozenset(held_out)


def membership_digest(held_out: frozenset[str]) -> str:
    """SHA-256 hex digest of the sorted, newline-joined customer_id set —
    the audit-event digest form (Phase 7 design lock, Decision 7): any change
    to membership changes this digest, and the full list is reproducible from
    `assign()` alone, so a verbose per-customer audit trail is unnecessary."""
    joined = "\n".join(sorted(held_out))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def customer_amounts_from_risk_items(
    risk_items: Sequence, risk_customer_map: Mapping[str, str]
) -> dict[str, int]:
    """Convenience builder: customer_id -> total amount_paise across all of
    that customer's risk items. `risk_items` is any sequence of objects with
    `.risk_id` and `.amount_paise` (i.e. `sim.ledger.Ledger.risk_items`) —
    typed loosely here rather than importing `sampark.contracts.RiskItem`, so
    this module has no dependency beyond the two fields it actually reads."""
    totals: dict[str, int] = {}
    for item in risk_items:
        customer_id = risk_customer_map[item.risk_id]
        totals[customer_id] = totals.get(customer_id, 0) + item.amount_paise
    return totals
