"""PostgreSQL persistence — spec §7, PostgreSQL 16 is the at-risk ledger.

Loads a sim.ledger.Ledger into the hand-authored schema
(sampark/schema.sql): customers, contact_states, risk_items, in that
order (FK dependency order). This module never creates, alters, or
introspects a table — schema.sql is human-owned (CLAUDE.md §3) and is
applied separately and explicitly, before this ever runs.

Connection config comes only from the environment, mirroring
sampark/integrations/razorpay.py's RazorpayConfig.from_env pattern.

Conflict handling: a primary-key match against an existing row is checked
against that row's actual field values, not papered over. An identical
existing row is a safe idempotent no-op (re-loading the same seeded
dataset); a differing existing row is a genuine data-integrity problem —
most commonly two different seeded datasets colliding on an id that
should have been unique — and is raised as LedgerConflictError rather
than silently discarded via a blanket ON CONFLICT DO NOTHING. Nothing is
written until every table has been checked, so a conflict anywhere aborts
the whole load rather than leaving it partially applied.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Sequence

import psycopg

from sim.ledger import Ledger


class PostgresConfigError(RuntimeError):
    """Required Postgres connection environment variables are missing."""


class LedgerConflictError(RuntimeError):
    """An existing database row disagrees with the ledger being loaded.

    Raised instead of silently discarding data. The primary use case this
    guards against: risk_id (and, in principle, a hash-collided
    customer_id) is only guaranteed unique for one seeded generation —
    two different seeds writing to the same table must never resolve a
    same-id collision by quietly keeping whichever row got there first.
    """


@dataclass(frozen=True)
class PostgresConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls) -> "PostgresConfig":
        raw = {
            "POSTGRES_HOST": os.environ.get("POSTGRES_HOST", ""),
            "POSTGRES_PORT": os.environ.get("POSTGRES_PORT", ""),
            "POSTGRES_DB": os.environ.get("POSTGRES_DB", ""),
            "POSTGRES_USER": os.environ.get("POSTGRES_USER", ""),
            "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        }
        missing = [name for name, value in raw.items() if not value]
        if missing:
            raise PostgresConfigError(
                f"Missing required Postgres environment variable(s): {missing}"
            )
        return cls(
            host=raw["POSTGRES_HOST"],
            port=int(raw["POSTGRES_PORT"]),
            dbname=raw["POSTGRES_DB"],
            user=raw["POSTGRES_USER"],
            password=raw["POSTGRES_PASSWORD"],
        )

    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password}"
        )


def _fetch_existing(
    cur: psycopg.Cursor, table: str, key_column: str, columns: Sequence[str], keys: Sequence[Any]
) -> dict[Any, tuple]:
    """Existing rows for `keys`, keyed by their primary key value.

    Each value is a tuple of `columns` in the given order — the same
    shape a caller builds for its own candidate rows, so the two can be
    compared with plain `==`.
    """
    if not keys:
        return {}
    cur.execute(
        f"SELECT {key_column}, {', '.join(columns)} FROM {table} WHERE {key_column} = ANY(%s)",
        (list(keys),),
    )
    return {row[0]: tuple(row[1:]) for row in cur.fetchall()}


def _partition(
    candidates: dict[Any, tuple], existing: dict[Any, tuple]
) -> tuple[list[Any], list[Any]]:
    """Split candidate keys into (new_keys, conflicting_keys).

    A key already present with an identical value is neither — it is a
    safe no-op, left out of both lists.
    """
    new_keys: list[Any] = []
    conflicting_keys: list[Any] = []
    for key, value in candidates.items():
        if key not in existing:
            new_keys.append(key)
        elif existing[key] != value:
            conflicting_keys.append(key)
    return new_keys, conflicting_keys


def load_ledger(conn: psycopg.Connection, ledger: Ledger) -> None:
    """Insert customers, contact_states, and risk_items.

    Every table is checked against the database before anything is
    written: a candidate row identical to the existing row at that
    primary key is a safe idempotent no-op (re-loading the same seeded
    dataset); a candidate row that disagrees with an existing row at the
    same primary key raises LedgerConflictError, and nothing from this
    call is written. Only genuinely new rows are inserted, via a plain
    INSERT — no ON CONFLICT clause, so the primary-key constraint itself
    still does real work rather than being bypassed.

    Batched via `executemany` (one round trip per table for the check,
    one for the insert) — at 20,000 risk items, one-row-at-a-time
    round trips made this the dominant cost of running the generator.
    """
    customer_candidates = {
        c.customer_id: (c.phone_hash, c.email_hash) for c in ledger.customers
    }
    contact_state_candidates = {
        customer_id: (
            state.contacts_24h,
            state.contacts_7d,
            state.last_contact_at,
            state.optouts_by_channel,
            state.consent_scopes,
            state.fatigue_score,
        )
        for customer_id, state in ledger.contact_states.items()
    }
    risk_item_candidates = {
        r.risk_id: (
            ledger.risk_customer_map[r.risk_id],
            r.source,
            r.amount_paise,
            r.root_cause,
            r.detected_at,
        )
        for r in ledger.risk_items
    }

    with conn.cursor() as cur:
        existing_customers = _fetch_existing(
            cur, "customers", "customer_id", ["phone_hash", "email_hash"],
            list(customer_candidates),
        )
        existing_contact_states = _fetch_existing(
            cur, "contact_states", "customer_id",
            [
                "contacts_24h", "contacts_7d", "last_contact_at",
                "optouts_by_channel", "consent_scopes", "fatigue_score",
            ],
            list(contact_state_candidates),
        )
        existing_risk_items = _fetch_existing(
            cur, "risk_items", "risk_id",
            ["customer_id", "source", "amount_paise", "root_cause", "detected_at"],
            list(risk_item_candidates),
        )

        new_customer_ids, customer_conflicts = _partition(customer_candidates, existing_customers)
        new_contact_state_ids, contact_state_conflicts = _partition(
            contact_state_candidates, existing_contact_states
        )
        new_risk_ids, risk_item_conflicts = _partition(risk_item_candidates, existing_risk_items)

        conflicts_by_table = {
            "customers": customer_conflicts,
            "contact_states": contact_state_conflicts,
            "risk_items": risk_item_conflicts,
        }
        conflicts_by_table = {t: keys for t, keys in conflicts_by_table.items() if keys}
        if conflicts_by_table:
            details = "; ".join(
                f"{table}: {keys}" for table, keys in conflicts_by_table.items()
            )
            raise LedgerConflictError(
                f"Existing row(s) disagree with the ledger being loaded — "
                f"nothing was written. {details}"
            )

        new_customers = {cid: customer_candidates[cid] for cid in new_customer_ids}
        cur.executemany(
            """
            INSERT INTO customers (customer_id, phone_hash, email_hash)
            VALUES (%s, %s, %s)
            """,
            [
                (customer_id, phone_hash, email_hash)
                for customer_id, (phone_hash, email_hash) in new_customers.items()
            ],
        )

        new_contact_states = {
            cid: contact_state_candidates[cid] for cid in new_contact_state_ids
        }
        cur.executemany(
            """
            INSERT INTO contact_states (
                customer_id, contacts_24h, contacts_7d, last_contact_at,
                optouts_by_channel, consent_scopes, fatigue_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    customer_id,
                    contacts_24h,
                    contacts_7d,
                    last_contact_at,
                    json.dumps(optouts_by_channel),
                    json.dumps(consent_scopes),
                    fatigue_score,
                )
                for customer_id, (
                    contacts_24h, contacts_7d, last_contact_at,
                    optouts_by_channel, consent_scopes, fatigue_score,
                ) in new_contact_states.items()
            ],
        )

        new_risk_items = {rid: risk_item_candidates[rid] for rid in new_risk_ids}
        cur.executemany(
            """
            INSERT INTO risk_items (
                risk_id, customer_id, source, amount_paise, root_cause, detected_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (risk_id, customer_id, source, amount_paise, root_cause, detected_at)
                for risk_id, (
                    customer_id, source, amount_paise, root_cause, detected_at,
                ) in new_risk_items.items()
            ],
        )
    conn.commit()
