"""Channel provider with a failure path — spec §12.3 failure 1, §6.2.

Phase 2's `agents/channel.py::MockChannelAdapter.send()` returns
`delivered=True` unconditionally and takes no idempotency key: Arm A has no
deliverability model at all (a documented Phase 2 scope decision). So the
repository had NO provider failure path to demonstrate, and this module is
the smallest abstraction that adds one. `agents/channel.py` is NOT modified
— it is wrapped, and its payload-logging behaviour is reused verbatim.

--- Why the retry is provider-level, and not a re-issuance ---

The naive reading of "timeout -> rollback -> retry -> success" is that the
retry re-runs issuance and gets a fresh grant. That is not implementable
against this codebase, and must not be forced. Two facts, both verified:

  * `sampark/budget/issuance.py` step (1) returns any existing grant for a
    `request_id` REGARDLESS of its state, and
    `grant_id = uuid5(NS_GRANT, str(request_id))` is a pure function of the
    request. One request owns exactly one grant, permanently.
  * `sampark/budget/postgres_ledger.py::_release` moves the grant to
    ROLLED_BACK, and no transition OUT of ROLLED_BACK exists in either that
    module's legality map or in `sampark/mediation/lifecycle.py`'s.
    ROLLED_BACK is terminal.

Making a rolled-back request re-issuable would mean editing
`sampark/budget/issuance.py` — the human-owned SERIALIZABLE transaction
(CLAUDE.md §3), whose exact semantics every prior evidence run depends on.
That is not an acceptable price for a demo.

Reading spec §6.2 precisely shows it was never asked for. §6.2's
compensation note is "slot is NOT silently consumed; no double-send on
retry", and §12.3 is "retry is idempotent under the same grant_id. No
double-send, no silently burned budget." Those are TWO separate guarantees:

    no silently burned budget  ->  ROLLBACK returns margin + contact slot
    no double-send             ->  the PROVIDER retry is idempotent,
                                   keyed by grant_id

This module provides the second. `sampark.demo.runner` provides the first by
calling the existing, already-tested `rollback_grant`. Neither requires a
protected file to change.

--- The four modes ---

    NONE                  every send succeeds (normal operation).
    TIMEOUT_THEN_SUCCESS  attempt 1 raises WITHOUT recording acceptance;
                          attempt 2 sends for real and succeeds.
                          Proves: a retry is safe, no budget was burned.
    ACCEPT_THEN_TIMEOUT   attempt 1 records acceptance and THEN raises — the
                          genuinely hard case, where the provider already
                          delivered but the caller never learned so.
                          Attempt 2 finds the key and returns the ORIGINAL
                          receipt without re-sending.
                          Proves: NO DOUBLE-SEND when the timeout lands
                          after provider-side acceptance.
    HARD_DOWN             every attempt raises. After MAX_ATTEMPTS the
                          runner rolls the grant back.
                          Proves: rollback is real, and releases both margin
                          pools and the contact slot.

Determinism: this module reads no clock and draws no randomness. Which
attempt fails is a pure function of the armed mode and the attempt counter,
so a seeded replay fails identically on every run.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

from agents.channel import DeliveryReceipt, MockChannelAdapter
from agents.types import ContactAction

# How many times the runner asks the provider before giving up and rolling
# back. 2 is the smallest number that can demonstrate BOTH a successful
# retry and an exhausted one, which is exactly what §12.3 asks for.
MAX_ATTEMPTS = 2


class ProviderFailureMode(enum.Enum):
    NONE = "none"
    TIMEOUT_THEN_SUCCESS = "timeout_then_success"
    ACCEPT_THEN_TIMEOUT = "accept_then_timeout"
    HARD_DOWN = "hard_down"


class ProviderTimeout(RuntimeError):
    """The provider did not answer in time. Carries the grant_id so the
    runner rolls back exactly the reservation this attempt was for."""

    def __init__(self, grant_id: uuid.UUID, attempt: int, mode: ProviderFailureMode) -> None:
        super().__init__(
            "provider timeout on grant_id=" + str(grant_id)
            + " attempt=" + str(attempt) + " mode=" + mode.value
        )
        self.grant_id = grant_id
        self.attempt = attempt
        self.mode = mode


@dataclass
class SendResult:
    receipt: DeliveryReceipt
    attempts: int
    deduplicated: bool  # True iff a stored receipt was returned instead of re-sending


@dataclass
class MockProvider:
    """Wraps the unchanged Phase 2 mock adapters and adds (a) an idempotency
    store keyed by grant_id and (b) injectable failure.

    `failure_mode` is read at send time, so a control armed mid-run affects
    the NEXT send and nothing already completed. `_targeted_grant_id`, when
    set, restricts the failure to one specific grant — which is what makes
    the demo legible (one visible rollback) instead of a cascade of every
    grant in the window failing at once.
    """

    failure_mode: ProviderFailureMode = ProviderFailureMode.NONE
    _targeted_grant_id: uuid.UUID | None = None
    _adapters: dict[str, MockChannelAdapter] = field(
        default_factory=lambda: {
            "sms": MockChannelAdapter("sms"),
            "whatsapp": MockChannelAdapter("whatsapp"),
            "voice": MockChannelAdapter("voice"),
        }
    )
    # grant_id -> the receipt the provider has ALREADY accepted. This is the
    # idempotency store, and the reason a retry can never double-send.
    _accepted: dict[uuid.UUID, DeliveryReceipt] = field(default_factory=dict)
    _attempts: dict[uuid.UUID, int] = field(default_factory=dict)

    # --- chaos surface -------------------------------------------------

    def arm(self, mode: ProviderFailureMode, grant_id: uuid.UUID | None = None) -> None:
        self.failure_mode = mode
        self._targeted_grant_id = grant_id

    def disarm(self) -> None:
        self.failure_mode = ProviderFailureMode.NONE
        self._targeted_grant_id = None

    def is_armed(self) -> bool:
        return self.failure_mode is not ProviderFailureMode.NONE

    # --- the send path -------------------------------------------------

    def attempts_for(self, grant_id: uuid.UUID) -> int:
        return self._attempts.get(grant_id, 0)

    def has_accepted(self, grant_id: uuid.UUID) -> bool:
        return grant_id in self._accepted

    def _applies_to(self, grant_id: uuid.UUID) -> bool:
        if not self.is_armed():
            return False
        return self._targeted_grant_id is None or self._targeted_grant_id == grant_id

    def send(self, grant_id: uuid.UUID, action: ContactAction) -> SendResult:
        """One provider attempt for `grant_id`.

        Raises `ProviderTimeout` on failure. Returns a `SendResult` on
        success — `deduplicated=True` means the provider had already accepted
        this grant_id and the stored receipt was returned WITHOUT contacting
        anyone a second time.
        """
        attempt = self._attempts.get(grant_id, 0) + 1
        self._attempts[grant_id] = attempt

        # IDEMPOTENCY FIRST, unconditionally, before any failure injection:
        # if this grant was already accepted, nothing may re-send it, no
        # matter which mode is armed. This ordering IS the no-double-send
        # guarantee.
        if grant_id in self._accepted:
            return SendResult(receipt=self._accepted[grant_id], attempts=attempt, deduplicated=True)

        if self._applies_to(grant_id):
            mode = self.failure_mode
            if mode is ProviderFailureMode.HARD_DOWN:
                raise ProviderTimeout(grant_id, attempt, mode)
            if mode is ProviderFailureMode.TIMEOUT_THEN_SUCCESS and attempt == 1:
                # Nothing recorded: the provider genuinely never took it.
                raise ProviderTimeout(grant_id, attempt, mode)
            if mode is ProviderFailureMode.ACCEPT_THEN_TIMEOUT and attempt == 1:
                # The provider DID accept — record it, THEN fail the caller.
                # The idempotency check above will find it on the retry and
                # refuse to re-send.
                self._accepted[grant_id] = self._adapters[action.channel].send(action)
                raise ProviderTimeout(grant_id, attempt, mode)

        receipt = self._adapters[action.channel].send(action)
        self._accepted[grant_id] = receipt
        return SendResult(receipt=receipt, attempts=attempt, deduplicated=False)
