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

--- Phase 7: world v2 (spec §8.9) ---

`Environment.build(..., world="v1")` is the default everywhere and is
BYTE-IDENTICAL to pre-Phase-7 behavior: `p_recover` and `observe`'s core
logic (this module's frozen lines) are untouched, and the two new RNG
namespaces below are never constructed at all under world="v1" — the
opt-out/natural-recovery code paths are gated off, not merely defaulted
to zero.

`world="v2"` adds two things, each independent of `self._rng`
(`_RESPONSE_MODEL_SALT = 991`, unchanged):

    1. An OPT-OUT draw inside `observe()` itself — spec §8.6's
       `Δ P(opt_out | contact_history + this_contact)` finally
       instantiated as a real, ContactOutcome-level label, drawn from
       `_OPTOUT_MODEL_SALT`'s own Generator. It never reads or perturbs
       `self._rng`, and it never changes `recovered` /
       `amount_recovered_paise` / `incentive_paise` — only the two new
       `ContactOutcome` fields (`agents/types.py`).
    2. `observe_natural()` — a SEPARATE method, called by the runner only
       for risk items that were NEVER contacted, strictly AFTER the
       contact stream for a run is complete, over the complement of the
       contacted set. It reads `_NATURAL_RECOVERY_SALT`'s own Generator
       and shares the SAME `_observed_risk_ids` exactly-once guard
       `observe()` already enforces, so a risk item can never receive
       both a contacted outcome and a natural outcome. Because it runs
       strictly after every admission/ranking/grant decision for the run
       has already been made, it cannot influence any of them — this is
       a property of WHEN it is called, not merely an intention.

Both new RNG streams are independent `numpy.random.Generator` instances,
seeded from `(seed, <their own salt>)`, exactly mirroring how
`_RESPONSE_MODEL_SALT` is already isolated from `sim/seeding.py`'s
`make_rngs` namespace.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Mapping, Protocol

import numpy as np

from agents.types import ContactAction, ContactOutcome
from sampark.contracts import RiskItem
from sim.generator import RawSignal
from sim.ledger import Ledger
from sim.natural import NaturalOutcome, p_natural
from sim.population import HiddenResponseProfile, Population

BETA_INCENTIVE = 4.0
BETA_FATIGUE = 1.0

_RESPONSE_MODEL_SALT = 991
_PROBABILITY_EPSILON = 1e-9

# --- Phase 7, world v2 only ------------------------------------------------

VALID_WORLDS = ("v1", "v2")
_NATURAL_RECOVERY_SALT = 7331
_OPTOUT_MODEL_SALT = 1523

# Owner-authored prior (Phase 7 design lock, Decision 10) — the same class
# of decision as BETA_INCENTIVE/BETA_FATIGUE above: a simulation
# coefficient, not calibrated against anything, chosen BEFORE any Phase 7
# evidence run and never re-tuned after observing one (Decision 17's
# precommitment discipline). Calibration target: a cumulative monthly
# opt-out rate plausible for a recovery-messaging programme, and enough
# positive labels to clear the fatigue-hazard model's MIN_POSITIVES_PER_BUCKET
# floor (sampark/models/fatigue_hazard.py) at the (contact_index) level —
# not the reverse. At the mean fatigue_hazard (Beta(2, 8), mean 0.2) and a
# first contact (prior_contacts=0), this yields p_optout ~= 0.06 * 0.2 * 1
# = 1.2%; at a high-fatigue customer's second contact (fatigue_hazard=0.8,
# prior_contacts=1), p_optout ~= 0.06 * 0.8 * 2 = 9.6%.
OPTOUT_BASE = 0.06
OPTOUT_MAX = 0.5


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


def p_recover(
    profile: HiddenResponseProfile,
    incentive_bps: int,
    prior_contacts: int,
    *,
    beta_fatigue: float = BETA_FATIGUE,
    beta_incentive: float = BETA_INCENTIVE,
) -> float:
    """Pure ground-truth recovery probability — no RNG, no state.

    Exposed at module level (rather than buried inside Environment.observe)
    so the model's required monotonicity properties (fatigue never helps;
    incentive never helps a less price-sensitive customer more than a
    more price-sensitive one) are directly testable without drawing a
    stochastic outcome.

    --- Phase 9: the two coefficients are parameters, not constants ---

    `beta_fatigue` / `beta_incentive` are KEYWORD-ONLY and default to this
    module's frozen `BETA_FATIGUE` / `BETA_INCENTIVE`, so every pre-Phase-9
    call site — which passes neither — is byte-identical. They exist because
    spec §11 requires a sensitivity sweep over the fatigue-hazard parameter,
    and this module's own docstring already named that sweep as the expected
    revisit of these two coefficients.

    Scaling `beta_fatigue` by k is exactly equivalent to scaling every
    customer's `fatigue_hazard` by k. It does NOT touch any RNG stream: the
    population draw is unchanged and `observe`'s response-model Generator
    makes the same number of draws in the same order. Only the threshold
    those draws are compared against moves. See `sim/sensitivity.py` for why
    that makes the sweep a pure re-observation.
    """
    logit_p = (
        _logit(profile.conversion_propensity)
        + beta_incentive * (incentive_bps / 10_000) * profile.price_sensitivity
        - beta_fatigue * prior_contacts * profile.fatigue_hazard
    )
    return _sigmoid(logit_p)


def p_optout(profile: HiddenResponseProfile, prior_contacts: int) -> float:
    """Pure ground-truth opt-out probability for the contact about to be
    made — no RNG, no state. `prior_contacts` is the SAME value `observe()`
    already computes for `p_recover` (contacts strictly before this one);
    `(1 + prior_contacts)` is "contacts so far including this one", matching
    spec §8.6's `Δ P(opt_out | contact_history + this_contact)`.

    Locked properties (Phase 7 design lock §7.4), each directly testable:
      - zero with no contact: this function is only ever called from
        inside `observe()`, i.e. only for a risk item that IS being
        contacted right now — it is never evaluated for an uncontacted
        item, so "no contact -> p_optout=0" holds by construction, not by
        a special-cased branch here.
      - monotone non-decreasing in prior_contacts (fatigue accumulates).
      - monotone non-decreasing in profile.fatigue_hazard (the existing
        hidden parameter means what it is named).
    """
    return min(OPTOUT_MAX, OPTOUT_BASE * profile.fatigue_hazard * (1 + prior_contacts))


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
        self,
        profile_by_customer: Mapping[str, HiddenResponseProfile],
        rng: _RandomSource,
        *,
        world: str = "v1",
        natural_rng: "_RandomSource | None" = None,
        optout_rng: "_RandomSource | None" = None,
        beta_fatigue: float = BETA_FATIGUE,
        beta_incentive: float = BETA_INCENTIVE,
    ) -> None:
        if world not in VALID_WORLDS:
            raise ValueError(f"world must be one of {VALID_WORLDS}, got {world!r}")
        if world == "v2" and (natural_rng is None or optout_rng is None):
            raise ValueError("world='v2' requires both natural_rng and optout_rng")
        self._profile_by_customer = profile_by_customer
        self._rng = rng
        self._world = world
        self._natural_rng = natural_rng
        self._optout_rng = optout_rng
        # Phase 9 (spec §11 sensitivity sweep). Default to the frozen
        # module constants, so every pre-Phase-9 construction is unchanged.
        self._beta_fatigue = beta_fatigue
        self._beta_incentive = beta_incentive
        self._true_contacts: dict[str, int] = defaultdict(int)
        self._observed_risk_ids: set[str] = set()

    @classmethod
    def build(
        cls,
        population: Population,
        signals: tuple[RawSignal, ...],
        ledger: Ledger,
        seed: int,
        *,
        world: str = "v1",
        beta_fatigue: float = BETA_FATIGUE,
        beta_incentive: float = BETA_INCENTIVE,
    ) -> "Environment":
        """`world="v1"` (default; every pre-Phase-7 call site) constructs
        ONLY the unchanged response-model RNG — `natural_rng`/`optout_rng`
        stay `None` and the two new RNG namespaces below are never even
        instantiated. `world="v2"` additionally constructs them, each from
        its own `(seed, salt)` `SeedSequence`, independent of
        `_RESPONSE_MODEL_SALT` and of `sim/seeding.py`'s `make_rngs`
        namespace — the same isolation pattern this classmethod already
        used for the response model before Phase 7."""
        rng = np.random.default_rng(np.random.SeedSequence(entropy=(seed, _RESPONSE_MODEL_SALT)))
        natural_rng = optout_rng = None
        if world == "v2":
            natural_rng = np.random.default_rng(np.random.SeedSequence(entropy=(seed, _NATURAL_RECOVERY_SALT)))
            optout_rng = np.random.default_rng(np.random.SeedSequence(entropy=(seed, _OPTOUT_MODEL_SALT)))
        return cls(
            _profile_by_customer(population, signals, ledger),
            rng,
            world=world,
            natural_rng=natural_rng,
            optout_rng=optout_rng,
            beta_fatigue=beta_fatigue,
            beta_incentive=beta_incentive,
        )

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

        probability = p_recover(
            profile,
            action.incentive_bps,
            prior_contacts,
            beta_fatigue=self._beta_fatigue,
            beta_incentive=self._beta_incentive,
        )
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

        # Phase 7, world v2 only. self._rng (the line above) has already
        # made its ONE draw for this call, exactly as before Phase 7 —
        # this block reads a SEPARATE Generator and therefore cannot
        # perturb that draw's value or the sequence of future draws from
        # self._rng. Under world="v1" self._optout_rng is None and this
        # block never executes.
        opt_out = False
        opt_out_channel: str | None = None
        if self._world == "v2":
            assert self._optout_rng is not None  # guaranteed by __init__'s world="v2" check
            p_out = p_optout(profile, prior_contacts)
            if bool(self._optout_rng.random() < p_out):
                opt_out = True
                opt_out_channel = action.channel

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
            opt_out=opt_out,
            opt_out_channel=opt_out_channel,
        )

    def observe_natural(self, risk_item: RiskItem, customer_id: str, observed_at: datetime) -> NaturalOutcome:
        """Resolves ONE risk item that was NEVER contacted, under world v2
        only. Callers (the runner, never this class) must call this ONLY
        after every admission/ranking/grant decision for the run is
        already final, and ONLY for risk_ids in the complement of the
        contacted set — this method enforces the SAME exactly-once guard
        `observe()` uses (`_observed_risk_ids`), so a risk item that was
        somehow passed to both raises `DuplicateObservationError` rather
        than silently producing two outcomes for one item.

        `observed_at` is supplied by the caller (never a wall clock —
        Phase 7 design lock's determinism rule) — typically the run's
        observation-window horizon end, since natural recovery has no
        instantaneous decision moment the way a contact does.
        """
        if self._world != "v2":
            raise RuntimeError("observe_natural requires world='v2'")
        assert self._natural_rng is not None  # guaranteed by __init__'s world="v2" check

        if risk_item.risk_id in self._observed_risk_ids:
            raise DuplicateObservationError(
                f"risk_id {risk_item.risk_id!r} was already observed — recovery_unit "
                "is the RiskItem, and a risk item may be economically resolved at "
                "most once (contacted XOR natural, never both)"
            )
        self._observed_risk_ids.add(risk_item.risk_id)

        profile = self._profile_by_customer[customer_id]
        probability = p_natural(profile, risk_item.root_cause)
        recovered = bool(self._natural_rng.random() < probability)
        amount_recovered_paise = risk_item.amount_paise if recovered else 0

        return NaturalOutcome(
            risk_id=risk_item.risk_id,
            customer_id=customer_id,
            source=risk_item.source,
            root_cause=risk_item.root_cause,
            amount_paise=risk_item.amount_paise,
            p_natural=probability,
            recovered=recovered,
            amount_recovered_paise=amount_recovered_paise,
            observed_at=observed_at,
        )
