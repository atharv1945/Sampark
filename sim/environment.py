"""Environment — Arm A ground-truth outcome determination (Phase 2).

The ONLY component permitted to read Population.hidden_response or track
cross-agent contact history (Phase 2 locked decision 5). No RecoveryAgent
holds a reference to this module, and nothing under agents/ imports it.

Response model — a deterministic SYNTHETIC ground-truth assumption for
the simulator, not a domain truth and not derived from the spec beyond
naming the three hidden parameters (spec §11: conversion propensity,
fatigue hazard, price sensitivity):

    logit(p) = logit(conversion_propensity)
             + BETA_INCENTIVE * (incentive_bps / 10000) * price_sensitivity
             - BETA_FATIGUE * prior_contacts * fatigue_hazard
    p = sigmoid(logit(p))

BETA_INCENTIVE / BETA_FATIGUE are simulation coefficients, locked as a
Phase 2 starting point — they are not calibrated against anything and
are expected to be revisited (e.g. the spec §11 sensitivity sweep).

prior_contacts is the TRUE cumulative contact count for a customer
across all four agents combined, accumulated as actions are replayed in
their fixed chronological order (sim/arm_a.py) — never visible to any
agent's select_actions.

The response-model RNG is seeded from (seed, _RESPONSE_MODEL_SALT), a
namespace independent of sim/seeding.py's make_rngs. Phase 1 seeding
code is not modified or extended by this module.

Recovery-unit / exactly-once invariant (Phase 2 accounting
clarification): recovery_unit is the RiskItem (see sim/metrics.py) — a
risk item may be economically resolved at most once. `Environment`
enforces this directly: `observe` raises DuplicateObservationError if
the same risk_id is ever observed a second time, rather than silently
drawing (and potentially counting) a second recovery for money that was
already resolved.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Mapping, Protocol

import numpy as np

from agents.types import ContactAction, ContactOutcome
from sampark.contracts import RiskItem
from sim.generator import RawSignal
from sim.ledger import Ledger
from sim.population import HiddenResponseProfile, Population

BETA_INCENTIVE = 4.0
BETA_FATIGUE = 1.0

_RESPONSE_MODEL_SALT = 991
_PROBABILITY_EPSILON = 1e-9


class DuplicateObservationError(RuntimeError):
    """Environment.observe was called a second time for a risk_id that
    already has an outcome. recovery_unit is the RiskItem — a risk item
    may be economically resolved at most once (Phase 2 accounting
    clarification), so this is raised instead of silently drawing (and
    possibly counting) a second recovery."""


class _RandomSource(Protocol):
    def random(self) -> float: ...


def _logit(p: float) -> float:
    clamped = min(max(p, _PROBABILITY_EPSILON), 1 - _PROBABILITY_EPSILON)
    return math.log(clamped / (1 - clamped))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def p_recover(profile: HiddenResponseProfile, incentive_bps: int, prior_contacts: int) -> float:
    """Pure ground-truth recovery probability — no RNG, no state.

    Exposed at module level (rather than buried inside Environment.observe)
    so the model's required monotonicity properties (fatigue never helps;
    incentive never helps a less price-sensitive customer more than a
    more price-sensitive one) are directly testable without drawing a
    stochastic outcome.
    """
    logit_p = (
        _logit(profile.conversion_propensity)
        + BETA_INCENTIVE * (incentive_bps / 10_000) * profile.price_sensitivity
        - BETA_FATIGUE * prior_contacts * profile.fatigue_hazard
    )
    return _sigmoid(logit_p)


def _profile_by_customer(
    population: Population, signals: tuple[RawSignal, ...], ledger: Ledger
) -> dict[str, HiddenResponseProfile]:
    """customer_id -> HiddenResponseProfile, via each signal's own
    person_id and the ledger's risk_id -> customer_id resolution, keeping
    the first signal per customer in generation order — the same rule
    sim/ledger.py already applies when resolving a customer's group-level
    phone_hash / email_hash."""
    profile_by_person = {profile.person_id: profile for profile in population.hidden_response}
    result: dict[str, HiddenResponseProfile] = {}
    for signal in signals:
        customer_id = ledger.risk_customer_map[signal.signal_id]
        if customer_id not in result:
            result[customer_id] = profile_by_person[signal.person_id]
    return result


class Environment:
    """Owns the hidden-response join and the true cross-agent contact
    count. `observe` must be called once per action, in the run's fixed
    chronological replay order — prior_contacts depends on call order.
    Calling `observe` twice for the same risk_id raises
    DuplicateObservationError (see module docstring): recovery_unit is
    the RiskItem, so a risk item may be economically resolved at most
    once.
    """

    def __init__(
        self, profile_by_customer: Mapping[str, HiddenResponseProfile], rng: _RandomSource
    ) -> None:
        self._profile_by_customer = profile_by_customer
        self._rng = rng
        self._true_contacts: dict[str, int] = defaultdict(int)
        self._observed_risk_ids: set[str] = set()

    @classmethod
    def build(
        cls,
        population: Population,
        signals: tuple[RawSignal, ...],
        ledger: Ledger,
        seed: int,
    ) -> "Environment":
        rng = np.random.default_rng(np.random.SeedSequence(entropy=(seed, _RESPONSE_MODEL_SALT)))
        return cls(_profile_by_customer(population, signals, ledger), rng)

    def observe(self, action: ContactAction, risk_item: RiskItem) -> ContactOutcome:
        if risk_item.risk_id in self._observed_risk_ids:
            raise DuplicateObservationError(
                f"risk_id {risk_item.risk_id!r} was already observed — recovery_unit "
                "is the RiskItem, and a risk item may be economically resolved at "
                "most once"
            )
        self._observed_risk_ids.add(risk_item.risk_id)

        profile = self._profile_by_customer[action.customer_id]
        prior_contacts = self._true_contacts[action.customer_id]

        probability = p_recover(profile, action.incentive_bps, prior_contacts)
        recovered = bool(self._rng.random() < probability)

        self._true_contacts[action.customer_id] += 1

        # recovery_unit is the RiskItem (sim/metrics.py) — this is a
        # synthetic per-risk-item recovered value, not deduplicated
        # customer revenue and not yet corrected against a holdout
        # baseline (Phase 7 attribution, spec §8.9).
        amount_recovered_paise = risk_item.amount_paise if recovered else 0
        incentive_paise = (
            (amount_recovered_paise * action.incentive_bps) // 10_000 if recovered else 0
        )

        return ContactOutcome(
            outcome_id=f"{action.agent_id}:{action.risk_id}",
            agent_id=action.agent_id,
            customer_id=action.customer_id,
            risk_id=action.risk_id,
            channel=action.channel,
            incentive_bps=action.incentive_bps,
            contacted_at=action.scheduled_at,
            recovered=recovered,
            amount_recovered_paise=amount_recovered_paise,
            incentive_paise=incentive_paise,
        )
