"""Budgeted greedy allocator — Design Lock §8.

Processes ONE window's ALREADY-HARD-FILTERED candidate set (arrivals +
carried-forward deferrals — the caller, sim/arm_b.py, drives the
day-by-day loop and owns carrying deferred candidates into their next
window). Within a window: partition by customer, score, admit, rank,
attempt issuance with fallback, then defer/deny everyone else.

Phase 4C hardening (W3): this module has NO dependency on
`sampark.policy.hard` — no `Verdict`, no `HardVerdict`, no
`PolicyContext`, no evaluator (opt-out/consent/DLT/interlocks/quiet
hours/contact cap). Hard-policy filtering happens strictly BEFORE this
module runs, in `sampark.mediation.hard_filter.filter_candidates`
(Design Lock §5.1's pipeline: hard policy evaluation -> admissible
candidate set -> allocator). `allocate_window` receives only candidates
already proven hard-ADMISSIBLE, plus a plain
`dict[str, tuple[str, ...]]` of FACT_UNAVAILABLE reason-code strings
keyed by risk_id (data, not evaluator access) for attaching to a
NEGATIVE_EXPECTED_NET denial.

`tests/allocator/test_structural_boundaries.py::test_allocator_never_imports_policy_hard`
inspects import aliases and actual call sites (not just raw module
names) to keep this boundary from silently regressing behind an alias.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from sampark.allocator import scoring
from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import AGING_BONUS_PAISE
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind, deferred_or_denied
from sampark.allocator.reason_codes import (
    CUSTOMER_MARGIN_EXHAUSTED,
    LOST_TO_HIGHER_EXPECTED_NET,
    MERCHANT_MARGIN_EXHAUSTED,
    NEGATIVE_EXPECTED_NET,
)
from sampark.budget.margin import downgrade_to_fit
from sampark.budget.store import BudgetDenial, GrantIssued, GrantIssuer, InMemoryMediationLedger
from sampark.budget.windows import next_window_start
from sampark.contracts import Grant

# Re-exported for backward compatibility — every existing call site that
# does `from sampark.allocator.greedy import OutcomeKind, AllocationOutcome`
# keeps working; the canonical definitions now live in
# sampark.allocator.outcomes (shared with sampark.mediation.hard_filter).
__all__ = ["OutcomeKind", "AllocationOutcome", "allocate_window"]


def _tie_break_key(item: tuple[float, Candidate], aging_bonus_paise: int) -> tuple:
    score_val, candidate = item
    priority = scoring.priority(score_val, candidate.windows_deferred, aging_bonus_paise)
    return (
        -priority,
        -candidate.risk_item.amount_paise,
        candidate.risk_item.detected_at,
        candidate.risk_item.risk_id,
        candidate.request.agent_id,
    )


def _fifo_key(item: tuple[float, Candidate]) -> tuple:
    """Ablation C (Design Lock §14.4, Phase 4C-2 Blocker 2): chronological
    order only — no expected_net, no priority, no aging bonus. Isolates
    "throttling" (hard caps, still fully enforced upstream in the hard
    filter chain) from "allocation" (value-based ranking, replaced here
    with pure arrival order). `detected_at` is the RiskItem's fixed
    original detection time, unaffected by how many times a candidate
    has been re-deferred, so this needs no separate aging term.

    Deliberately value-blind, and BY DESIGN this bypasses the
    `expected_net > 0` admission step below (see `allocate_window`'s
    fifo_mode branch) — FIFO-under-cap isolates the value-AWARE
    allocator (headline ranking + admission) from pure chronological
    throttling under the SAME hard caps; it is not "headline ranking
    with a different sort key," it is "no value judgement at all,
    admit-if-capacity-allows." Design Lock §14.4's ablation exists
    specifically to measure how much of Arm B's uplift comes from
    THROTTLING (contact caps, quiet hours — still fully enforced) versus
    from VALUE-BASED SELECTION (which candidate wins a contested slot)."""
    _score_val, candidate = item
    return (candidate.risk_item.detected_at, candidate.risk_item.risk_id, candidate.request.agent_id)


def allocate_window(
    candidates: tuple[Candidate, ...],
    ledger: InMemoryMediationLedger,
    issuer: GrantIssuer,
    decision_at: datetime,
    aging_bonus_paise: int = AGING_BONUS_PAISE,
    conn: Any = None,
    fifo_mode: bool = False,
    *,
    run_seed_risk_ids: frozenset[str] | None = None,
    fact_unavailable_by_risk_id: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[AllocationOutcome, ...]:
    """`candidates` must already be hard-ADMISSIBLE — this function does
    not hard-filter (see module docstring). Callers that need the full
    hard-filter + allocate pipeline should go through
    `sampark.mediation.service.mediate_window` (production) or
    `sampark.mediation.hard_filter.filter_candidates` followed by this
    function (tests exercising the full pipeline explicitly).

    `run_seed_risk_ids` — Phase 4C hardening, W5. Passed straight through
    to `issuer.issue_grant`; this module does not use it directly.
    `InMemoryGrantIssuer` ignores it (its ledger is already correctly
    scoped by construction); `PostgresGrantIssuer`/
    `sampark.budget.issuance.issue_grant` use it to scope the
    authoritative customer-margin-budget query, falling back to the
    pre-W5 unscoped query when `None` — the default exists ONLY so this
    parameter's addition does not break callers unaware of it (notably
    the human-owned `tests/test_concurrent_grant_issuance.py`, which
    must not be modified); every production caller (`sim/arm_b.py`)
    passes the real set explicitly.

    `fact_unavailable_by_risk_id` — optional, `sampark.mediation.hard_filter`'s
    output for SURVIVING candidates (Design Lock §4.2: a candidate can be
    hard-ADMISSIBLE and still carry recorded FACT_UNAVAILABLE gaps).
    Attached only to a NEGATIVE_EXPECTED_NET denial's
    `AllocationOutcome.fact_unavailable_reason_codes`, matching this
    module's behavior before the W3 refactor. Defaults to "no gaps
    recorded" for callers (mostly tests) that construct candidates
    directly without running them through `filter_candidates`.

    `conn` defaults to `ledger` itself (the in-memory reference's
    "conn" IS the ledger — see sampark/budget/store.py). A real
    deployment would pass a psycopg.Connection distinct from the
    read-only ledger view.

    `fifo_mode` (Phase 4C-2 Blocker 2, ablation C): admits every
    candidate regardless of expected_net's sign, and ranks admitted
    candidates by arrival order (`_fifo_key`) instead of by
    `priority`/`_tie_break_key`. Margin downgrade-viability and issuance
    fallback are UNCHANGED — only which candidates are admitted and how
    they are ranked differs. See `_fifo_key`'s docstring for why this is
    a deliberate, documented departure from the headline admission rule."""
    if conn is None:
        conn = ledger
    if fact_unavailable_by_risk_id is None:
        fact_unavailable_by_risk_id = {}

    by_customer: dict[str, list[Candidate]] = collections.defaultdict(list)
    for candidate in candidates:
        by_customer[candidate.customer_id].append(candidate)

    outcomes: list[AllocationOutcome] = []
    # Phase 5 U-3 (data-threading only): the ScoreBreakdown each admitted
    # candidate was ALREADY scored with below, keyed by risk_id so the
    # winner-selection loop and the loser/deferred path further down can
    # attach the real, already-computed score to their AllocationOutcome
    # instead of leaving it None. Nothing here changes what is computed
    # or when — only that the result is kept instead of discarded.
    score_breakdown_by_risk_id: dict[str, scoring.ScoreBreakdown] = {}

    for customer_id in sorted(by_customer):
        cands = sorted(by_customer[customer_id], key=lambda c: c.risk_item.risk_id)

        admitted: list[tuple[float, Candidate]] = []
        for candidate in cands:
            fact_unavailable = fact_unavailable_by_risk_id.get(candidate.risk_item.risk_id, ())

            n = ledger.contacts_made(customer_id, decision_at)
            other_open = ledger.open_candidates_for_customer(
                customer_id, decision_at, exclude_risk_id=candidate.risk_item.risk_id
            )
            score_breakdown = scoring.score(
                candidate,
                candidate.request.requested_max_incentive_bps,
                n,
                tuple(item.amount_paise for item in other_open),
            )
            score_breakdown_by_risk_id[candidate.risk_item.risk_id] = score_breakdown
            if score_breakdown.expected_net_paise <= 0 and not fifo_mode:
                ledger.mark_terminally_denied(candidate.risk_item.risk_id)
                outcomes.append(
                    AllocationOutcome(
                        candidate=candidate,
                        outcome_kind=OutcomeKind.DENIED,
                        reason_code=NEGATIVE_EXPECTED_NET,
                        next_eligible_at=None,
                        grant=None,
                        fact_unavailable_reason_codes=fact_unavailable,
                        score=score_breakdown,
                        rescheduled_candidate=None,
                    )
                )
                continue

            admitted.append((score_breakdown.expected_net_paise, candidate))

        if not admitted:
            continue

        if fifo_mode:
            admitted.sort(key=_fifo_key)
        else:
            admitted.sort(key=lambda item: _tie_break_key(item, aging_bonus_paise))

        winner_candidate: Candidate | None = None
        winner_grant: Grant | None = None
        winner_effective_bps: int | None = None
        winner_score: scoring.ScoreBreakdown | None = None
        attempted_denials: dict[str, tuple[str, datetime]] = {}

        for score_val, candidate in admitted:
            # Phase 5 U-3: the score this SPECIFIC attempt is actually
            # scored under — starts as the admission-time score, and is
            # replaced below with the recomputed one IFF a downgrade
            # happens, so it always reflects the terms actually attempted
            # (existing Phase 4 behavior; only the capture is new).
            current_score = score_breakdown_by_risk_id[candidate.risk_item.risk_id]

            merchant_remaining, customer_remaining = ledger.remaining_margin_paise(
                candidate.customer_id, candidate.window_id
            )
            downgraded_ceiling = downgrade_to_fit(
                candidate.requested_incentive_ceiling_paise, merchant_remaining, customer_remaining
            )
            effective_bps = candidate.request.requested_max_incentive_bps
            if downgraded_ceiling < candidate.requested_incentive_ceiling_paise:
                effective_bps = (
                    (downgraded_ceiling * 10_000) // candidate.risk_item.amount_paise
                    if candidate.risk_item.amount_paise
                    else 0
                )
                n = ledger.contacts_made(customer_id, decision_at)
                other_open = ledger.open_candidates_for_customer(
                    customer_id, decision_at, exclude_risk_id=candidate.risk_item.risk_id
                )
                downgraded_score = scoring.score(
                    candidate, effective_bps, n, tuple(item.amount_paise for item in other_open)
                )
                current_score = downgraded_score
                if downgraded_score.expected_net_paise <= 0:
                    reason = (
                        MERCHANT_MARGIN_EXHAUSTED
                        if merchant_remaining <= customer_remaining
                        else CUSTOMER_MARGIN_EXHAUSTED
                    )
                    attempted_denials[candidate.risk_item.risk_id] = (
                        reason,
                        next_window_start(candidate.window_id),
                    )
                    continue  # abandon this candidate without attempting issuance

            result = issuer.issue_grant(conn, candidate, effective_bps, decision_at, run_seed_risk_ids)
            if isinstance(result, GrantIssued):
                winner_candidate = candidate
                winner_grant = result.grant
                winner_effective_bps = effective_bps
                winner_score = current_score
                break
            assert isinstance(result, BudgetDenial)
            next_eligible = result.next_eligible_at or next_window_start(candidate.window_id)
            attempted_denials[candidate.risk_item.risk_id] = (result.reason_code, next_eligible)

        if winner_candidate is not None:
            outcomes.append(
                AllocationOutcome(
                    candidate=winner_candidate,
                    outcome_kind=OutcomeKind.GRANTED,
                    reason_code=None,
                    next_eligible_at=None,
                    grant=winner_grant,
                    fact_unavailable_reason_codes=(),
                    score=winner_score,
                    rescheduled_candidate=None,
                    effective_incentive_bps=winner_effective_bps,
                )
            )

        for score_val, candidate in admitted:
            if winner_candidate is not None and candidate.risk_item.risk_id == winner_candidate.risk_item.risk_id:
                continue
            if candidate.risk_item.risk_id in attempted_denials:
                reason_code, next_eligible_at = attempted_denials[candidate.risk_item.risk_id]
            else:
                reason_code, next_eligible_at = (
                    LOST_TO_HIGHER_EXPECTED_NET,
                    next_window_start(candidate.window_id),
                )
            outcomes.append(
                deferred_or_denied(
                    candidate, reason_code, next_eligible_at, ledger,
                    score=score_breakdown_by_risk_id[candidate.risk_item.risk_id],
                )
            )

    return tuple(outcomes)
