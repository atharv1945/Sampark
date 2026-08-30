"""English -> PolicyIR -> deterministic compiled rule — Phase 7 (spec §8.4).

    English policy text
        -> (LLM, ONE call, offline, developer-time — llm.py)
        -> PolicyIR (ir.py) — an UNTRUSTED PROPOSAL
        -> validate() (validate.py) — DETERMINISTIC, no LLM
        -> generate_rule_function() / generate_rule_source() /
           generate_test_source() (generate.py) — DETERMINISTIC TEMPLATES
        -> render_english() (render.py) — DETERMINISTIC, for owner sign-off
        -> policies/activated.yaml (owner-reviewed)
        -> sampark.policy.compiled composes activated rules into
           HARD_RULES, appended after the 11 hand-written ones

ZERO LLM dependency on any runtime (allocator/mediation) path — the LLM
is confined to `llm.py`, called only by the offline `--compile` CLI
command, never imported by anything under `sampark/policy/compiled/` or
`sampark/mediation/`.
"""

from __future__ import annotations

from sampark.policy.compiler.ir import FactRef, IRParseError, PolicyRule, PredicateFamily, parse_ir
from sampark.policy.compiler.validate import ValidationResult, Verdict, validate

__all__ = [
    "FactRef",
    "IRParseError",
    "PolicyRule",
    "PredicateFamily",
    "parse_ir",
    "ValidationResult",
    "Verdict",
    "validate",
]
