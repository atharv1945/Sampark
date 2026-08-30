"""Stage-two enforcement: the agent rate ceiling — spec §12.3 failure 2.

`CapabilityScope.max_requests_per_hour` has been declared
(`sampark/contracts/agent.py`), persisted
(`sampark/registry/store.py`) and CHECK-constrained
(`sampark/schema.sql`) since Phase 3 — and read by NO evaluation code
anywhere in the repository. This module is the missing enforcement, and it
is the mechanism spec §12.3's rogue demo actually describes.

--- Why this lives here, and not in three tempting other places ---

Spec §12.3 splits the rogue agent into two stages, and calls the contrast
between them "the entire thesis in ninety seconds of screen time":

    STAGE 1  the agent misbehaves OUTSIDE its scope. The Registry rejects it
             on signature-verified scope, with NO allocator involvement.
             This is AUTHORIZATION: cheap, binary, stateless.
    STAGE 2  the agent misbehaves INSIDE its scope. Scope passes; budgets,
             rate ceiling and quiet hours deny. Strikes accumulate, the key
             is revoked.

Keeping those two genuinely separate is the whole point, so:

  * NOT inside `sampark/registry/scope.py::evaluate_scope`. That would
    collapse stage 1 and stage 2 into one stage and destroy the contrast,
    and it would make a deliberately pure, stateless function stateful.
  * NOT as a 12th rule in `sampark/policy/hard/__init__.py::HARD_RULES`.
    That package is protected; adding a rule breaks the frozen ordering test
    and changes the `fact_unavailable`/denial counts in the committed
    Phase 4/6/7 evidence.
  * NOT inside `sampark/mediation/service.py::mediate_window`. That is the
    Phase 4 decision path, and `DECISIONS.md` already records the reasoning
    against wiring a strike trigger into it.

So it lives here, in Phase 8's own package, and the runner calls it at the
one correct point: AFTER `evaluate_scope` has returned None (scope passed)
and BEFORE a Candidate is built (so the allocator is never reached).

--- What may strike, and what must never ---

Only `agent.rate_ceiling_exceeded` accumulates a strike.

Budget and allocation denials — `allocation.lost_to_higher_expected_net`,
`budget.contact_cap_24h`, `budget.contact_slot_taken`,
`budget.merchant_margin_exhausted` — and quiet-hours deferrals
(`policy.quiet_hours`) must NEVER strike. They are the NORMAL, CORRECT
outcome for a well-behaved agent and occur in the thousands in every
committed Arm B run. Striking on them would revoke all four honest agents
within a single run, and would turn the demo's headline claim (scope
violations read 0 for well-behaved agents while every other row moves)
into a screen full of false accusations.

The principle, stated once so it is not re-derived later:

    An agent is struck for MISUSING THE PROTOCOL — asking too often.
    An agent is never struck for LOSING A FAIR CONTEST.

Note this is deliberately NARROWER than spec §12.3's literal list
("budgets, rate ceiling and quiet hours deny. Strikes accumulate"). The
denials still all happen and are all shown; only the STRIKE is narrowed.
That divergence is intentional, is recorded in the Phase 8 design lock, and
is flagged for owner sign-off — it is not an oversight.

--- Relationship to `sampark.registry.strikes` ---

This module adds NO new strike state machine. It calls the existing,
unchanged `apply_strike` / `revoke` and the existing `STRIKE_THRESHOLD = 3`.
`sampark.registry.strikes.record_scope_denial` is deliberately NOT called
from anywhere in Phase 8: it strikes on SCOPE denials (stage one), and
mixing two strike sources would make it ambiguous on camera which stage
revoked the key.

--- Determinism ---

The rolling window is keyed on `GrantRequest.issued_at`, a SIMULATED
instant. This module reads no wall clock and draws no randomness, so a
seeded replay trips the ceiling at exactly the same request every time.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sampark.contracts import Agent, AgentState, CapabilityScope, GrantRequest
from sampark.registry.store import AgentRepository
from sampark.registry.strikes import STRIKE_THRESHOLD, apply_strike

# The stage-two reason code. A namespaced string, exactly like Phase 3's
# nine `scope.*` codes and Phase 4's `policy.*`/`budget.*`/`allocation.*`
# families — deliberately NOT added to
# `sampark.allocator.reason_codes.DENY_REASON_CODES`, which is a protected
# Phase 4 module this phase does not touch.
RATE_CEILING_EXCEEDED = "agent.rate_ceiling_exceeded"

# The complete, closed set of reason codes that accumulate a strike. See the
# module docstring for why this is exactly one entry and must stay that way.
STAGE_TWO_STRIKE_REASON_CODES: frozenset[str] = frozenset({RATE_CEILING_EXCEEDED})

RATE_WINDOW = timedelta(hours=1)


class NotStrikeWorthyError(ValueError):
    """`record_stage_two_strike` was called with a reason code outside
    `STAGE_TWO_STRIKE_REASON_CODES`. Raised rather than silently striking:
    the guard is the enforcement of the "never struck for losing a fair
    contest" rule, so it must be loud, not defensive."""


@dataclass
class AgentRateWindow:
    """Per-agent rolling count of requests over the trailing `RATE_WINDOW`
    of SIMULATED time.

    Every request that reaches this gate is recorded, whether it is then
    admitted or denied. That is the faithful reading of spec §8.1's "request
    rate ceiling": a denied attempt was still an attempt. It also means an
    agent that keeps hammering stays denied for the rest of the hour, which
    is what a rate ceiling is for.

    State is in-process and lives for one demo run. It does not need to be
    durable, because every rate denial is itself written to the audit chain —
    the durable record is the log, not this counter. (Redis, the project's
    designated counter store, is not a pip-installed dependency here; adding
    a Postgres table would mean changing the human-owned schema. Neither is
    warranted for a counter whose every decision is already audited.)
    """

    _by_agent: dict[str, list[datetime]] = field(default_factory=dict)

    def count_in_window(self, agent_id: str, at: datetime) -> int:
        stamps = self._by_agent.get(agent_id)
        if not stamps:
            return 0
        cutoff = at - RATE_WINDOW
        # stamps is kept sorted by `record`, so the trailing slice is a
        # bisect rather than a scan.
        return len(stamps) - bisect.bisect_right(stamps, cutoff)

    def record(self, agent_id: str, at: datetime) -> None:
        stamps = self._by_agent.setdefault(agent_id, [])
        bisect.insort(stamps, at)

    def reset(self) -> None:
        self._by_agent.clear()


def evaluate_agent_rate(
    request: GrantRequest,
    scope: CapabilityScope,
    window: AgentRateWindow,
) -> str | None:
    """Stage two, part one. Returns `RATE_CEILING_EXCEEDED` if this request
    breaches the agent's own declared ceiling, else None.

    Called ONLY after `evaluate_scope` has already returned None. Mirrors
    that function's contract exactly: None means "carry on", a reason code
    means "denied". It never constructs a decision, a Candidate, or a Grant —
    the caller does, exactly as `sampark/mediation/service.py` does for a
    scope denial.

    This module imports nothing from `sampark.allocator` or
    `sampark.policy`; `tests/demo/test_allocator_non_involvement.py` asserts
    that structurally, so the "no allocator involvement" property covers
    stage two's gate as well as stage one's.
    """
    count = window.count_in_window(request.agent_id, request.issued_at)
    window.record(request.agent_id, request.issued_at)
    if count >= scope.max_requests_per_hour:
        return RATE_CEILING_EXCEEDED
    return None


@dataclass(frozen=True)
class StrikeResult:
    agent: Agent  # the agent AFTER the strike (new strike_count, maybe REVOKED)
    newly_revoked: bool  # True only on the transition into REVOKED


def record_stage_two_strike(
    agent_repo: AgentRepository, agent: Agent, reason_code: str
) -> StrikeResult:
    """Apply and persist one stage-two strike.

    Reuses `sampark.registry.strikes.apply_strike` unchanged, which is what
    increments `strike_count` and flips ACTIVE -> REVOKED on reaching
    `STRIKE_THRESHOLD` (3, unchanged). This function adds only the
    strike-worthiness guard and the persistence call, so there is exactly
    one strike state machine in the codebase, not two.
    """
    if reason_code not in STAGE_TWO_STRIKE_REASON_CODES:
        raise NotStrikeWorthyError(
            repr(reason_code) + " is not a stage-two strike reason code. "
            "Only " + repr(sorted(STAGE_TWO_STRIKE_REASON_CODES)) + " may strike — an agent is "
            "never struck for losing a fair contest (see module docstring)."
        )
    was_active = agent.state is AgentState.ACTIVE
    updated = apply_strike(agent)
    agent_repo.save_agent(updated)
    return StrikeResult(
        agent=updated,
        newly_revoked=was_active and updated.state is AgentState.REVOKED,
    )


__all__ = [
    "RATE_CEILING_EXCEEDED",
    "STAGE_TWO_STRIKE_REASON_CODES",
    "STRIKE_THRESHOLD",
    "AgentRateWindow",
    "NotStrikeWorthyError",
    "StrikeResult",
    "evaluate_agent_rate",
    "record_stage_two_strike",
]
