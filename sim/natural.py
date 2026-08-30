"""Natural-recovery process — Phase 7, spec §8.9.

World v2 completes the response model `sim/environment.py` already has,
rather than adding a competing mechanism. `p_recover(profile,
incentive_bps=0, prior_contacts=0)` already collapses exactly to
`profile.conversion_propensity` — that is why `P_BASE_MEAN` (0.28557,
`sampark/allocator/calibrated.py`) matches `Beta(2, 5)`'s mean (0.2857).
The contacted path has always embedded the customer's natural propensity;
the only population missing a resolution draw is the one that is NEVER
contacted. This module fills exactly that gap:

    p_natural(profile, risk_item) = clip(
        profile.conversion_propensity * NATURAL_MULTIPLIER_BY_ROOT_CAUSE[risk_item.root_cause],
        0.0, 1.0)

Every `NATURAL_MULTIPLIER_BY_ROOT_CAUSE[cause] < 1.0` — contacting a
customer never lowers their recovery probability in this world, matching
`p_recover`'s own structure (no term makes contact harmful). This is what
guarantees uplift is non-negative in expectation.

Sampling happens strictly AFTER the contact stream is complete (Phase 7
design lock, Decision 1), over the complement of the contacted set, from
an RNG namespace (`_NATURAL_RECOVERY_SALT`) independent of
`sim.environment.Environment`'s response-model RNG
(`_RESPONSE_MODEL_SALT = 991`). Because natural recovery never influences
which candidates are admitted, ranked, granted, deferred or denied, every
Phase 4/5/6 decision, audit event and `prev_hash` is bit-identical between
world v1 and world v2 — verified directly by
`tests/sim_environment/test_world_v2_non_perturbation.py`.

NATURAL_MULTIPLIER_BY_ROOT_CAUSE is a committed, owner-authored prior — the
same class of artifact as `sim/environment.py`'s BETA_INCENTIVE/
BETA_FATIGUE (that module's own docstring: "simulation coefficients ...
not calibrated against anything"). The ORDERING is the defensible claim
(transient technical failure > customer-decision failure > structurally
blocked); the exact digits are a prior. Both are locked here, not chosen
per-run and never re-tuned after observing a Phase 7 result (Phase 7
design lock, Decision 10 / Decision 17).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sampark.allocator.constants import MAX_DEFERRAL_WINDOWS
from sampark.rootcause import load_taxonomy
from sim.generator import MONTH_LENGTH_DAYS, MONTH_START
from sim.population import HiddenResponseProfile

NATURAL_MODEL_VERSION = 1

# Owner-authored prior (Phase 7 design lock, Decision 10). Ordering is
# locked: {issuer_downtime, insufficient_funds} > {authentication_drop} >
# {price_hesitation, intent_lost, mandate_expired} > {disputed}, with
# `unknown` strictly interior to the mapped range — never the max, never
# zero. Every value in [0.05, 0.40] and strictly < 1.0.
NATURAL_MULTIPLIER_BY_ROOT_CAUSE: dict[str, float] = {
    "issuer_downtime": 0.40,        # purely transient; the next retry succeeds unaided
    "insufficient_funds": 0.35,     # self-heals on the next salary/balance cycle
    "authentication_drop": 0.25,    # customer often retries unprompted
    "unknown": 0.15,                # mid-range by construction — never the max
    "mandate_expired": 0.10,        # requires an explicit customer re-authorization
    "price_hesitation": 0.10,       # a decision was made; it rarely reverses unaided
    "intent_lost": 0.05,            # floor — the customer has disengaged
    "disputed": 0.05,               # floor — structurally blocked while the dispute is open
}

_MULTIPLIER_MIN = 0.05
_MULTIPLIER_MAX = 0.40

_ORDERING_TIERS: tuple[tuple[str, ...], ...] = (
    ("issuer_downtime", "insufficient_funds"),
    ("authentication_drop",),
    ("price_hesitation", "intent_lost", "mandate_expired"),
    ("disputed",),
)

_PROBABILITY_EPSILON = 1e-9
_RESPONSE_MODEL_TAXONOMY_UNKNOWN = "unknown"


class InvalidMultiplierTableError(ValueError):
    """NATURAL_MULTIPLIER_BY_ROOT_CAUSE fails one of its locked invariants
    (Phase 7 design lock, Decision 10 §2.5.1) — domain, range, ordering, or
    the `unknown` interiority rule. Raised at import time via
    `_validate_multiplier_table()` rather than at first use, so a bad table
    is caught before any evidence run reads it."""


def _validate_multiplier_table(table: dict[str, float]) -> None:
    taxonomy_values = set(load_taxonomy().taxonomy)
    table_keys = set(table.keys())
    if table_keys != taxonomy_values:
        missing = taxonomy_values - table_keys
        extra = table_keys - taxonomy_values
        raise InvalidMultiplierTableError(
            f"NATURAL_MULTIPLIER_BY_ROOT_CAUSE domain mismatch vs "
            f"sampark/rootcause/taxonomy.yaml: missing={sorted(missing)!r}, extra={sorted(extra)!r}"
        )

    for cause, value in table.items():
        if not (_MULTIPLIER_MIN <= value <= _MULTIPLIER_MAX):
            raise InvalidMultiplierTableError(
                f"NATURAL_MULTIPLIER_BY_ROOT_CAUSE[{cause!r}] = {value!r} is outside "
                f"the locked range [{_MULTIPLIER_MIN}, {_MULTIPLIER_MAX}]"
            )
        if value >= 1.0:
            raise InvalidMultiplierTableError(
                f"NATURAL_MULTIPLIER_BY_ROOT_CAUSE[{cause!r}] = {value!r} must be < 1.0 "
                "(contacting must never lower recovery probability in this world)"
            )

    tier_ranges = []
    for tier in _ORDERING_TIERS:
        values = [table[cause] for cause in tier]
        tier_ranges.append((min(values), max(values)))
    for (lo_of_higher, hi_of_higher), (lo_of_lower, hi_of_lower) in zip(tier_ranges, tier_ranges[1:]):
        if lo_of_higher < hi_of_lower:
            raise InvalidMultiplierTableError(
                "NATURAL_MULTIPLIER_BY_ROOT_CAUSE violates the locked ordering "
                f"{_ORDERING_TIERS!r}: a lower tier has a value >= a higher tier's minimum"
            )

    unknown_value = table[_RESPONSE_MODEL_TAXONOMY_UNKNOWN]
    mapped_values = [v for cause, v in table.items() if cause != _RESPONSE_MODEL_TAXONOMY_UNKNOWN]
    if not (min(mapped_values) < unknown_value < max(mapped_values)):
        raise InvalidMultiplierTableError(
            f"NATURAL_MULTIPLIER_BY_ROOT_CAUSE['unknown'] = {unknown_value!r} must lie strictly "
            f"inside the mapped causes' range ({min(mapped_values)!r}, {max(mapped_values)!r})"
        )


_validate_multiplier_table(NATURAL_MULTIPLIER_BY_ROOT_CAUSE)


def p_natural(profile: HiddenResponseProfile, root_cause: str) -> float:
    """Pure ground-truth natural-recovery probability — no RNG, no state,
    mirroring `sim.environment.p_recover`'s own shape. `root_cause` must be
    a key of `NATURAL_MULTIPLIER_BY_ROOT_CAUSE` (i.e. a taxonomy value) —
    a KeyError here is a real bug (a root_cause outside the taxonomy),
    never silently defaulted."""
    raw = profile.conversion_propensity * NATURAL_MULTIPLIER_BY_ROOT_CAUSE[root_cause]
    return min(max(raw, _PROBABILITY_EPSILON), 1 - _PROBABILITY_EPSILON)


@dataclass(frozen=True)
class NaturalOutcome:
    """The resolution of ONE never-contacted risk item under world v2.
    Deliberately NOT a `ContactOutcome` — no `agent_id`, no `channel`, no
    `incentive_bps`: none of those exist for an item nobody contacted, and
    reusing `ContactOutcome`'s shape (with blanked fields) would silently
    pollute every `Sequence[ContactOutcome]` consumer (`sim/metrics.py`,
    `sim/mediation_metrics.py`, `sim.mediation_metrics.build_contact_records`).
    """

    risk_id: str
    customer_id: str
    source: str
    root_cause: str
    amount_paise: int
    p_natural: float
    recovered: bool
    amount_recovered_paise: int
    observed_at: datetime


def observation_window_end() -> datetime:
    """The Phase 7 observation-window horizon end (Phase 7 design lock, Part
    3.3): `MONTH_START + MONTH_LENGTH_DAYS + (MAX_DEFERRAL_WINDOWS + 1)`
    days — the identical tail `sim/arm_b.py::_window_range` already extends
    the mediation loop by, so every candidate reaches a terminal outcome.
    Used as `observed_at` for every `NaturalOutcome`: natural recovery has
    no instantaneous decision moment the way a contact does, so it is
    attributed to the end of the window it is measured over. A pure
    function of committed constants — never a wall clock."""
    return MONTH_START + timedelta(days=MONTH_LENGTH_DAYS + MAX_DEFERRAL_WINDOWS + 1)
