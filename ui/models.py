"""Request models for the demo API.

PRESENTATION contracts only. These deliberately do NOT restate, wrap or
re-validate `sampark.contracts` — those are the human-owned domain contracts
(CONTRACTS.md, CLAUDE.md §3) and Phase 8 has no business redefining them.
Responses are plain dicts assembled from audit rows and session state, so
there is no second place where a decision's shape is declared.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sampark.demo.scenario import DEFAULT_SEED


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int = Field(default=DEFAULT_SEED, ge=0)
    # Wall-clock pacing between windows. True for the live demo (a ~40s
    # replay a human can follow); False for tests, which want it to finish
    # as fast as Postgres allows. Pacing changes only how fast an
    # already-decided sequence is revealed — never what is decided.
    pace: bool = True


class ChaosFireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Control-specific: an agent_id, a customer_id, or a provider failure
    # mode. Validated by the control itself, which raises
    # ChaosInapplicableError (409) rather than guessing at a default that
    # would silently do the wrong thing.
    target: str | None = None
