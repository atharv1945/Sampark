"""Ordered hard-filter chain — Design Lock §5.1.

Ordered by DESCENDING PERMANENCE, so a permanently barred candidate
reports that rather than a transient cap:

    1. opt_out                          DENY, permanent
    2. consent_scope                    DENY (always FACT_UNAVAILABLE today)
    3. dlt_template                     DENY
    4. interlock.dispute_open           DENY
    5. interlock.rto_flag               FACT_UNAVAILABLE only
    6. interlock.refund_in_flight       FACT_UNAVAILABLE only
    7. interlock.fraud_review           FACT_UNAVAILABLE only
    8. interlock.mandate_cancellation   FACT_UNAVAILABLE only
    9. interlock.active_grant_in_window DEFER
   10. quiet_hours                      DEFER (hours)
   11. contact_cap                      DEFER (days)

First non-ADMISSIBLE (i.e. INADMISSIBLE) verdict wins and short-circuits
the remaining rules. FACT_UNAVAILABLE does NOT short-circuit — it is
recorded and evaluation continues to the next rule.

This module must never import sampark.policy.soft or sampark.allocator.scoring
— tests/allocator/test_structural_boundaries.py asserts it via AST.
sampark.allocator.candidate and sampark.allocator.reason_codes are data/
constant modules, not scoring, and are fine to import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sampark.allocator.candidate import Candidate
from sampark.policy.hard import contact_cap, consent_scope, dlt_template, interlocks, opt_out, quiet_hours
from sampark.policy.types import HardVerdict, PolicyContext, Verdict

HardRule = Callable[[Candidate, PolicyContext], HardVerdict]

HARD_RULES: tuple[tuple[str, HardRule], ...] = (
    ("opt_out", opt_out.evaluate),
    ("consent_scope", consent_scope.evaluate),
    ("dlt_template", dlt_template.evaluate),
    ("interlock.dispute_open", interlocks.evaluate_dispute_open),
    ("interlock.rto_flag", interlocks.evaluate_rto_flag),
    ("interlock.refund_in_flight", interlocks.evaluate_refund_in_flight),
    ("interlock.fraud_review", interlocks.evaluate_fraud_review),
    ("interlock.mandate_cancellation", interlocks.evaluate_mandate_cancellation),
    ("interlock.active_grant_in_window", interlocks.evaluate_active_grant_in_window),
    ("quiet_hours", quiet_hours.evaluate),
    ("contact_cap", contact_cap.evaluate),
)


@dataclass(frozen=True)
class HardFilterResult:
    verdict: HardVerdict  # ADMISSIBLE, or the first INADMISSIBLE verdict encountered
    fact_unavailable_reason_codes: tuple[str, ...]  # every FACT_UNAVAILABLE seen, in rule order


def evaluate_all(candidate: Candidate, ctx: PolicyContext) -> HardFilterResult:
    fact_unavailable: list[str] = []
    for _name, rule in HARD_RULES:
        verdict = rule(candidate, ctx)
        if verdict.verdict is Verdict.FACT_UNAVAILABLE:
            assert verdict.reason_code is not None
            fact_unavailable.append(verdict.reason_code)
            continue
        if verdict.verdict is Verdict.INADMISSIBLE:
            return HardFilterResult(
                verdict=verdict, fact_unavailable_reason_codes=tuple(fact_unavailable)
            )
    return HardFilterResult(
        verdict=HardVerdict.admissible(), fact_unavailable_reason_codes=tuple(fact_unavailable)
    )
