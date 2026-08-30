"""Deterministic back-rendering — IR to canonical English, Phase 7 (spec
§8.4 / design lock §8.6).

Together with the golden corpus (tests/policy/compiler/golden/), this is
what closes the "does the IR actually capture the English" link the
generated pytest CANNOT close (generate.py's own docstring). A human
compares `source_text` (the original sentence) against this function's
output (the canonical rendering of what was actually compiled) before
the rule enters `policies/activated.yaml`.

Templates only — no LLM call.
"""

from __future__ import annotations

from sampark.policy.compiler.ir import PolicyRule, PredicateFamily
from sampark.policy.compiler.validate import ValidationResult


def render_english(result: ValidationResult) -> str:
    """Renders whatever the IR actually says — including a FACT_UNAVAILABLE
    result, so the owner sees "this could not be enforced" rendered in
    English too, not just a code."""
    rule = result.rule
    p = rule.params

    if result.fact_unavailable:
        fact = rule.condition.fact.value if rule.condition else "an unknown fact"
        return f"[CANNOT BE ENFORCED — {fact} is not available to this system] {rule.source_text!r}"

    if result.verdict.value == "REJECTED":
        return f"[REJECTED: {'; '.join(result.reasons)}] {rule.source_text!r}"

    if rule.family is PredicateFamily.CONTACT_FREQUENCY_CAP:
        scope = f" on {p['channel']}" if p.get("channel") else ""
        window_label = "24 hours" if p["window"] == "24h" else "7 days"
        return f"Never contact a customer more than {p['max_contacts']} time(s) in {window_label}{scope}."

    if rule.family is PredicateFamily.TIME_OF_DAY_WINDOW:
        parts = []
        if p.get("forbidden_before"):
            parts.append(f"before {p['forbidden_before']}")
        if p.get("forbidden_after"):
            parts.append(f"after {p['forbidden_after']}")
        window_text = " or ".join(parts)
        return f"No {p['channel']} contact {window_text} ({p.get('tz', 'Asia/Kolkata')})."

    if rule.family is PredicateFamily.INCENTIVE_PROHIBITION:
        return "Never offer a discount-bearing incentive on this candidate."

    if rule.family is PredicateFamily.INTENT_SUPPRESSION:
        intent_text = "any recovery intent" if p["intent"] == "*" else p["intent"]
        return f"Stop all recovery contact for intent {intent_text!r}."

    if rule.family is PredicateFamily.CHANNEL_RESTRICTION:
        return f"Never contact a customer on {p['channel']}."

    raise ValueError(f"no renderer for family {rule.family!r}")  # unreachable — PredicateFamily is closed
