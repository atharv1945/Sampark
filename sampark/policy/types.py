"""Hard-policy verdict model — Design Lock §4.

Exactly three verdicts. FACT_UNAVAILABLE is a distinct member: it is
never constructed from, compared equal to, or coerced into ADMISSIBLE
(sampark/policy/hard/__init__.py's evaluate_all() relies on this — a
FACT_UNAVAILABLE verdict is recorded and evaluation continues; it can
never itself admit a candidate).

`sampark.policy.hard` rules are pure functions of (Candidate,
PolicyContext) -> HardVerdict. `MediationLedgerView` is the read-only
surface every hard rule and the soft scoring layer needs; it has no
mutating method — reservation/confirmation live in the (human-owned)
issuance boundary, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Mapping, Protocol

from sampark.allocator.candidate import Candidate
from sampark.contracts import RiskItem


class Verdict(Enum):
    ADMISSIBLE = "ADMISSIBLE"
    INADMISSIBLE = "INADMISSIBLE"
    FACT_UNAVAILABLE = "FACT_UNAVAILABLE"


class OnUnavailable(Enum):
    EXCLUDE = "EXCLUDE"  # candidate is denied
    PROCEED_AND_COUNT = "PROCEED_AND_COUNT"  # candidate continues; the gap is recorded


@dataclass(frozen=True)
class HardVerdict:
    verdict: Verdict
    reason_code: str | None = None
    next_eligible_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.verdict is Verdict.ADMISSIBLE:
            if self.reason_code is not None or self.next_eligible_at is not None:
                raise ValueError("ADMISSIBLE must carry no reason_code and no next_eligible_at")
        else:
            if self.reason_code is None:
                raise ValueError(f"{self.verdict} requires a reason_code")

    @classmethod
    def admissible(cls) -> "HardVerdict":
        return cls(Verdict.ADMISSIBLE)

    @classmethod
    def deny(cls, reason_code: str) -> "HardVerdict":
        """Permanent denial — next_eligible_at is None."""
        return cls(Verdict.INADMISSIBLE, reason_code, None)

    @classmethod
    def defer(cls, reason_code: str, next_eligible_at: datetime) -> "HardVerdict":
        """Transient denial — admissible again at next_eligible_at."""
        return cls(Verdict.INADMISSIBLE, reason_code, next_eligible_at)

    @classmethod
    def fact_unavailable(cls, reason_code: str) -> "HardVerdict":
        return cls(Verdict.FACT_UNAVAILABLE, reason_code, None)

    @property
    def is_defer(self) -> bool:
        return self.verdict is Verdict.INADMISSIBLE and self.next_eligible_at is not None

    @property
    def is_deny(self) -> bool:
        return self.verdict is Verdict.INADMISSIBLE and self.next_eligible_at is None


class MediationLedgerView(Protocol):
    """Read-only surface hard-policy rules and soft scoring read from.
    Implemented by sampark.budget.store.InMemoryMediationLedger (Phase 4
    reference/test implementation) and, once the owner-authored schema
    lands, by a Postgres-backed equivalent — see the schema/issuance
    proposal artifact."""

    def optouts_by_channel(self, customer_id: str) -> Mapping[str, str]: ...

    def consent_scopes(self, customer_id: str) -> Mapping[str, Mapping[str, str]]: ...

    def risk_items_for_customer(self, customer_id: str) -> tuple[RiskItem, ...]: ...

    def rolling_contact_counts(self, customer_id: str, decision_at: datetime) -> tuple[int, int]:
        """(count in the last 24h, count in the last 7d) of grants in a
        capacity-consuming state (Design Lock §3.3), anchored on
        grants.send_after, strict '>' boundary (Design Lock §3.4)."""
        ...

    def has_active_claim(self, customer_id: str, window_id: date) -> bool: ...

    def open_candidates_for_customer(
        self, customer_id: str, decision_at: datetime, exclude_risk_id: str
    ) -> tuple[RiskItem, ...]:
        """This customer's risk items that have arrived and are still
        unresolved (not GRANTED-and-CONFIRMED, not terminally denied) at
        decision_at, excluding `exclude_risk_id` — Design Lock §6.2's
        `other_open_*`."""
        ...


@dataclass(frozen=True)
class PolicyContext:
    ledger: MediationLedgerView
    decision_at: datetime
