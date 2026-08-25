"""Grant issuance — the SERIALIZABLE transaction, Design Lock §11.

HUMAN-OWNED (CLAUDE.md §3): the SERIALIZABLE grant-issuance transaction and
its concurrency test are hand-authored, per spec §18.0. This file implements
exactly the transaction boundary specified in the approved Phase 4 Design
Lock §11 and PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md §B — nothing here departs
from that design; it is the SQL translation of an already-approved decision,
not a new one.

Conforms to the `GrantIssuer` protocol in sampark/budget/store.py — the same
`GrantIssued` / `BudgetDenial` return types the in-memory reference
implementation uses, so `sampark/mediation/service.py` and
`sampark/allocator/greedy.py` need no changes to swap one issuer for the
other. `conn` is a real `psycopg.Connection`, not the in-memory ledger.

Determinism (Design Lock §16): every surrogate key this transaction may
create — `budget_window_id`, `customer_margin_window_id`, `claim_id` — is
derived with `uuid5`, not `uuid4`. None of these IDs ever appears in a
GrantDecision or a metric, so their determinism is not strictly required by
the "byte-identical decision log" guarantee, but it costs nothing and keeps
the whole system reproducible given a seed rather than carving out an
unexplained exception to the no-uuid4 rule.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import psycopg
import psycopg.errors
from psycopg.rows import dict_row

from sampark.allocator.candidate import Candidate
from sampark.allocator.constants import (
    CONTACT_CAP_24H as CAP_24H_LIMIT,
)
from sampark.allocator.constants import (
    CONTACT_CAP_7D as CAP_7D_LIMIT,
)
from sampark.allocator.constants import GRANT_TTL_HOURS, MAX_SERIALIZATION_RETRIES, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.allocator.reason_codes import (
    CONTACT_CAP_24H,
    CONTACT_CAP_7D,
    CONTACT_SLOT_TAKEN,
    CUSTOMER_MARGIN_EXHAUSTED,
    MERCHANT_MARGIN_EXHAUSTED,
)
from sampark.budget.margin import customer_margin_budget_paise, downgrade_to_fit, remaining_paise
from sampark.budget.store import MERCHANT_ID, NS_GRANT, BudgetDenial, GrantIssued, IssuanceResult
from sampark.budget.windows import next_window_start
from sampark.contracts import Grant, GrantState

# uuid5 namespaces for bookkeeping surrogate keys — see module docstring.
_NS_BUDGET_WINDOW = uuid.UUID("f1a2b3c4-1111-4a2b-8c3d-1e2f3a4b5c6d")
_NS_CUSTOMER_MARGIN_WINDOW = uuid.UUID("f1a2b3c4-2222-4a2b-8c3d-1e2f3a4b5c6d")
_NS_CLAIM = uuid.UUID("f1a2b3c4-3333-4a2b-8c3d-1e2f3a4b5c6d")


class _Denied(Exception):
    """Internal control-flow sentinel: raised inside the transaction to
    force a rollback and unwind to a BudgetDenial return, never surfaced
    to a caller."""

    def __init__(self, denial: BudgetDenial) -> None:
        self.denial = denial


def _budget_window_id(merchant_id: str, window_id) -> uuid.UUID:
    return uuid.uuid5(_NS_BUDGET_WINDOW, f"{merchant_id}:{window_id}")


def _customer_margin_window_id(customer_id: str, window_id) -> uuid.UUID:
    return uuid.uuid5(_NS_CUSTOMER_MARGIN_WINDOW, f"{customer_id}:{window_id}")


def _claim_id(grant_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_NS_CLAIM, str(grant_id))


def _row_to_grant(row: dict[str, Any]) -> Grant:
    return Grant(
        grant_id=row["grant_id"],
        channel=row["channel"],
        incentive_ceiling_paise=row["incentive_ceiling_paise"],
        send_after=row["send_after"],
        expires_at=row["expires_at"],
        state=GrantState(row["state"]),
    )


def _attempt_once(
    conn: psycopg.Connection,
    candidate: Candidate,
    effective_incentive_bps: int,
    decision_at: datetime,
    run_seed_risk_ids: frozenset[str] | None,
) -> IssuanceResult:
    request = candidate.request
    window_id = candidate.window_id
    customer_id = candidate.customer_id
    next_eligible = next_window_start(window_id)

    with conn.transaction():
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")

            # (1) IDEMPOTENCY. A committed grant for this request_id wins outright.
            cur.execute(
                "SELECT grant_id, channel, incentive_ceiling_paise, send_after, expires_at, state "
                "FROM grants WHERE request_id = %s",
                (request.request_id,),
            )
            existing = cur.fetchone()
            if existing is not None:
                return GrantIssued(grant=_row_to_grant(existing))

            # (2) Persist the signed request verbatim (no-op if already present).
            cur.execute(
                "INSERT INTO grant_requests "
                "(request_id, agent_id, customer_id, risk_id, intent, requested_channel, "
                " requested_max_incentive_bps, issued_at, signature) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (request_id) DO NOTHING",
                (
                    request.request_id, request.agent_id, request.customer_id, request.risk_id,
                    request.intent, request.requested_channel, request.requested_max_incentive_bps,
                    request.issued_at, request.signature,
                ),
            )

            # (3) LOCK ORDER: merchant pool, then customer pool. Always this
            #     order, every caller — the deadlock-freedom argument in
            #     Design Lock §11.2 depends on this being invariant.
            merchant_bw_id = _budget_window_id(MERCHANT_ID, window_id)
            cur.execute(
                "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (merchant_id, window_id) DO NOTHING",
                (merchant_bw_id, MERCHANT_ID, window_id, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW),
            )
            cur.execute(
                "SELECT budget_window_id, margin_budget_paise, margin_reserved_paise, margin_spent_paise "
                "FROM budget_windows WHERE merchant_id = %s AND window_id = %s FOR UPDATE",
                (MERCHANT_ID, window_id),
            )
            merchant_row = cur.fetchone()
            merchant_remaining = remaining_paise(
                merchant_row["margin_budget_paise"], merchant_row["margin_reserved_paise"],
                merchant_row["margin_spent_paise"],
            )

            # W5: scoped to THIS RUN's own risk_id set when the caller
            # provides one. risk_items is a SHARED table across every
            # seed ever loaded into this Postgres instance (Phase 1's
            # committed-generator pattern) — an unscoped SUM here would
            # include another seed's risk items for any customer_id two
            # seeds' identity resolution happens to share (spec §8.2),
            # inflating this run's customer margin pool beyond what a
            # single-seed evaluation should ever see. Same defect class
            # as, and now consistent with,
            # PostgresMediationLedger.remaining_margin_paise's own
            # run_seed_risk_ids scoping (sampark/budget/postgres_ledger.py).
            #
            # `run_seed_risk_ids is None` preserves the ORIGINAL unscoped
            # query — required so this parameter's addition does not
            # break any caller unaware of it (most notably the
            # human-owned tests/test_concurrent_grant_issuance.py, which
            # never passes it and must not be modified). Every
            # production caller (sim/arm_b.py, the official evidence
            # path) explicitly passes the real set.
            if run_seed_risk_ids is None:
                cur.execute(
                    "SELECT COALESCE(SUM(amount_paise), 0) AS total FROM risk_items WHERE customer_id = %s",
                    (customer_id,),
                )
            else:
                cur.execute(
                    "SELECT COALESCE(SUM(amount_paise), 0) AS total FROM risk_items "
                    "WHERE customer_id = %s AND risk_id = ANY(%s)",
                    (customer_id, list(run_seed_risk_ids)),
                )
            customer_total_at_risk = cur.fetchone()["total"]
            customer_cmw_id = _customer_margin_window_id(customer_id, window_id)
            cur.execute(
                "INSERT INTO customer_margin_windows "
                "(customer_margin_window_id, customer_id, window_id, margin_budget_paise) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (customer_id, window_id) DO NOTHING",
                (customer_cmw_id, customer_id, window_id, customer_margin_budget_paise(customer_total_at_risk)),
            )
            cur.execute(
                "SELECT customer_margin_window_id, margin_budget_paise, margin_reserved_paise, margin_spent_paise "
                "FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s FOR UPDATE",
                (customer_id, window_id),
            )
            customer_row = cur.fetchone()
            customer_remaining = remaining_paise(
                customer_row["margin_budget_paise"], customer_row["margin_reserved_paise"],
                customer_row["margin_spent_paise"],
            )

            if merchant_remaining <= 0:
                raise _Denied(BudgetDenial(MERCHANT_MARGIN_EXHAUSTED, next_eligible))
            if customer_remaining <= 0:
                raise _Denied(BudgetDenial(CUSTOMER_MARGIN_EXHAUSTED, next_eligible))

            ceiling = downgrade_to_fit(
                requested_ceiling_paise=(candidate.risk_item.amount_paise * effective_incentive_bps) // 10_000,
                merchant_remaining_paise=merchant_remaining,
                customer_remaining_paise=customer_remaining,
            )

            # (4) AUTHORITATIVE CONTACT CAPS. This read is what SERIALIZABLE
            #     protects — no unique index can replace it.
            cur.execute(
                "SELECT "
                "  count(*) FILTER (WHERE g.send_after > %(decision_at)s - INTERVAL '24 hours') AS c24, "
                "  count(*) FILTER (WHERE g.send_after > %(decision_at)s - INTERVAL '7 days')   AS c7 "
                "FROM grants g JOIN grant_requests r ON r.request_id = g.request_id "
                "WHERE r.customer_id = %(customer_id)s "
                "  AND g.state IN ('RESERVED','EXECUTING','CONFIRMED')",
                {"decision_at": decision_at, "customer_id": customer_id},
            )
            caps = cur.fetchone()
            if caps["c24"] >= CAP_24H_LIMIT:
                raise _Denied(BudgetDenial(CONTACT_CAP_24H, next_eligible))
            if caps["c7"] >= CAP_7D_LIMIT:
                raise _Denied(BudgetDenial(CONTACT_CAP_7D, next_eligible))

            # (5) GRANT first, so the claim's FK is satisfied immediately
            #     (no DEFERRABLE constraint anywhere in the schema).
            grant_id = uuid.uuid5(NS_GRANT, str(request.request_id))
            send_after = candidate.proposed_send_after
            expires_at = send_after + timedelta(hours=GRANT_TTL_HOURS)
            cur.execute(
                "INSERT INTO grants "
                "(grant_id, request_id, budget_window_id, channel, incentive_ceiling_paise, "
                " send_after, expires_at, state) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'RESERVED')",
                (
                    grant_id, request.request_id, merchant_row["budget_window_id"], request.requested_channel,
                    ceiling, send_after, expires_at,
                ),
            )

            # (6) THE CONTENDED WRITE. Partial unique index fires here.
            claim_id = _claim_id(grant_id)
            try:
                cur.execute(
                    "INSERT INTO contact_slot_claims (claim_id, customer_id, window_id, grant_id, state, claimed_at) "
                    "VALUES (%s, %s, %s, %s, 'RESERVED', %s)",
                    (claim_id, customer_id, window_id, grant_id, decision_at),
                )
            except psycopg.errors.UniqueViolation as exc:
                constraint = (exc.diag.constraint_name or "").lower()
                if "active_uniq" in constraint:
                    raise _Denied(BudgetDenial(CONTACT_SLOT_TAKEN, next_eligible)) from exc
                raise  # an unexpected unique violation must never be silently swallowed

            # (7) RESERVE MARGIN in both pools.
            cur.execute(
                "UPDATE budget_windows SET margin_reserved_paise = margin_reserved_paise + %s "
                "WHERE budget_window_id = %s",
                (ceiling, merchant_row["budget_window_id"]),
            )
            cur.execute(
                "UPDATE customer_margin_windows SET margin_reserved_paise = margin_reserved_paise + %s "
                "WHERE customer_margin_window_id = %s",
                (ceiling, customer_row["customer_margin_window_id"]),
            )

            # (8) CACHE. Recomputed values, never blind increments — caps
            #     above are the freshly recomputed count, not a stale read.
            cur.execute(
                "UPDATE contact_states SET contacts_24h = %s, contacts_7d = %s, last_contact_at = %s "
                "WHERE customer_id = %s",
                (caps["c24"] + 1, caps["c7"] + 1, send_after, customer_id),
            )

            grant = Grant(
                grant_id=grant_id, channel=request.requested_channel, incentive_ceiling_paise=ceiling,
                send_after=send_after, expires_at=expires_at, state=GrantState.RESERVED,
            )
            return GrantIssued(grant=grant)


def issue_grant(
    conn: psycopg.Connection,
    candidate: Candidate,
    effective_incentive_bps: int,
    decision_at: datetime,
    run_seed_risk_ids: frozenset[str] | None = None,
) -> IssuanceResult:
    """Design Lock §11's exact transaction boundary against real
    PostgreSQL. Retries the WHOLE transaction (never a subset of
    statements) up to MAX_SERIALIZATION_RETRIES times on SQLSTATE 40001;
    a genuine claim conflict (23505 on the active-claim partial unique
    index) is a legitimate denial, not retried.

    `run_seed_risk_ids` (Phase 4C hardening, W5) — the complete set of
    risk_ids belonging to THIS run's own synthetic world, used ONLY to
    scope the customer-margin-budget query against cross-seed leakage
    (`risk_items` is a table shared across every seed ever loaded into
    this Postgres instance). Defaults to `None` (the pre-W5, unscoped
    query) rather than being required, SOLELY so this addition does not
    break `tests/test_concurrent_grant_issuance.py` — human-owned,
    explicitly not to be modified, and its single-customer fixture is
    unaffected by scoping either way. Every production caller
    (`sim/arm_b.py`, the official evidence path) explicitly passes the
    real set; omitting it anywhere else silently reverts to unscoped
    sizing, so new callers should pass it deliberately."""
    last_denial = BudgetDenial(CONTACT_SLOT_TAKEN, next_window_start(candidate.window_id))
    for _attempt in range(MAX_SERIALIZATION_RETRIES):
        try:
            return _attempt_once(conn, candidate, effective_incentive_bps, decision_at, run_seed_risk_ids)
        except _Denied as denied:
            return denied.denial
        except psycopg.errors.SerializationFailure:
            continue
    return last_denial


class PostgresGrantIssuer:
    """Adapts the module-level `issue_grant` function to the `GrantIssuer`
    Protocol (sampark/budget/store.py) — the same shape
    `InMemoryGrantIssuer` implements, so `sampark/allocator/greedy.py`
    and `sampark/mediation/service.py` accept either with zero changes.
    `conn` must be a real `psycopg.Connection`, passed explicitly by the
    caller (e.g. `allocate_window(..., conn=pg_conn)`) — it is NOT the
    same object as the read-only MediationLedgerView passed as `ledger`."""

    def issue_grant(
        self,
        conn: psycopg.Connection,
        candidate: Candidate,
        effective_incentive_bps: int,
        decision_at: datetime,
        run_seed_risk_ids: frozenset[str] | None = None,
    ) -> IssuanceResult:
        return issue_grant(conn, candidate, effective_incentive_bps, decision_at, run_seed_risk_ids)
