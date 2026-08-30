"""The chaos panel — spec §12.4, seven controls, recovered verbatim.

Spec §12.4's table, quoted exactly:

    | Control                          | What it exercises          |
    | Kill uplift model                | Graceful degradation       |
    | Revoke agent key                 | Registry quarantine        |
    | Set clock to 21:40               | TCCCPR quiet-hour filter   |
    | Force provider timeout           | Reservation rollback       |
    | Flood rogue agent to 6 req/min   | Rate ceiling + strikes     |
    | Mark customer opted-out mid-run  | Permanent suppression      |
    | Trigger RTO flag on an active cart | Interlock matrix         |

Seven, no more and no fewer. This module does not invent an eighth because
one would be easy, and does not drop one because it is awkward.

--- Two things this module deliberately does NOT do ---

1. **It never writes an audit event for a button press.** Spec §12.1 makes
   the audit log the DECISION record, not a UI activity feed. Arming a
   control changes system state; what reaches the chain is the EFFECT, once
   it actually changes a decision — a `decision.denied`, an `agent.revoked`,
   a `grant.rolled_back`, a `model.degraded`. If a control were fired and
   nothing downstream changed, the chain would correctly say nothing
   happened.
2. **It never fakes an effect when it cannot really apply.** A control
   fired in a state where its mechanism has nothing to act on returns
   `ChaosInapplicableError` (surfaced as HTTP 409), changes no state, and
   writes nothing. Silently pretending would be exactly the fabricated-demo
   failure mode the trace-integrity rule exists to prevent.

--- Control 7, and an honest substitution ---

`sampark/policy/hard/interlocks.py`'s `rto_flag` row is declared with
`condition=_unavailable`, a function that returns `None` unconditionally. It
never consults the ledger, so NO data change can make it deny — it can only
ever report FACT_UNAVAILABLE. Making it deny would require editing both
`sampark/policy/hard/interlocks.py` and `sampark/policy/types.py`
(`MediationLedgerView` has no RTO-flag method), both protected, and would
flip `fact_unavailable.rto_flag` from *recorded* to *resolved* across every
future run — changing the `fact_unavailable_counts` in the committed Phase
4/6/7 evidence.

So control 7 drives `dispute_open` instead: a genuinely working DENY
interlock in the same matrix, whose condition really does read the ledger
(`RiskItem.root_cause == 'disputed'`) and really does block every
discount-bearing grant for that customer. Same mechanism demonstrated (the
interlock matrix, sitting above agents that are each individually in
scope), zero protected-file changes.

This is a deviation from §12.4's literal control name. It is labelled as
such in the control's own `spec_note`, surfaced in the UI, and listed in
the README's "what did not get handled" section. It is flagged for owner
sign-off in the Phase 8 design lock; it is not a silent substitution.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from sampark.demo.provider import ProviderFailureMode


class ChaosControlId(str, enum.Enum):
    KILL_MODEL = "kill_model"
    REVOKE_AGENT_KEY = "revoke_agent_key"
    SET_CLOCK_QUIET_HOURS = "set_clock_quiet_hours"
    FORCE_PROVIDER_TIMEOUT = "force_provider_timeout"
    FLOOD_ROGUE_AGENT = "flood_rogue_agent"
    MARK_CUSTOMER_OPTED_OUT = "mark_customer_opted_out"
    TRIGGER_INTERLOCK_ON_CART = "trigger_interlock_on_cart"


class ChaosInapplicableError(RuntimeError):
    """The control cannot act in the current state. Mapped to HTTP 409 by
    the API. No state changed, no audit event written."""


@dataclass(frozen=True)
class ChaosControl:
    control_id: ChaosControlId
    spec_name: str  # spec §12.4's own wording, verbatim
    exercises: str  # spec §12.4's "What it exercises" column, verbatim
    mechanism: str  # the real backend operation invoked
    expected_audit: str  # the event type(s) the EFFECT produces
    spec_note: str = ""  # non-empty only where this deviates from §12.4


CONTROLS: tuple[ChaosControl, ...] = (
    ChaosControl(
        control_id=ChaosControlId.KILL_MODEL,
        spec_name="Kill uplift model",
        exercises="Graceful degradation",
        mechanism="sampark.demo.scorer_kill.KillableScorer.kill() - the next call to "
        "score() raises ModelUnavailableError; the runner catches it once, emits "
        "model.degraded, swaps in sampark.allocator.scorer.default_scorer() (the "
        "frozen Phase 4 heuristic) and re-runs the window.",
        expected_audit="model.degraded",
        spec_note="The uplift model is ALREADY unavailable on this dataset (no control "
        "arm; committed Phase 6 finding), so every run also emits a "
        "model.artifact_unavailable degradation at start. This control kills the "
        "scorer SEAM at runtime. Both reasons produce the same event and the same "
        "deterministic fallback - which is the point.",
    ),
    ChaosControl(
        control_id=ChaosControlId.REVOKE_AGENT_KEY,
        spec_name="Revoke agent key",
        exercises="Registry quarantine",
        mechanism="sampark.registry.strikes.revoke() + AgentRepository.save_agent(). "
        "The agent's next request is denied by evaluate_scope step 3.",
        expected_audit="agent.revoked, then request.denied_on_scope (scope.agent_revoked)",
    ),
    ChaosControl(
        control_id=ChaosControlId.SET_CLOCK_QUIET_HOURS,
        spec_name="Set clock to 21:40",
        exercises="TCCCPR quiet-hour filter",
        mechanism="Re-times the next pending request's proposed_send_after to 21:40 IST. "
        "NOT a clock mock: nothing in this codebase reads a wall clock on the "
        "decision path (structurally tested), and "
        "sampark.policy.hard.quiet_hours.evaluate is a pure function of the "
        "instant it is handed - so setting the instant IS the mechanism.",
        expected_audit="decision.deferred (policy.quiet_hours)",
    ),
    ChaosControl(
        control_id=ChaosControlId.FORCE_PROVIDER_TIMEOUT,
        spec_name="Force provider timeout",
        exercises="Reservation rollback",
        mechanism="sampark.demo.provider.MockProvider.arm(). The next grant's send "
        "raises ProviderTimeout; the runner retries, and on exhaustion calls the "
        "existing sampark.budget.postgres_ledger.rollback_grant().",
        expected_audit="grant.rolled_back (HARD_DOWN), or grant.confirmed after a "
        "deduplicated retry (the two retry modes)",
    ),
    ChaosControl(
        control_id=ChaosControlId.FLOOD_ROGUE_AGENT,
        spec_name="Flood rogue agent to 6 req/min",
        exercises="Rate ceiling + strikes",
        mechanism="Injects the rogue's six correctly-scoped requests into the next "
        "window inside one simulated minute. Its declared "
        "max_requests_per_hour=3, so requests 4-6 breach the ceiling.",
        expected_audit="decision.denied (agent.rate_ceiling_exceeded) x3, agent.struck x3, agent.revoked",
    ),
    ChaosControl(
        control_id=ChaosControlId.MARK_CUSTOMER_OPTED_OUT,
        spec_name="Mark customer opted-out mid-run",
        exercises="Permanent suppression",
        mechanism="UPDATE contact_states SET optouts_by_channel in the demo schema. "
        "sampark.policy.hard.opt_out then permanently DENIES that channel for "
        "that customer.",
        expected_audit="decision.denied (policy.opt_out_active)",
    ),
    ChaosControl(
        control_id=ChaosControlId.TRIGGER_INTERLOCK_ON_CART,
        spec_name="Trigger RTO flag on an active cart",
        exercises="Interlock matrix",
        mechanism="UPDATE risk_items SET root_cause='disputed' in the demo schema, "
        "driving sampark.policy.hard.interlocks' dispute_open row, which DENIES "
        "every discount-bearing grant for that customer.",
        expected_audit="decision.denied (interlock.dispute_open)",
        spec_note="SUBSTITUTION, stated plainly: the rto_flag interlock row is declared "
        "with a condition that always returns FACT_UNAVAILABLE and can never deny "
        "without editing protected Phase 4 policy files and changing committed "
        "evidence. dispute_open is a real, working row of the SAME interlock "
        "matrix. See the README's 'what did not get handled' section.",
    ),
)

CONTROLS_BY_ID: dict[ChaosControlId, ChaosControl] = {c.control_id: c for c in CONTROLS}

assert len(CONTROLS) == 7, "spec §12.4 defines exactly seven chaos controls"


@dataclass
class ChaosState:
    """DEMO CONTROL STATE — explicitly NOT system truth.

    The UI renders this in its own region, visually distinct from the trace,
    and never merges it into the audit-derived event stream. It records what
    a human armed; the audit chain records what the system then did. Those
    are two different kinds of fact and Phase 8 keeps them apart on purpose.
    """

    fired: dict[ChaosControlId, int] = field(default_factory=dict)
    last_effect: dict[ChaosControlId, str] = field(default_factory=dict)

    # Pending one-shot intents consumed by the runner on its next window.
    pending_quiet_hours: bool = False
    pending_flood: bool = False
    pending_provider_mode: ProviderFailureMode | None = None

    def note(self, control_id: ChaosControlId, effect: str) -> None:
        self.fired[control_id] = self.fired.get(control_id, 0) + 1
        self.last_effect[control_id] = effect

    def reset(self) -> None:
        self.fired.clear()
        self.last_effect.clear()
        self.pending_quiet_hours = False
        self.pending_flood = False
        self.pending_provider_mode = None

    def snapshot(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for control in CONTROLS:
            out.append(
                {
                    "control_id": control.control_id.value,
                    "spec_name": control.spec_name,
                    "exercises": control.exercises,
                    "mechanism": control.mechanism,
                    "expected_audit": control.expected_audit,
                    "spec_note": control.spec_note,
                    "fired_count": self.fired.get(control.control_id, 0),
                    "last_effect": self.last_effect.get(control.control_id, ""),
                }
            )
        return out


__all__ = [
    "CONTROLS",
    "CONTROLS_BY_ID",
    "ChaosControl",
    "ChaosControlId",
    "ChaosInapplicableError",
    "ChaosState",
]
