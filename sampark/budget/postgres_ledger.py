"""PostgreSQL-backed MediationLedgerView + post-issuance lifecycle helpers
— Phase 4C-2, Blocker 1.

`sampark/budget/store.py`'s own docstring names this module's purpose
directly: "InMemoryMediationLedger ... used by Phase 4's unit tests and
by the Arm B batch runner UNTIL the owner-authored schema and issuance
transaction land." Both have now landed
(sampark/schema.sql, sampark/budget/issuance.py) — this is the
Postgres-backed equivalent of MediationLedgerView the same docstring
anticipated, so the OFFICIAL Arm B evidence runner reads policy state
from the same authoritative source `sampark.budget.issuance.issue_grant`
writes to, rather than from a Python dict issuance never updates.

Read-side (`optouts_by_channel`, `consent_scopes`,
`risk_items_for_customer`, `rolling_contact_counts`,
`has_active_claim`, `contacts_made`, `remaining_margin_paise`) queries
REAL committed Postgres state — every number here is authoritative,
never cached in this process.

`open_candidates_for_customer`'s "not terminally denied" half is the
one exception: a hard-policy DENY or a negative-expected-net DENY never
reaches `sampark.budget.issuance.issue_grant` at all (grant_requests is
only written inside that transaction, at step 2), so Postgres has no
row recording "this candidate was permanently denied before ever being
attempted." Recording that durably would mean extending the owner-owned
schema, which this phase does not do. Instead, exactly like
`InMemoryMediationLedger.mark_terminally_denied`, this class keeps an
in-process set for the lifetime of one Arm B run — legitimate working
memory for a single sequential evaluation, not a second durable source
of truth: nothing outside this one run's process ever reads it, and
every number that actually MUST be durable and authoritative (contact
caps, margin, claims) is still a live Postgres query, never this set.

`execute_grant` / `confirm_grant` / `rollback_grant` / `expire_grant`
mirror `sampark.mediation.lifecycle`'s exact legal-transition rules and
margin-settlement semantics (Design Lock §9), against real rows,
because `sampark.mediation.lifecycle` itself operates on
`InMemoryMediationLedger` bookkeeping that Postgres-issued grants never
populate — this is the same state machine, a different persistence
backend for it, not a new one.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Mapping

import psycopg
from psycopg.rows import dict_row

from sampark.allocator.constants import MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
from sampark.budget.contact import CAPACITY_CONSUMING_STATES, RELEASING_STATES
from sampark.budget.margin import customer_margin_budget_paise, remaining_paise
from sampark.contracts import RiskItem


class PostgresLifecycleError(RuntimeError):
    """Raised when a requested grant-state transition is illegal or the
    grant does not exist — mirrors sampark.mediation.lifecycle's
    IllegalTransitionError/UnknownGrantError, against real rows."""


class PostgresMediationLedger:
    """Read-side MediationLedgerView + the small set of extra read
    methods sampark.allocator.greedy actually calls
    (`contacts_made`, `remaining_margin_paise`, `mark_terminally_denied`)
    — the same surface InMemoryMediationLedger exposes, backed by real
    Postgres queries instead of a Python dict.

    `run_seed_risk_ids` — Phase 4C-2 hardening. `risk_items` is a
    SHARED table: Phase 1 deliberately loads multiple seeds' ledgers
    into it side by side and never cleans them up (committed-generator,
    persisted-output — the same pattern this project already uses for
    seeds 7/42, and for whatever a test's own fixtures leave behind).
    Two different seeds' populations can resolve the same synthetic
    person to the SAME customer_id (spec §8.2's identity resolution
    doing exactly what it is supposed to — "one human is one row" —
    working correctly across what happen to be two unrelated
    generations). `InMemoryMediationLedger` is naturally immune to this:
    it is built fresh, once, from a single `build_dataset(seed)` call,
    so it only ever sees ITS OWN seed's risk items for a customer.
    Without this scoping, `risk_items_for_customer` and
    `open_candidates_for_customer` would return OTHER seeds' risk items
    for a customer shared across seeds too — a real, reproducible
    parity break (Phase 4C-2 hardening investigation): a customer's
    genuinely-seed-42-only `interlock.dispute_open` status, and their
    fatigue-cost `other_open_amounts_paise`, must reflect ONLY seed 42's
    world, exactly as `sim.arm_a.run_arm_a` and the in-memory Arm B
    backend already do — not a merged cross-seed picture the "same
    underlying synthetic world" experimental protocol never intended.
    Every OTHER method on this class (grants/claims/margin queries) is
    already correctly seed-scoped without this: `grant_requests`/
    `grants`/`contact_slot_claims` only ever contain rows THIS run
    itself created, because request_ids are deterministically derived
    from (seed, agent_id, risk_id) and the transactional tables start
    empty for every run (Design Lock §11's issuance; Phase 4C-2's
    per-run cleanup)."""

    def __init__(
        self,
        conn: psycopg.Connection,
        merchant_id: str,
        run_seed_risk_ids: frozenset[str],
        merchant_budget_paise_per_window: int = MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW,
    ) -> None:
        self._conn = conn
        self._merchant_id = merchant_id
        self._run_seed_risk_ids = run_seed_risk_ids
        self._merchant_budget_paise_per_window = merchant_budget_paise_per_window
        self._terminally_denied_risk_ids: set[str] = set()

    def optouts_by_channel(self, customer_id: str) -> Mapping[str, str]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT optouts_by_channel FROM contact_states WHERE customer_id = %s", (customer_id,))
            row = cur.fetchone()
        return row[0] if row else {}

    def consent_scopes(self, customer_id: str) -> Mapping[str, Mapping[str, str]]:
        with self._conn.cursor() as cur:
            cur.execute("SELECT consent_scopes FROM contact_states WHERE customer_id = %s", (customer_id,))
            row = cur.fetchone()
        return row[0] if row else {}

    def risk_items_for_customer(self, customer_id: str) -> tuple[RiskItem, ...]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT risk_id, source, amount_paise, root_cause, detected_at "
                "FROM risk_items WHERE customer_id = %s AND risk_id = ANY(%s)",
                (customer_id, list(self._run_seed_risk_ids)),
            )
            rows = cur.fetchall()
        return tuple(
            RiskItem(risk_id=r[0], source=r[1], amount_paise=r[2], root_cause=r[3], detected_at=r[4])
            for r in rows
        )

    def rolling_contact_counts(self, customer_id: str, decision_at: datetime) -> tuple[int, int]:
        """Design Lock §3.4 — identical query to
        sampark/budget/issuance.py step 4, read-only (no FOR UPDATE
        here; the authoritative lock happens inside issue_grant itself
        — this is a best-effort pre-check, same status as the in-memory
        reference's own rolling_contact_counts, Design Lock §3.2."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT "
                "  count(*) FILTER (WHERE g.send_after > %(decision_at)s - INTERVAL '24 hours'), "
                "  count(*) FILTER (WHERE g.send_after > %(decision_at)s - INTERVAL '7 days') "
                "FROM grants g JOIN grant_requests r ON r.request_id = g.request_id "
                "WHERE r.customer_id = %(customer_id)s AND g.state IN ('RESERVED','EXECUTING','CONFIRMED')",
                {"decision_at": decision_at, "customer_id": customer_id},
            )
            c24, c7 = cur.fetchone()
        return c24, c7

    def has_active_claim(self, customer_id: str, window_id: date) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM contact_slot_claims WHERE customer_id = %s AND window_id = %s "
                "AND state IN ('RESERVED','EXECUTING','CONFIRMED'))",
                (customer_id, window_id),
            )
            (exists,) = cur.fetchone()
        return bool(exists)

    def contacts_made(self, customer_id: str, before: datetime) -> int:
        """Design Lock §6.2's `n` — count of grants ACTUALLY SENT
        (EXECUTING or CONFIRMED, not merely RESERVED) strictly before
        `before`. Identical definition to
        InMemoryMediationLedger.contacts_made."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM grants g JOIN grant_requests r ON r.request_id = g.request_id "
                "WHERE r.customer_id = %s AND g.state IN ('EXECUTING','CONFIRMED') AND g.send_after < %s",
                (customer_id, before),
            )
            (count,) = cur.fetchone()
        return count

    def open_candidates_for_customer(
        self, customer_id: str, decision_at: datetime, exclude_risk_id: str
    ) -> tuple[RiskItem, ...]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT r_item.risk_id, r_item.source, r_item.amount_paise, r_item.root_cause, r_item.detected_at "
                "FROM risk_items r_item "
                "WHERE r_item.customer_id = %s AND r_item.risk_id != %s AND r_item.detected_at <= %s "
                "AND r_item.risk_id = ANY(%s) "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM grant_requests req JOIN grants g ON g.request_id = req.request_id "
                "  WHERE req.risk_id = r_item.risk_id AND g.state = 'CONFIRMED'"
                ")",
                (customer_id, exclude_risk_id, decision_at, list(self._run_seed_risk_ids)),
            )
            rows = cur.fetchall()
        return tuple(
            RiskItem(risk_id=r[0], source=r[1], amount_paise=r[2], root_cause=r[3], detected_at=r[4])
            for r in rows
            if r[0] not in self._terminally_denied_risk_ids
        )

    def remaining_margin_paise(self, customer_id: str, window_id: date) -> tuple[int, int]:
        """Best-effort PREVIEW, same status as
        InMemoryMediationLedger.remaining_margin_paise — the
        AUTHORITATIVE check is issue_grant's own internal downgrade.

        Phase 4C-2 hardening fix: both budget_windows and
        customer_margin_windows rows are created LAZILY, inside
        issue_grant's own transaction (Design Lock §11 step 3) — the
        FIRST time a candidate for that (merchant|customer, window)
        pair is actually issued. Before this fix, a not-yet-existing
        row was read as "0 remaining", so EVERY candidate's first-ever
        resolution in a window saw customer_remaining=0 and got
        downgraded to a 0-paise ceiling regardless of the real pool
        size — silently zeroing incentive_bps for the large majority of
        incentive-bearing grants (in the seed-42 investigation: exactly
        every cart_recovery_agent and mandate_recovery_agent outcome,
        4,395 of 10,298). A row that does not exist yet is not a pool
        that is exhausted; it is a pool that has not been CREATED yet,
        and issue_grant will create it with the FULL configured budget
        (Design Lock §14.3) the moment it is actually needed — so the
        preview must compute that same full-budget value, not zero."""
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT margin_budget_paise, margin_reserved_paise, margin_spent_paise "
                "FROM budget_windows WHERE merchant_id = %s AND window_id = %s",
                (self._merchant_id, window_id),
            )
            merchant_row = cur.fetchone()
            cur.execute(
                "SELECT margin_budget_paise, margin_reserved_paise, margin_spent_paise "
                "FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s",
                (customer_id, window_id),
            )
            customer_row = cur.fetchone()

            if merchant_row is not None:
                merchant_remaining = remaining_paise(*merchant_row)
            else:
                merchant_remaining = self._merchant_budget_paise_per_window

            if customer_row is not None:
                customer_remaining = remaining_paise(*customer_row)
            else:
                cur.execute(
                    "SELECT COALESCE(SUM(amount_paise), 0) FROM risk_items "
                    "WHERE customer_id = %s AND risk_id = ANY(%s)",
                    (customer_id, list(self._run_seed_risk_ids)),
                )
                (customer_total_at_risk,) = cur.fetchone()
                customer_remaining = customer_margin_budget_paise(customer_total_at_risk)

        return merchant_remaining, customer_remaining

    def mark_terminally_denied(self, risk_id: str) -> None:
        self._terminally_denied_risk_ids.add(risk_id)


def seed_budget_window(
    conn: psycopg.Connection, merchant_id: str, window_id: date, margin_budget_paise: int
) -> None:
    """Pre-seed a budget_windows row with an EXPLICIT budget for one
    window — supports Blocker 2's merchant-margin ablation (× 0.5)
    without touching sampark/budget/issuance.py: that module's own
    budget_windows INSERT is `ON CONFLICT (merchant_id, window_id) DO
    NOTHING`, so a row pre-seeded here with the ablation's budget is
    left untouched by issuance and used as-is."""
    from sampark.budget.issuance import _budget_window_id  # local import: avoids a cycle

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (merchant_id, window_id) DO NOTHING",
            (_budget_window_id(merchant_id, window_id), merchant_id, window_id, margin_budget_paise),
        )


def _require_row(conn: psycopg.Connection, grant_id: uuid.UUID) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT g.grant_id, g.request_id, g.budget_window_id, g.incentive_ceiling_paise, "
            "g.send_after, g.state, r.customer_id "
            "FROM grants g JOIN grant_requests r ON r.request_id = g.request_id WHERE g.grant_id = %s",
            (grant_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise PostgresLifecycleError(f"unknown grant_id: {grant_id!r}")
    return row


def _window_id_for(send_after: datetime) -> date:
    from sampark.budget.windows import window_id_for

    return window_id_for(send_after)


def execute_grant(conn: psycopg.Connection, grant_id: uuid.UUID, at: datetime) -> None:
    """RESERVED -> EXECUTING. Idempotent: a no-op if already EXECUTING."""
    row = _require_row(conn, grant_id)
    if row["state"] == "EXECUTING":
        return
    if row["state"] != "RESERVED":
        raise PostgresLifecycleError(f"{row['state']} -> EXECUTING is not a legal transition (grant_id={grant_id!r})")
    with conn.cursor() as cur:
        cur.execute("UPDATE grants SET state = 'EXECUTING' WHERE grant_id = %s", (grant_id,))
        cur.execute(
            "UPDATE contact_slot_claims SET state = 'EXECUTING' WHERE grant_id = %s", (grant_id,)
        )


def confirm_grant(conn: psycopg.Connection, grant_id: uuid.UUID, at: datetime, actual_spend_paise: int) -> None:
    """EXECUTING -> CONFIRMED. Settles both margin pools to actual spend
    (Design Lock §2), exactly like sampark.mediation.lifecycle.confirm."""
    row = _require_row(conn, grant_id)
    if row["state"] == "CONFIRMED":
        return
    if row["state"] != "EXECUTING":
        raise PostgresLifecycleError(f"{row['state']} -> CONFIRMED is not a legal transition (grant_id={grant_id!r})")
    window_id = _window_id_for(row["send_after"])
    ceiling = row["incentive_ceiling_paise"]
    with conn.cursor() as cur:
        cur.execute("UPDATE grants SET state = 'CONFIRMED' WHERE grant_id = %s", (grant_id,))
        cur.execute(
            "UPDATE contact_slot_claims SET state = 'CONFIRMED' WHERE grant_id = %s", (grant_id,)
        )
        cur.execute(
            "UPDATE budget_windows SET margin_reserved_paise = margin_reserved_paise - %s, "
            "margin_spent_paise = margin_spent_paise + %s WHERE budget_window_id = %s",
            (ceiling, actual_spend_paise, row["budget_window_id"]),
        )
        cur.execute(
            "UPDATE customer_margin_windows SET margin_reserved_paise = margin_reserved_paise - %s, "
            "margin_spent_paise = margin_spent_paise + %s WHERE customer_id = %s AND window_id = %s",
            (ceiling, actual_spend_paise, row["customer_id"], window_id),
        )


def _release(conn: psycopg.Connection, grant_id: uuid.UUID, at: datetime, target_state: str) -> None:
    row = _require_row(conn, grant_id)
    if row["state"] == target_state:
        return
    legal = {"RESERVED": {"ROLLED_BACK", "EXPIRED"}, "EXECUTING": {"ROLLED_BACK"}}
    if target_state not in legal.get(row["state"], set()):
        raise PostgresLifecycleError(
            f"{row['state']} -> {target_state} is not a legal transition (grant_id={grant_id!r})"
        )
    window_id = _window_id_for(row["send_after"])
    ceiling = row["incentive_ceiling_paise"]
    with conn.cursor() as cur:
        cur.execute("UPDATE grants SET state = %s WHERE grant_id = %s", (target_state, grant_id))
        cur.execute(
            "UPDATE contact_slot_claims SET state = %s, released_at = %s WHERE grant_id = %s",
            (target_state, at, grant_id),
        )
        cur.execute(
            "UPDATE budget_windows SET margin_reserved_paise = margin_reserved_paise - %s WHERE budget_window_id = %s",
            (ceiling, row["budget_window_id"]),
        )
        cur.execute(
            "UPDATE customer_margin_windows SET margin_reserved_paise = margin_reserved_paise - %s "
            "WHERE customer_id = %s AND window_id = %s",
            (ceiling, row["customer_id"], window_id),
        )


def rollback_grant(conn: psycopg.Connection, grant_id: uuid.UUID, at: datetime) -> None:
    _release(conn, grant_id, at, "ROLLED_BACK")


def expire_grant(conn: psycopg.Connection, grant_id: uuid.UUID, at: datetime) -> None:
    _release(conn, grant_id, at, "EXPIRED")
