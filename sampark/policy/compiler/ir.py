"""PolicyIR — Phase 7 (spec §8.4). A closed grammar, five predicate
families, each mapping onto a fact `sampark.policy.types.MediationLedgerView`
already exposes (or explicitly does not — see FACT_AVAILABILITY below).

This module has NO LLM dependency and NO I/O — it is the data shape the
LLM's output (an untrusted proposal) must conform to, and the shape every
deterministic downstream stage (validate.py, generate.py, render.py)
operates on. A PolicyIR that does not parse into this shape is rejected
before it ever reaches validation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PredicateFamily(str, Enum):
    CONTACT_FREQUENCY_CAP = "contact_frequency_cap"
    TIME_OF_DAY_WINDOW = "time_of_day_window"
    INCENTIVE_PROHIBITION = "incentive_prohibition"
    INTENT_SUPPRESSION = "intent_suppression"
    CHANNEL_RESTRICTION = "channel_restriction"


class FactRef(str, Enum):
    """The closed set of fact references a `condition` may cite. Each
    declares its availability against the SAME facts
    `sampark.policy.hard.*` already reads — never a new fact this
    codebase does not have. Two of these (CHARGEBACK_90D, RTO_FLAGGED)
    are DELIBERATELY unavailable: they mirror the exact gap
    `sampark/policy/hard/interlocks.py` already reports as
    FACT_UNAVAILABLE, and the compiler must reach the same conclusion by
    the same reasoning (Phase 7 design lock §8.4)."""

    CONTACTS_24H = "contacts_24h"  # available — rolling_contact_counts
    CONTACTS_7D = "contacts_7d"  # available — rolling_contact_counts
    CHARGEBACK_90D = "chargeback_90d"  # UNAVAILABLE — mirrors interlocks.py's dispute/chargeback gap
    RTO_FLAGGED = "rto_flagged"  # UNAVAILABLE — mirrors interlocks.py's rto_flag gap
    CONSENT_SCOPE = "consent_scope"  # UNAVAILABLE — mirrors consent_scope.py exactly


# Facts this codebase genuinely has, vs facts it does not (mirrors the
# EXACT gaps sampark/policy/hard/interlocks.py and consent_scope.py
# already report — never re-decided here).
FACT_AVAILABILITY: dict[FactRef, bool] = {
    FactRef.CONTACTS_24H: True,
    FactRef.CONTACTS_7D: True,
    FactRef.CHARGEBACK_90D: False,
    FactRef.RTO_FLAGGED: False,
    FactRef.CONSENT_SCOPE: False,
}

VALID_CHANNELS = frozenset({"sms", "whatsapp", "voice"})
VALID_INTENTS = frozenset({"payment_retry", "cart_recovery", "mandate_retry", "receivables_followup"})
VALID_WINDOWS = frozenset({"24h", "7d"})


@dataclass(frozen=True)
class Condition:
    """A single fact reference plus an operator, used by
    incentive_prohibition / intent_suppression / channel_restriction to
    express "if <fact> <op> <value>". `fact` must be a `FactRef` member —
    a condition citing anything else is a grammar violation, not a
    fact-unavailability finding (those are different rejection reasons)."""

    fact: FactRef
    op: str  # "eq" | "gt" | "exists"
    value: Any = None


@dataclass(frozen=True)
class PolicyRule:
    """ONE proposed rule — the LLM's untrusted output, or a hand-authored
    golden-corpus fixture, in EITHER case validated identically by
    validate.py before anything downstream trusts it.

    `source_text` is the original English sentence, kept through the
    whole pipeline so back-rendering (render.py) can be displayed
    alongside it for owner sign-off (Phase 7 design lock §8.6)."""

    rule_id: str
    family: PredicateFamily
    params: dict[str, Any] = field(default_factory=dict)
    condition: Condition | None = None
    source_text: str = ""


class IRParseError(ValueError):
    """The raw dict does not even parse into a PolicyRule shape — a
    grammar violation caught before validate.py's semantic checks run."""


def parse_ir(raw: dict[str, Any]) -> PolicyRule:
    """Parses a raw dict (e.g. from json.loads of a committed
    policies/ir/<id>.json, or an LLM response) into a `PolicyRule`.
    Raises `IRParseError` for anything structurally malformed — an
    unknown top-level key, a family outside `PredicateFamily`, a
    condition whose `fact` is outside `FactRef`. This is the FIRST gate,
    strictly narrower than validate.py's semantic checks."""
    try:
        rule_id = raw["rule_id"]
        family = PredicateFamily(raw["family"])
        params = dict(raw.get("params", {}))
        source_text = raw.get("source_text", "")
        condition_raw = raw.get("condition")
    except (KeyError, ValueError) as exc:
        raise IRParseError(f"malformed PolicyIR: {exc}") from exc

    condition: Condition | None = None
    if condition_raw is not None:
        try:
            condition = Condition(
                fact=FactRef(condition_raw["fact"]),
                op=condition_raw["op"],
                value=condition_raw.get("value"),
            )
        except (KeyError, ValueError) as exc:
            raise IRParseError(f"malformed condition: {exc}") from exc

    if not isinstance(rule_id, str) or not rule_id:
        raise IRParseError(f"rule_id must be a non-empty string, got {rule_id!r}")

    return PolicyRule(rule_id=rule_id, family=family, params=params, condition=condition, source_text=source_text)
