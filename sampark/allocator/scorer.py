"""Scorer — the model-agnostic scoring interface, Phase 6.

Design Lock (Phase 4, §14 Round 2 sprint-room decision): "the allocator
is model-agnostic behind an interface. Heuristic first (Phase 4), models
as an upgrade (Phase 6)." Phase 4 shipped the heuristic wired directly
into `sampark.allocator.greedy` via `sampark.allocator.scoring.score`.
This module is the seam that decision was always meant to sit behind.

`HeuristicScorer` wraps `sampark.allocator.scoring.score` VERBATIM — no
new arithmetic, no reinterpretation of any term. It is the default
scorer everywhere (`allocate_window(..., scorer=None)` constructs one),
so every existing caller's behavior is byte-identical to before this
module existed. `tests/allocator/test_scorer_interface.py` proves this
both by direct comparison against `scoring.score` and by an
end-to-end Arm B reproduction of the committed Phase 4 result.

A model-backed implementation (`sampark.models.scorer.ModelBackedScorer`)
lives outside `sampark.allocator` entirely — this package only defines
the shape every scorer must have, never a concrete model.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from sampark.allocator import scoring
from sampark.allocator.candidate import Candidate


@runtime_checkable
class Scorer(Protocol):
    """Structural contract: anything with this method can rank candidates
    for `sampark.allocator.greedy.allocate_window`. `n` and
    `other_open_amounts_paise` are SAMPARK's own observed state (never
    Environment internals, never HiddenResponseProfile — see
    `sampark.policy.soft.recovery_prior`'s docstring for why that
    boundary matters)."""

    def score(
        self,
        candidate: Candidate,
        effective_incentive_bps: int,
        n: int,
        other_open_amounts_paise: Sequence[int],
    ) -> scoring.ScoreBreakdown: ...


class HeuristicScorer:
    """The frozen Phase 4 formula, unchanged. Stateless — safe to share
    a single instance across an entire run, or to construct fresh per
    call; both are equivalent since this class holds no state."""

    def score(
        self,
        candidate: Candidate,
        effective_incentive_bps: int,
        n: int,
        other_open_amounts_paise: Sequence[int],
    ) -> scoring.ScoreBreakdown:
        return scoring.score(candidate, effective_incentive_bps, n, other_open_amounts_paise)


_DEFAULT_HEURISTIC_SCORER = HeuristicScorer()


def default_scorer() -> Scorer:
    """The scorer used when a caller passes `scorer=None` — one shared
    stateless `HeuristicScorer` instance, not a fresh one per call."""
    return _DEFAULT_HEURISTIC_SCORER
