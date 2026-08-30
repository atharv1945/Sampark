"""Deterministic PolicyIR validation — Phase 7 (spec §8.4).

NO LLM dependency. Every check here is a pure function of the `PolicyRule`
itself plus committed constants — grammar (already enforced by
`ir.parse_ir`), bounds, fact-availability, and conflict-against-existing-
regulation. Called on EVERY `PolicyRule`, whether it came from the LLM
(an untrusted proposal) or a hand-authored golden-corpus fixture — there
is exactly one validation code path, never two.

**The single most important rule here**: a `condition` citing an
UNAVAILABLE fact (`ir.FACT_AVAILABILITY[fact] is False`) does not fail
validation — it compiles to a `FACT_UNAVAILABLE` rule (Phase 7 design
lock §8.4), mirroring `sampark/policy/hard/consent_scope.py`'s exact
precedent: read the fact, refuse to interpret an absent signal, never
silently admit or silently deny. Two of spec §8.4's own four example
sentences (the 90-day chargeback rule, the RTO Shield rule) hit this
path — this is not a hypothetical edge case.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sampark.policy.compiler.ir import (
    FACT_AVAILABILITY,
    VALID_CHANNELS,
    VALID_INTENTS,
    VALID_WINDOWS,
    PolicyRule,
    PredicateFamily,
)

# Mirrors sampark/allocator/constants.py's frozen caps exactly — a
# compiled contact_frequency_cap rule that is EQUAL TO OR LOOSER than
# the existing system cap is redundant (never binds, since the stricter
# existing rule always fires first in the ordered chain) and is rejected
# as a conflict rather than silently accepted as a no-op. A STRICTER
# compiled cap is a legitimate additional restriction and is allowed —
# compiled rules can only ADD restrictions, never loosen an existing one
# (the grammar has no "permit" verb — Phase 7 design lock §8.4).
_EXISTING_CONTACT_CAPS = {"24h": 1, "7d": 2}


class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    FACT_UNAVAILABLE = "FACT_UNAVAILABLE"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ValidationResult:
    verdict: Verdict
    rule: PolicyRule
    reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPTED

    @property
    def fact_unavailable(self) -> bool:
        return self.verdict is Verdict.FACT_UNAVAILABLE


def _accept(rule: PolicyRule) -> ValidationResult:
    return ValidationResult(verdict=Verdict.ACCEPTED, rule=rule)


def _fact_unavailable(rule: PolicyRule, reason: str) -> ValidationResult:
    return ValidationResult(verdict=Verdict.FACT_UNAVAILABLE, rule=rule, reasons=(reason,))


def _reject(rule: PolicyRule, *reasons: str) -> ValidationResult:
    return ValidationResult(verdict=Verdict.REJECTED, rule=rule, reasons=reasons)


def _validate_bounds(rule: PolicyRule) -> tuple[str, ...]:
    """Bounds/enum checks, per family. Returns a tuple of violation
    reasons — empty if none."""
    reasons: list[str] = []
    p = rule.params

    if rule.family is PredicateFamily.CONTACT_FREQUENCY_CAP:
        window = p.get("window")
        max_contacts = p.get("max_contacts")
        if window not in VALID_WINDOWS:
            reasons.append(f"window must be one of {sorted(VALID_WINDOWS)}, got {window!r}")
        if not isinstance(max_contacts, int) or max_contacts < 0:
            reasons.append(f"max_contacts must be a non-negative int, got {max_contacts!r}")
        channel = p.get("channel")
        if channel is not None and channel not in VALID_CHANNELS:
            reasons.append(f"channel must be one of {sorted(VALID_CHANNELS)} or omitted, got {channel!r}")

    elif rule.family is PredicateFamily.TIME_OF_DAY_WINDOW:
        channel = p.get("channel")
        if channel not in VALID_CHANNELS:
            reasons.append(f"channel must be one of {sorted(VALID_CHANNELS)}, got {channel!r}")
        forbidden_before = p.get("forbidden_before")
        forbidden_after = p.get("forbidden_after")
        if forbidden_before is None and forbidden_after is None:
            reasons.append("time_of_day_window requires forbidden_before and/or forbidden_after")
        for key, value in (("forbidden_before", forbidden_before), ("forbidden_after", forbidden_after)):
            if value is not None and not _is_valid_hhmm(value):
                reasons.append(f"{key} must be HH:MM (00:00-23:59), got {value!r}")
        tz = p.get("tz", "Asia/Kolkata")
        if tz != "Asia/Kolkata":
            reasons.append(f"tz must be 'Asia/Kolkata' (the only zone this system's clock reasoning supports), got {tz!r}")

    elif rule.family is PredicateFamily.INTENT_SUPPRESSION:
        intent = p.get("intent")
        if intent != "*" and intent not in VALID_INTENTS:
            reasons.append(f"intent must be '*' or one of {sorted(VALID_INTENTS)}, got {intent!r}")

    elif rule.family is PredicateFamily.CHANNEL_RESTRICTION:
        channel = p.get("channel")
        if channel not in VALID_CHANNELS:
            reasons.append(f"channel must be one of {sorted(VALID_CHANNELS)}, got {channel!r}")

    elif rule.family is PredicateFamily.INCENTIVE_PROHIBITION:
        pass  # bounds live entirely in the condition, checked separately

    return tuple(reasons)


def _is_valid_hhmm(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hh, mm = value[:2], value[3:]
    if not (hh.isdigit() and mm.isdigit()):
        return False
    return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


def _validate_conflict(rule: PolicyRule) -> tuple[str, ...]:
    """Rejects a compiled rule that would be REDUNDANT against an
    existing, stricter hard-policy rule — the grammar has no "permit"
    verb, so a compiled rule can never actually LOOSEN a regulation; a
    rule that merely restates an existing cap at the same or looser
    threshold is pointless (it never binds, since the stricter existing
    rule fires first in the ordered chain) and is rejected rather than
    silently accepted as inert."""
    if rule.family is not PredicateFamily.CONTACT_FREQUENCY_CAP:
        return ()
    window = rule.params.get("window")
    max_contacts = rule.params.get("max_contacts")
    existing = _EXISTING_CONTACT_CAPS.get(window)
    if existing is not None and isinstance(max_contacts, int) and max_contacts >= existing:
        return (
            f"contact_frequency_cap(window={window!r}, max_contacts={max_contacts!r}) is redundant: "
            f"the existing system cap for {window!r} is already {existing!r} (sampark/allocator/constants.py) "
            f"and would always deny first — a compiled rule must be STRICTER than the existing cap to have "
            f"any effect, since compiled rules can only add restrictions",
        )
    return ()


def validate(rule: PolicyRule) -> ValidationResult:
    """The one entry point. Order: fact-availability FIRST (a condition
    citing an unavailable fact short-circuits straight to
    FACT_UNAVAILABLE — it is never ALSO checked for bounds/conflict,
    since those checks are meaningless against a fact this system cannot
    read), then bounds, then conflict."""
    if rule.condition is not None and not FACT_AVAILABILITY[rule.condition.fact]:
        return _fact_unavailable(
            rule,
            f"condition references {rule.condition.fact.value!r}, which this system cannot read "
            "(mirrors sampark/policy/hard/interlocks.py's / consent_scope.py's own documented gap) — "
            "compiled to FACT_UNAVAILABLE rather than guessed",
        )

    bounds_reasons = _validate_bounds(rule)
    if bounds_reasons:
        return _reject(rule, *bounds_reasons)

    conflict_reasons = _validate_conflict(rule)
    if conflict_reasons:
        return _reject(rule, *conflict_reasons)

    return _accept(rule)
