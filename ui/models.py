"""Request models for the demo API.

PRESENTATION contracts only. These deliberately do NOT restate, wrap or
re-validate `sampark.contracts` — those are the human-owned domain contracts
(CONTRACTS.md, CLAUDE.md §3) and Phase 8 has no business redefining them.
Responses are plain dicts assembled from audit rows and session state, so
there is no second place where a decision's shape is declared.
"""

from __future__ import annotations

from typing import Literal

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


class ProviderFailureRequest(BaseModel):
    """Arm the mock channel provider for the Razorpay product flow.

    `mode` is a `sampark.demo.provider.ProviderFailureMode` value. Validated
    by the session (which owns the enum) rather than restated as a second
    Literal here — one vocabulary, one owner."""

    model_config = ConfigDict(extra="forbid")

    mode: str | None = None


class PaymentLinkRequest(BaseModel):
    """Create one Razorpay Test Mode payment link.

    `role` is presentation only and never reaches Razorpay: "headline" is the
    demo's 1,000 INR subject, "contrast" is the second, above-break-even
    payment that exists so the grant path is demonstrable. `amount_inr`
    overrides the role's default amount; the gateway owns both defaults."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["headline", "contrast"] = "headline"
    amount_inr: int | None = Field(default=None, ge=1)
