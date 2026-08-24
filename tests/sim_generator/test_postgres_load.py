"""Integration test: the generator against the real PostgreSQL 16 instance
(docker-compose.yml). Spec §18.1's Phase 1 exit criterion is checked in
memory in test_generator_reproducibility.py; this file checks the same
property survives an actual load into Postgres — "PostgreSQL is real in
Phase 1", not just a Pydantic-shaped in-memory object — and that the
loader (sim/persistence.py) handles primary-key conflicts explicitly
rather than via a blanket ON CONFLICT DO NOTHING.

Skipped (not failed) when no reachable Postgres is configured, since CI
(.github/workflows/ci.yml) does not run docker compose — this file is for
local development against the running container.
"""

from __future__ import annotations

import dataclasses

import psycopg
import pytest

from sampark.contracts import RiskItem
from sim.cli import build_dataset
from sim.persistence import (
    LedgerConflictError,
    PostgresConfig,
    PostgresConfigError,
    load_ledger,
)

_SEED_A = 42
_SEED_B = 43


def _connect_or_skip() -> psycopg.Connection:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip(f"Postgres not configured: {exc}")
    try:
        return psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc}")


@pytest.fixture()
def pg_conn():
    conn = _connect_or_skip()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.risk_items')")
            if cur.fetchone()[0] is None:
                pytest.skip("schema.sql has not been applied to this database")
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def ledger_a():
    _, _, ledger = build_dataset(_SEED_A)
    return ledger


@pytest.fixture(scope="module")
def ledger_b():
    _, _, ledger = build_dataset(_SEED_B)
    return ledger


def _risk_item_count(cur, risk_ids) -> int:
    cur.execute(
        "SELECT count(*) FROM risk_items WHERE risk_id = ANY(%s)", (list(risk_ids),)
    )
    return cur.fetchone()[0]


def test_loading_the_ledger_persists_the_expected_row_counts(pg_conn, ledger_a):
    load_ledger(pg_conn, ledger_a)

    with pg_conn.cursor() as cur:
        assert _risk_item_count(cur, [r.risk_id for r in ledger_a.risk_items]) == len(
            ledger_a.risk_items
        )
        cur.execute(
            "SELECT count(*) FROM customers WHERE customer_id = ANY(%s)",
            ([c.customer_id for c in ledger_a.customers],),
        )
        assert cur.fetchone()[0] == len(ledger_a.customers)


def test_loading_the_same_seed_twice_does_not_duplicate_rows(pg_conn, ledger_a):
    load_ledger(pg_conn, ledger_a)
    load_ledger(pg_conn, ledger_a)  # same seed, same ledger: must be a no-op

    with pg_conn.cursor() as cur:
        assert _risk_item_count(cur, [r.risk_id for r in ledger_a.risk_items]) == len(
            ledger_a.risk_items
        )


def test_persisted_risk_items_reference_a_persisted_customer(pg_conn, ledger_a):
    """The composite FK (risk_id, customer_id) -> risk_items in schema.sql
    is what actually enforces this; this test proves the loader satisfies
    it rather than merely not crashing."""
    load_ledger(pg_conn, ledger_a)

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM risk_items ri
            LEFT JOIN customers c ON c.customer_id = ri.customer_id
            WHERE ri.risk_id = ANY(%s) AND c.customer_id IS NULL
            """,
            ([r.risk_id for r in ledger_a.risk_items],),
        )
        assert cur.fetchone()[0] == 0


def test_loading_a_different_seed_does_not_silently_discard_rows(pg_conn, ledger_a, ledger_b):
    """The bug this whole fix targets: risk_id used to be positional only
    (risk-000000, ...), so seed B's rows would collide with seed A's and
    ON CONFLICT DO NOTHING would quietly keep only seed A's. risk_id now
    embeds the seed, so both datasets' rows must independently persist in
    full."""
    load_ledger(pg_conn, ledger_a)
    load_ledger(pg_conn, ledger_b)

    with pg_conn.cursor() as cur:
        assert _risk_item_count(cur, [r.risk_id for r in ledger_a.risk_items]) == len(
            ledger_a.risk_items
        )
        assert _risk_item_count(cur, [r.risk_id for r in ledger_b.risk_items]) == len(
            ledger_b.risk_items
        )
        # And the two seeds' risk_ids are, as expected, entirely disjoint.
        a_ids = {r.risk_id for r in ledger_a.risk_items}
        b_ids = {r.risk_id for r in ledger_b.risk_items}
        assert a_ids.isdisjoint(b_ids)


def test_a_conflicting_existing_row_raises_explicit_failure_and_writes_nothing(
    pg_conn, ledger_a
):
    """A genuine primary-key collision with different field values (the
    scenario ON CONFLICT DO NOTHING used to paper over) must raise, and
    must not write any part of the batch it was found in — including a
    brand-new row riding along in the same call, which is the only way to
    tell "aborted the whole batch" apart from "skipped the bad row"."""
    load_ledger(pg_conn, ledger_a)  # ensure the victim row already exists as-is

    victim = ledger_a.risk_items[0]
    tampered_victim = victim.model_copy(update={"amount_paise": victim.amount_paise + 1})

    brand_new_item = RiskItem(
        risk_id="risk-conflict-test-brand-new",
        source=victim.source,
        amount_paise=12345,
        root_cause=victim.root_cause,
        detected_at=victim.detected_at,
    )
    tampered_ledger = dataclasses.replace(
        ledger_a,
        risk_items=(tampered_victim, brand_new_item),
        risk_customer_map={
            **ledger_a.risk_customer_map,
            brand_new_item.risk_id: ledger_a.risk_customer_map[victim.risk_id],
        },
    )

    with pytest.raises(LedgerConflictError) as exc_info:
        load_ledger(pg_conn, tampered_ledger)
    assert victim.risk_id in str(exc_info.value)

    with pg_conn.cursor() as cur:
        cur.execute("SELECT amount_paise FROM risk_items WHERE risk_id = %s", (victim.risk_id,))
        stored_amount = cur.fetchone()[0]
        assert stored_amount == victim.amount_paise  # untouched, not the tampered value

        # The brand-new row in that same rejected batch must not exist
        # either — conflict detection aborts the whole load, not just the
        # offending row.
        assert _risk_item_count(cur, [brand_new_item.risk_id]) == 0
