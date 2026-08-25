"""Grant-issuance contract + reference/test implementation — Design Lock §11.

The exact SERIALIZABLE issuance transaction is HUMAN-OWNED
(sampark/budget/issuance.py, not created here). This module defines
the CALLABLE CONTRACT the mediation layer expects from it:

    issue_grant(conn, candidate, effective_incentive_bps, decision_at)
        -> GrantIssued | BudgetDenial

and provides `InMemoryMediationLedger` + `InMemoryGrantIssuer`, a
single-process reference implementation conforming to that same
protocol, used by Phase 4's unit tests and by the Arm B batch runner
UNTIL the owner-authored schema and issuance transaction land.

This is explicitly NOT a claim that the in-memory implementation is
correct under concurrency — it holds no real locks and gives no
SERIALIZABLE guarantee. The 50-way concurrency test
(tests/test_concurrent_grant_issuance.py) is human-owned and is not
implemented against this class; it exists only to prove the real
Postgres transaction, once written. What IS asserted here is protocol
conformance: identical inputs produce the identical decision shape
(GrantIssued vs BudgetDenial, same reason codes, same state
transitions) that the real transaction is specified to produce.

`conn` in the protocol signature is a placeholder for whatever handle
the real implementation needs (a psycopg.Connection, eventually); the
in-memory reference's "conn" IS the InMemoryMediationLedger instance
itself — callers pass the same object they query for MediationLedgerView.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Protocol

from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import CONTACT_CAP_24H as CAP_24H_LIMIT
from sampark.allocator.constants import CONTACT_CAP_7D as CAP_7D_LIMIT
from sampark.allocator.constants import GRANT_TTL_HOURS, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.allocator.reason_codes import (
    CONTACT_CAP_24H,
    CONTACT_CAP_7D,
    CONTACT_SLOT_TAKEN,
    CUSTOMER_MARGIN_EXHAUSTED,
    MERCHANT_MARGIN_EXHAUSTED,
)
from sampark.budget.contact import CAPACITY_CONSUMING_STATES, RELEASING_STATES
from sampark.budget.margin import customer_margin_budget_paise, downgrade_to_fit, remaining_paise
from sampark.budget.windows import next_window_start
from sampark.contracts import Grant, GrantRequest, GrantState, RiskItem

NS_GRANT = uuid.UUID("6f5f4b6a-6b1e-4c2e-9a3e-2c1e9f9a1a11")

MERCHANT_ID = "merchant-sim"


# --- issuance protocol -------------------------------------------------


@dataclass(frozen=True)
class GrantIssued:
    grant: Grant


@dataclass(frozen=True)
class BudgetDenial:
    reason_code: str
    next_eligible_at: datetime | None = None


IssuanceResult = GrantIssued | BudgetDenial


class GrantIssuer(Protocol):
    def issue_grant(
        self,
        conn: Any,
        candidate: Candidate,
        effective_incentive_bps: int,
        decision_at: datetime,
        run_seed_risk_ids: frozenset[str] | None = None,
    ) -> IssuanceResult: ...


# --- in-memory bookkeeping records (test/reference double only) --------


@dataclass
class _PoolRecord:
    budget_paise: int
    reserved_paise: int = 0
    spent_paise: int = 0


@dataclass
class _ClaimRecord:
    claim_id: uuid.UUID
    customer_id: str
    window_id: date
    grant_id: uuid.UUID
    state: str
    claimed_at: datetime
    released_at: datetime | None = None


@dataclass
class _GrantRecord:
    grant: Grant
    request_id: uuid.UUID
    customer_id: str
    window_id: date
    claim_id: uuid.UUID


class InMemoryMediationLedger:
    """Reference/test double standing in for BOTH the psycopg.Connection
    the real issuance transaction would take AND the MediationLedgerView
    hard-policy rules read through. Populated once from the full Phase 1
    ledger (risk_items) at Arm B runner startup; mutated only through
    InMemoryGrantIssuer.issue_grant and sampark.mediation.lifecycle's
    execute/confirm/rollback/expire helpers.
    """

    def __init__(
        self,
        risk_items_by_customer: Mapping[str, tuple[RiskItem, ...]],
        merchant_id: str = MERCHANT_ID,
        merchant_budget_paise_per_window: int = MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
    ) -> None:
        self._risk_items_by_customer = dict(risk_items_by_customer)
        self._merchant_id = merchant_id
        self._merchant_budget_paise_per_window = merchant_budget_paise_per_window

        self._grant_requests: dict[uuid.UUID, GrantRequest] = {}
        self._grants_by_request_id: dict[uuid.UUID, _GrantRecord] = {}
        self._grants_by_grant_id: dict[uuid.UUID, _GrantRecord] = {}
        self._grants_by_customer: dict[str, list[uuid.UUID]] = {}  # customer_id -> [request_id, ...]
        self._claims: dict[tuple[str, date], list[_ClaimRecord]] = {}
        self._merchant_pools: dict[tuple[str, date], _PoolRecord] = {}
        self._customer_pools: dict[tuple[str, date], _PoolRecord] = {}

        # request_id -> set of risk_ids resolved terminal-DENIED (never
        # granted, never will be) — used by open_candidates_for_customer.
        self._terminally_denied_risk_ids: set[str] = set()

    # --- MediationLedgerView -------------------------------------------

    def optouts_by_channel(self, customer_id: str) -> Mapping[str, str]:
        return {}  # Phase 1 seeds ContactState.optouts_by_channel = {} for every customer

    def consent_scopes(self, customer_id: str) -> Mapping[str, Mapping[str, str]]:
        return {}  # Phase 1 seeds ContactState.consent_scopes = {} — never interpreted, see consent_scope.py

    def risk_items_for_customer(self, customer_id: str) -> tuple[RiskItem, ...]:
        return self._risk_items_by_customer.get(customer_id, ())

    def rolling_contact_counts(self, customer_id: str, decision_at: datetime) -> tuple[int, int]:
        """Design Lock §3.4: anchored on grant.send_after, strict '>'
        boundary — `send_after > decision_at - INTERVAL` counts, so an
        age of exactly 24h00m00s has aged out."""
        c24 = c7 = 0
        for request_id in self._grants_by_customer.get(customer_id, ()):
            record = self._grants_by_request_id[request_id]
            if record.grant.state.value not in CAPACITY_CONSUMING_STATES:
                continue
            if record.grant.send_after > decision_at - timedelta(hours=24):
                c24 += 1
            if record.grant.send_after > decision_at - timedelta(days=7):
                c7 += 1
        return c24, c7

    def contacts_made(self, customer_id: str, before: datetime) -> int:
        """SAMPARK's own count of contacts ACTUALLY SENT (state
        EXECUTING or CONFIRMED — not merely RESERVED, and not a
        ROLLED_BACK/EXPIRED reservation that never reached the
        customer) strictly before `before`. This is Design Lock §6.2's
        `n` — unbounded by the 24h/7d rolling window, unlike
        rolling_contact_counts()."""
        count = 0
        for request_id in self._grants_by_customer.get(customer_id, ()):
            record = self._grants_by_request_id[request_id]
            if record.grant.state not in (GrantState.EXECUTING, GrantState.CONFIRMED):
                continue
            if record.grant.send_after < before:
                count += 1
        return count

    def has_active_claim(self, customer_id: str, window_id: date) -> bool:
        for claim in self._claims.get((customer_id, window_id), ()):
            if claim.state in CAPACITY_CONSUMING_STATES:
                return True
        return False

    def open_candidates_for_customer(
        self, customer_id: str, decision_at: datetime, exclude_risk_id: str
    ) -> tuple[RiskItem, ...]:
        granted_confirmed_risk_ids = {
            self._grant_requests[record.request_id].risk_id
            for record in (
                self._grants_by_request_id[rid] for rid in self._grants_by_customer.get(customer_id, ())
            )
            if record.grant.state is GrantState.CONFIRMED
        }
        resolved = granted_confirmed_risk_ids | self._terminally_denied_risk_ids
        return tuple(
            item
            for item in self._risk_items_by_customer.get(customer_id, ())
            if item.risk_id != exclude_risk_id
            and item.risk_id not in resolved
            and item.detected_at <= decision_at
        )

    # --- bookkeeping helpers used by the mediation service --------------

    def mark_terminally_denied(self, risk_id: str) -> None:
        self._terminally_denied_risk_ids.add(risk_id)

    def get_grant_by_request_id(self, request_id: uuid.UUID) -> _GrantRecord | None:
        return self._grants_by_request_id.get(request_id)

    def get_grant_by_grant_id(self, grant_id: uuid.UUID) -> _GrantRecord | None:
        return self._grants_by_grant_id.get(grant_id)

    def _merchant_pool(self, window_id: date) -> _PoolRecord:
        key = (self._merchant_id, window_id)
        if key not in self._merchant_pools:
            self._merchant_pools[key] = _PoolRecord(budget_paise=self._merchant_budget_paise_per_window)
        return self._merchant_pools[key]

    def _customer_pool(self, customer_id: str, window_id: date) -> _PoolRecord:
        key = (customer_id, window_id)
        if key not in self._customer_pools:
            total_at_risk = sum(
                item.amount_paise for item in self._risk_items_by_customer.get(customer_id, ())
            )
            self._customer_pools[key] = _PoolRecord(
                budget_paise=customer_margin_budget_paise(total_at_risk)
            )
        return self._customer_pools[key]

    def remaining_margin_paise(self, customer_id: str, window_id: date) -> tuple[int, int]:
        """(merchant_remaining, customer_remaining) — a best-effort PREVIEW
        for the allocator to decide whether a downgraded candidate is
        still worth attempting (Design Lock §8: "if downgraded score <=
        0, abandon that candidate" — that check needs expected_net,
        which this budget-layer module does not compute; see
        sampark/allocator/greedy.py). The AUTHORITATIVE check is
        issue_grant's own internal downgrade, not this preview."""
        merchant_pool = self._merchant_pool(window_id)
        customer_pool = self._customer_pool(customer_id, window_id)
        return (
            remaining_paise(merchant_pool.budget_paise, merchant_pool.reserved_paise, merchant_pool.spent_paise),
            remaining_paise(customer_pool.budget_paise, customer_pool.reserved_paise, customer_pool.spent_paise),
        )

    def update_grant_state(self, grant_id: uuid.UUID, new_state: GrantState, at: datetime) -> Grant:
        record = self._grants_by_grant_id[grant_id]
        updated_grant = record.grant.model_copy(update={"state": new_state})
        record.grant = updated_grant

        claim = self._claim_for_grant(grant_id)
        if claim is not None:
            claim.state = new_state.value
            if new_state.value in RELEASING_STATES:
                claim.released_at = at
        return updated_grant

    def _claim_for_grant(self, grant_id: uuid.UUID) -> _ClaimRecord | None:
        for claims in self._claims.values():
            for claim in claims:
                if claim.grant_id == grant_id:
                    return claim
        return None

    def release_margin(self, request_id: uuid.UUID) -> None:
        """ROLLBACK/EXPIRE: release the full reservation from both pools."""
        record = self._grants_by_request_id[request_id]
        window_id = record.window_id
        ceiling = record.grant.incentive_ceiling_paise
        merchant_pool = self._merchant_pool(window_id)
        customer_pool = self._customer_pool(record.customer_id, window_id)
        merchant_pool.reserved_paise -= ceiling
        customer_pool.reserved_paise -= ceiling

    def settle_margin(self, request_id: uuid.UUID, actual_spend_paise: int) -> None:
        """CONFIRM: release the reservation, book the actual spend."""
        record = self._grants_by_request_id[request_id]
        window_id = record.window_id
        ceiling = record.grant.incentive_ceiling_paise
        merchant_pool = self._merchant_pool(window_id)
        customer_pool = self._customer_pool(record.customer_id, window_id)
        merchant_pool.reserved_paise -= ceiling
        merchant_pool.spent_paise += actual_spend_paise
        customer_pool.reserved_paise -= ceiling
        customer_pool.spent_paise += actual_spend_paise


class InMemoryGrantIssuer:
    """Reference implementation of the GrantIssuer protocol, matching
    the statement sequence in Design Lock §11 step-for-step, minus real
    locking (single-threaded only — see module docstring)."""

    def issue_grant(
        self,
        conn: InMemoryMediationLedger,
        candidate: Candidate,
        effective_incentive_bps: int,
        decision_at: datetime,
        run_seed_risk_ids: frozenset[str] | None = None,
    ) -> IssuanceResult:
        """`run_seed_risk_ids` is accepted for `GrantIssuer` protocol
        conformance (W5) but NOT used to scope the customer-margin
        pool here: `InMemoryMediationLedger._risk_items_by_customer` is
        already, by construction, exactly this run's own risk items
        (built fresh from a single `build_dataset(seed)` call — it has
        no shared-table cross-seed exposure the way Postgres's
        `risk_items` table does). Using this parameter to re-filter
        would be redundant at best and, for a caller that passes an
        incomplete set, would silently narrow an otherwise-correct
        pool — so it is deliberately ignored here, unlike the
        Postgres-backed issuer where it is the authoritative fix."""
        ledger = conn

        # (1) IDEMPOTENCY
        existing = ledger.get_grant_by_request_id(candidate.request.request_id)
        if existing is not None:
            return GrantIssued(grant=existing.grant)

        # (2) persist the signed request (no-op if already present)
        ledger._grant_requests.setdefault(candidate.request.request_id, candidate.request)

        # (3) LOCK ORDER: merchant pool, then customer pool
        merchant_pool = ledger._merchant_pool(candidate.window_id)
        customer_pool = ledger._customer_pool(candidate.customer_id, candidate.window_id)

        ceiling = downgrade_to_fit(
            requested_ceiling_paise=(
                candidate.risk_item.amount_paise * effective_incentive_bps
            )
            // 10_000,
            merchant_remaining_paise=remaining_paise(
                merchant_pool.budget_paise, merchant_pool.reserved_paise, merchant_pool.spent_paise
            ),
            customer_remaining_paise=remaining_paise(
                customer_pool.budget_paise, customer_pool.reserved_paise, customer_pool.spent_paise
            ),
        )
        next_eligible = next_window_start(candidate.window_id)
        if remaining_paise(
            merchant_pool.budget_paise, merchant_pool.reserved_paise, merchant_pool.spent_paise
        ) <= 0:
            return BudgetDenial(MERCHANT_MARGIN_EXHAUSTED, next_eligible)
        if remaining_paise(
            customer_pool.budget_paise, customer_pool.reserved_paise, customer_pool.spent_paise
        ) <= 0:
            return BudgetDenial(CUSTOMER_MARGIN_EXHAUSTED, next_eligible)

        # (4) AUTHORITATIVE CONTACT CAPS
        c24, c7 = ledger.rolling_contact_counts(candidate.customer_id, decision_at)
        if c24 >= CAP_24H_LIMIT:
            return BudgetDenial(CONTACT_CAP_24H, next_eligible)
        if c7 >= CAP_7D_LIMIT:
            return BudgetDenial(CONTACT_CAP_7D, next_eligible)

        # (5)+(6) THE CONTENDED WRITE — grant then claim, one active claim
        # per (customer_id, window_id) [in-memory stand-in for the partial
        # unique index; see module docstring re: no real concurrency guard]
        if ledger.has_active_claim(candidate.customer_id, candidate.window_id):
            return BudgetDenial(CONTACT_SLOT_TAKEN, next_eligible)

        grant_id = uuid.uuid5(NS_GRANT, str(candidate.request.request_id))
        send_after = candidate.proposed_send_after
        expires_at = send_after + timedelta(hours=GRANT_TTL_HOURS)
        grant = Grant(
            grant_id=grant_id,
            channel=candidate.request.requested_channel,
            incentive_ceiling_paise=ceiling,
            send_after=send_after,
            expires_at=expires_at,
            state=GrantState.RESERVED,
        )
        claim = _ClaimRecord(
            claim_id=uuid.uuid5(NS_GRANT, f"claim:{candidate.request.request_id}"),
            customer_id=candidate.customer_id,
            window_id=candidate.window_id,
            grant_id=grant_id,
            state="RESERVED",
            claimed_at=decision_at,
        )
        record = _GrantRecord(
            grant=grant,
            request_id=candidate.request.request_id,
            customer_id=candidate.customer_id,
            window_id=candidate.window_id,
            claim_id=claim.claim_id,
        )
        ledger._grants_by_request_id[candidate.request.request_id] = record
        ledger._grants_by_grant_id[grant_id] = record
        ledger._grants_by_customer.setdefault(candidate.customer_id, []).append(
            candidate.request.request_id
        )
        ledger._claims.setdefault((candidate.customer_id, candidate.window_id), []).append(claim)

        # (7) RESERVE MARGIN in both pools
        merchant_pool.reserved_paise += ceiling
        customer_pool.reserved_paise += ceiling

        # (8) CACHE — computed here for parity with the real transaction;
        # the durable authority remains the grants ledger itself (Design
        # Lock §3.6). Arm B does not maintain a separate ContactState
        # object, so this step is a documented no-op in the reference
        # implementation.

        return GrantIssued(grant=grant)
