"""Hard-policy filtering — Design Lock §5.1 / §8 hardening (W3).

The approved architecture is a one-way pipeline:

    hard policy evaluation -> admissible candidate set -> allocator

Before this module existed, `sampark.allocator.greedy.allocate_window`
called `sampark.policy.hard.evaluate_all` itself, which meant the
allocator held a direct dependency on `Verdict`, `HardVerdict`,
`PolicyContext`, and (transitively) every hard-policy evaluator
(opt-out, consent-scope, DLT template, all six interlocks, quiet hours,
contact cap) — the exact "second opinion on admissibility" CLAUDE.md §6
and the Design Lock §8 header comment say the allocator must never have.

This module is the ONLY place outside `sampark.policy.hard` itself that
imports `Verdict`/`HardVerdict`/`PolicyContext`/`evaluate_all` on the
Phase 4 decision path. `sampark.allocator.greedy` imports neither this
module nor `sampark.policy.hard` — verified by
`tests/allocator/test_structural_boundaries.py`, which now inspects
import aliases and actual call sites, not just raw module names.

`filter_candidates` partitions a raw candidate tuple into:

- `survivors` — hard-ADMISSIBLE candidates only, in the same
  (customer_id, risk_id)-sorted order `sampark.allocator.greedy` already
  used internally before this refactor, so moving hard-filtering here
  does not change which candidates compete against which, or in what
  order, inside `allocate_window`.
- `immediate_outcomes` — a DENIED or DEFERRED `AllocationOutcome` for
  every hard-INADMISSIBLE candidate, built with
  `sampark.allocator.outcomes.deferred_or_denied` (aging/exhaustion is
  the allocator's fairness responsibility, Design Lock §7, applied
  identically here as it is to an allocator-side competitive loss).
- `fact_unavailable_by_risk_id` — every FACT_UNAVAILABLE reason code
  `evaluate_all` recorded for a SURVIVING candidate (Design Lock §4.2:
  FACT_UNAVAILABLE never short-circuits, so a candidate can be
  hard-ADMISSIBLE and still carry recorded gaps). The allocator needs
  these strings only to attach them to a NEGATIVE_EXPECTED_NET denial's
  `AllocationOutcome.fact_unavailable_reason_codes` field — it receives
  them as a plain `dict[str, tuple[str, ...]]`, never as a HardVerdict
  or an evaluator it could call itself.

Outcome ORDER note: only `allocate_window`'s own outputs can ever be
GRANTED (hard-filtering only denies or defers), and `filter_candidates`
preserves the exact per-customer, risk-id-sorted admitted-candidate
order the allocator always used — so the relative order of GRANTED
decisions across a window, and therefore `sim/arm_b.py`'s
`Environment.observe` call sequence, is UNCHANGED by this refactor.
"""

from __future__ import annotations

import collections

from datetime import datetime

from sampark.allocator.candidate import Candidate
from sampark.allocator.greedy import allocate_window
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind, deferred_or_denied
from sampark.policy import hard as hard_policy
from sampark.policy.types import MediationLedgerView, PolicyContext, Verdict


def filter_candidates(
    candidates: tuple[Candidate, ...],
    ledger: MediationLedgerView,
    decision_at: datetime,
) -> tuple[tuple[Candidate, ...], tuple[AllocationOutcome, ...], dict[str, tuple[str, ...]]]:
    by_customer: dict[str, list[Candidate]] = collections.defaultdict(list)
    for candidate in candidates:
        by_customer[candidate.customer_id].append(candidate)

    survivors: list[Candidate] = []
    immediate_outcomes: list[AllocationOutcome] = []
    fact_unavailable_by_risk_id: dict[str, tuple[str, ...]] = {}

    for customer_id in sorted(by_customer):
        cands = sorted(by_customer[customer_id], key=lambda c: c.risk_item.risk_id)
        ctx = PolicyContext(ledger=ledger, decision_at=decision_at)

        for candidate in cands:
            filter_result = hard_policy.evaluate_all(candidate, ctx)
            verdict = filter_result.verdict

            if verdict.verdict is Verdict.INADMISSIBLE:
                if verdict.is_deny:
                    ledger.mark_terminally_denied(candidate.risk_item.risk_id)
                    immediate_outcomes.append(
                        AllocationOutcome(
                            candidate=candidate,
                            outcome_kind=OutcomeKind.DENIED,
                            reason_code=verdict.reason_code,
                            next_eligible_at=None,
                            grant=None,
                            fact_unavailable_reason_codes=filter_result.fact_unavailable_reason_codes,
                            score=None,
                            rescheduled_candidate=None,
                        )
                    )
                else:
                    assert verdict.next_eligible_at is not None
                    immediate_outcomes.append(
                        deferred_or_denied(
                            candidate, verdict.reason_code, verdict.next_eligible_at, ledger,
                            fact_unavailable_reason_codes=filter_result.fact_unavailable_reason_codes,
                        )
                    )
                continue

            # ADMISSIBLE — survives to the allocator. FACT_UNAVAILABLE
            # gaps recorded along the way (Design Lock §4.2) are handed
            # over as plain strings, keyed by risk_id.
            fact_unavailable_by_risk_id[candidate.risk_item.risk_id] = filter_result.fact_unavailable_reason_codes
            survivors.append(candidate)

    return tuple(survivors), tuple(immediate_outcomes), fact_unavailable_by_risk_id


def filter_and_allocate(
    candidates: tuple[Candidate, ...],
    ledger: MediationLedgerView,
    issuer,
    decision_at: datetime,
    aging_bonus_paise: int,
    conn=None,
    fifo_mode: bool = False,
    *,
    run_seed_risk_ids: frozenset[str] | None = None,
) -> tuple[AllocationOutcome, ...]:
    """The full Design Lock §5.1/§8 pipeline in one call: hard policy
    evaluation -> admissible candidate set -> allocator. This is what
    `sampark.mediation.service.mediate_window` calls, and what any test
    exercising the FULL pipeline (hard-filter behavior together with
    allocation) should call — `sampark.allocator.greedy.allocate_window`
    on its own now assumes its input is already hard-admissible and
    performs no hard-policy evaluation itself.

    `run_seed_risk_ids` — Phase 4C hardening, W5. Threaded straight
    through to `allocate_window` -> `issuer.issue_grant`; see that
    function's docstring for the `None`-default rationale."""
    survivors, immediate_outcomes, fact_unavailable_by_risk_id = filter_candidates(candidates, ledger, decision_at)
    allocator_outcomes = allocate_window(
        survivors, ledger, issuer, decision_at, aging_bonus_paise, conn=conn, fifo_mode=fifo_mode,
        run_seed_risk_ids=run_seed_risk_ids, fact_unavailable_by_risk_id=fact_unavailable_by_risk_id,
    )
    return immediate_outcomes + allocator_outcomes
