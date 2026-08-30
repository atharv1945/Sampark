"""Killable scorer — spec §12.3 failure 3 ("Model unavailable").

Spec §12.3 asks for the uplift model to be killed on camera, after which
"the allocator degrades to unweighted heuristic ranking, logs a degradation
event, and keeps issuing grants. Recovery drops; compliance does not. That
distinction is the whole design philosophy."

--- The honesty constraint, stated before the mechanism ---

On THIS dataset `sampark.models.scorer.build_scorer()` ALREADY returns a
`HeuristicScorer`, because the uplift T-learner reports
`available=False`: there is no untreated control population to learn from
(the committed Phase 6 finding, unchanged by Phase 7's holdout work for the
non-holdout arms). Pretending a live model is running so it can be
dramatically killed would misrepresent committed evidence, which CLAUDE.md
§14 forbids outright.

So Phase 8 does not pretend. It surfaces BOTH real degradation reasons and
treats them identically, which is a stronger demonstration than the
pretence would have been:

    model.artifact_unavailable  the committed artifact did not load, or
                                loaded and failed `is_valid_for_scoring()`.
                                TRUE ON EVERY RUN of this repository today.
                                Emitted once, at run start.
    model.killed_by_operator    chaos control 1 killed the scorer seam
                                mid-run.

Both converge on the same deterministic fallback and the same
`model.degraded` event. What the reviewer sees is the real argument:
**SAMPARK treats "never had a model" and "the model died mid-run"
identically — detect, degrade, log, keep issuing compliant grants.**

--- Why a wrapper, and not a change to build_scorer ---

`sampark.allocator.scorer.Scorer` is a structural `Protocol`, introduced in
Phase 6 precisely as "the seam this decision was always meant to sit
behind". Wrapping it needs no change to `sampark/models/scorer.py`,
`sampark/models/artifact.py`, or any `artifact_data*.py` — none of which
Phase 8 may touch, since their exact behaviour is quoted in committed Phase
6/7 evidence reports.

The fallback target is `sampark.allocator.scorer.default_scorer()` — the
same single shared stateless `HeuristicScorer` instance every pre-Phase-6
caller already used. So the post-degradation scoring is not "similar to"
the frozen Phase 4 heuristic; it is bit-for-bit that exact code path.

--- Determinism ---

`kill()` is an explicit operator action, not a probability. Given the same
seed and the same window at which the kill is armed, the same candidates
raise, the same single `model.degraded` event is written, and every
subsequent score is the heuristic's. No clock, no RNG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from sampark.allocator import scoring
from sampark.allocator.candidate import Candidate
from sampark.allocator.scorer import Scorer

# Reason codes are re-exported from the emitter so there is ONE spelling of
# each in the codebase, and the audit payload and the UI cannot drift apart.
from sampark.audit.emit import (
    MODEL_DEGRADED_ARTIFACT_UNAVAILABLE,
    MODEL_DEGRADED_KILLED_BY_OPERATOR,
)


class ModelUnavailableError(RuntimeError):
    """The model-backed scorer became unavailable mid-run. Raised out of
    `score()` — a runtime raise is what a real service outage looks like to
    a caller, which is exactly what the demo needs to show being handled.

    Caught by `sampark.demo.runner`, which emits `model.degraded`, swaps in
    the frozen heuristic, and re-runs the window. Never caught inside this
    module: swallowing it here would hide the failure from the audit log,
    the one thing spec §12.1 forbids.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__("model scorer unavailable: " + reason_code)
        self.reason_code = reason_code


@dataclass
class KillableScorer:
    """A `Scorer` that can be switched off at runtime.

    Satisfies `sampark.allocator.scorer.Scorer` structurally (it is a
    `runtime_checkable` Protocol), so `allocate_window(..., scorer=...)`
    accepts it with zero changes to the allocator.

    While alive it delegates VERBATIM to `inner` — it adds no arithmetic,
    no rounding, and no reinterpretation of any term. So a run that never
    kills the scorer produces byte-identical scores to one that passes
    `inner` directly, which is what makes this wrapper safe to leave in the
    normal path.
    """

    inner: Scorer
    _killed: bool = False
    _reason_code: str = field(default=MODEL_DEGRADED_KILLED_BY_OPERATOR)

    @property
    def killed(self) -> bool:
        return self._killed

    @property
    def reason_code(self) -> str:
        return self._reason_code

    @property
    def inner_name(self) -> str:
        return type(self.inner).__name__

    def kill(self, reason_code: str = MODEL_DEGRADED_KILLED_BY_OPERATOR) -> None:
        self._killed = True
        self._reason_code = reason_code

    def score(
        self,
        candidate: Candidate,
        effective_incentive_bps: int,
        n: int,
        other_open_amounts_paise: Sequence[int],
    ) -> scoring.ScoreBreakdown:
        if self._killed:
            raise ModelUnavailableError(self._reason_code)
        return self.inner.score(candidate, effective_incentive_bps, n, other_open_amounts_paise)


def initial_degradation_reason(scorer: Scorer) -> str | None:
    """The reason to report at run start, or None if a real model is live.

    `sampark.models.scorer.build_scorer()` NEVER raises: it catches every
    artifact failure and returns `default_scorer()` — a `HeuristicScorer` —
    logging why. That means the only way to learn, from the outside, that
    the model was unavailable is to look at what came back. If the factory
    handed back a plain heuristic, the artifact was not usable, and that is
    a degradation the audit log should carry from the very first window
    rather than silently starting in a degraded state.

    This inspects the RESULT of the unchanged factory. It does not
    re-implement, re-run, or second-guess the factory's own gate.
    """
    from sampark.allocator.scorer import HeuristicScorer

    if isinstance(scorer, HeuristicScorer):
        return MODEL_DEGRADED_ARTIFACT_UNAVAILABLE
    return None


__all__ = [
    "MODEL_DEGRADED_ARTIFACT_UNAVAILABLE",
    "MODEL_DEGRADED_KILLED_BY_OPERATOR",
    "KillableScorer",
    "ModelUnavailableError",
    "initial_degradation_reason",
]
