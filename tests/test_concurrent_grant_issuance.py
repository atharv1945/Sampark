"""tests/test_concurrent_grant_issuance.py — the most important test in the
project (CLAUDE.md §12).

Human-owned (CLAUDE.md §3). Implements exactly the concurrency-test design
in the approved Phase 4 Design Lock §12 and
PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md §C: 50 concurrent, genuinely signed
requests race for the SAME customer's SAME contact window, with only one
remaining slot under the rolling-24h cap. Exactly one must be granted.

This proves sampark/budget/issuance.py's real SERIALIZABLE transaction
against real PostgreSQL — not the in-memory reference double, which gives
no concurrency guarantee at all (see sampark/budget/store.py's docstring).

A companion negative control (test_negative_control_*) proves the main test
actually detects the race it claims to: with the partial unique index
dropped and the transaction downgraded to READ COMMITTED, the identical
race produces more than one grant. The schema is restored immediately
after, in a `finally` block, regardless of outcome — this file never
leaves the production schema weakened.
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from typing import Any

import psycopg
import pytest

from sampark.allocator.candidate import build_candidate
from sampark.allocator.reason_codes import CONTACT_CAP_24H, CONTACT_CAP_7D, CONTACT_SLOT_TAKEN
from sampark.budget.issuance import issue_grant
from sampark.budget.store import BudgetDenial, GrantIssued
from sampark.contracts import GrantRequest, RiskItem
from sampark.registry.keys import generate_keypair
from sampark.registry.scope import evaluate_scope
from sampark.registry.store import PostgresAgentRepository, PostgresRiskItemRepository
from sim.persistence import PostgresConfig, PostgresConfigError

pytestmark = pytest.mark.postgres

N_WORKERS = 50
DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
SEND_AFTER = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)


def _connect_or_skip() -> psycopg.Connection:
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip(f"Postgres not configured: {exc}")
    try:
        conn = psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.contact_slot_claims')")
        if cur.fetchone()[0] is None:
            conn.close()
            pytest.skip("Phase 4 schema additions have not been applied to this database")
    return conn


def _conninfo() -> str:
    return PostgresConfig.from_env().conninfo()


class RaceFixture:
    def __init__(self, conn: psycopg.Connection, n: int) -> None:
        self.conn = conn
        self.n = n
        self.agent_id = f"race-agent-{uuid.uuid4().hex[:12]}"
        self.customer_id = f"race-cust-{uuid.uuid4().hex[:12]}"
        self.keypair = generate_keypair()
        self.risk_items: list[RiskItem] = []
        self.requests: list[GrantRequest] = []

    def setup(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agents (agent_id, public_key, publisher, state, strike_count) "
                "VALUES (%s, %s, %s, 'ACTIVE', 0)",
                (self.agent_id, self.keypair.public_key_b64, "race-test"),
            )
            cur.execute(
                "INSERT INTO capability_scopes "
                "(agent_id, allowed_channels, allowed_intents, allowed_risk_sources, "
                " max_incentive_bps, max_requests_per_hour) VALUES (%s, %s, %s, %s, %s, %s)",
                (self.agent_id, '["whatsapp"]', '["cart_recovery"]', '["abandoned_checkout"]', 0, 100_000),
            )
            cur.execute("INSERT INTO customers (customer_id) VALUES (%s)", (self.customer_id,))
            cur.execute(
                "INSERT INTO contact_states (customer_id, contacts_24h, contacts_7d, "
                "optouts_by_channel, consent_scopes, fatigue_score) VALUES (%s, 0, 0, '{}', '{}', 0.0)",
                (self.customer_id,),
            )
            for i in range(self.n):
                risk_id = f"race-risk-{i:03d}-{uuid.uuid4().hex[:8]}"
                cur.execute(
                    "INSERT INTO risk_items (risk_id, customer_id, source, amount_paise, root_cause, detected_at) "
                    "VALUES (%s, %s, 'abandoned_checkout', %s, 'price_hesitation', %s)",
                    (risk_id, self.customer_id, 100_000, dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)),
                )
                self.risk_items.append(
                    RiskItem(
                        risk_id=risk_id, source="abandoned_checkout", amount_paise=100_000,
                        root_cause="price_hesitation", detected_at=dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc),
                    )
                )

        for item in self.risk_items:
            unsigned = GrantRequest(
                request_id=uuid.uuid4(), agent_id=self.agent_id, customer_id=self.customer_id,
                risk_id=item.risk_id, intent="cart_recovery", requested_channel="whatsapp",
                requested_max_incentive_bps=0, issued_at=dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc),
                signature="placeholder",
            )
            signature = self.keypair.sign(unsigned.canonical_bytes())
            self.requests.append(unsigned.model_copy(update={"signature": signature}))

    def teardown(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM contact_slot_claims WHERE customer_id = %s", (self.customer_id,))
            cur.execute(
                "DELETE FROM grants WHERE request_id IN "
                "(SELECT request_id FROM grant_requests WHERE customer_id = %s)",
                (self.customer_id,),
            )
            cur.execute("DELETE FROM grant_requests WHERE customer_id = %s", (self.customer_id,))
            cur.execute("DELETE FROM customer_margin_windows WHERE customer_id = %s", (self.customer_id,))
            cur.execute(
                "DELETE FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s",
                (SEND_AFTER.date(),),
            )
            cur.execute("DELETE FROM risk_items WHERE customer_id = %s", (self.customer_id,))
            cur.execute("DELETE FROM contact_states WHERE customer_id = %s", (self.customer_id,))
            cur.execute("DELETE FROM customers WHERE customer_id = %s", (self.customer_id,))
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (self.agent_id,))


@pytest.fixture()
def race_fixture():
    conn = _connect_or_skip()
    fixture = RaceFixture(conn, N_WORKERS)
    fixture.setup()
    try:
        yield fixture
    finally:
        fixture.teardown()
        conn.close()


def _run_worker(conninfo: str, request: GrantRequest, results: list, exceptions: list, index: int, barrier: threading.Barrier) -> None:
    conn = None
    try:
        conn = psycopg.connect(conninfo, connect_timeout=5)
        conn.autocommit = True
        agent_repo = PostgresAgentRepository(conn)
        risk_item_repo = PostgresRiskItemRepository(conn)

        barrier.wait(timeout=30)

        scope_decision = evaluate_scope(request, agent_repo, risk_item_repo)
        if scope_decision is not None:
            results[index] = scope_decision
            return

        record = risk_item_repo.get_risk_item(request.risk_id)
        candidate = build_candidate(request, record.risk_item, record.customer_id, SEND_AFTER)
        results[index] = issue_grant(conn, candidate, 0, DECISION_AT)
    except Exception as exc:  # noqa: BLE001 — captured for the "zero uncaught exceptions" assertion
        exceptions[index] = exc
    finally:
        if conn is not None:
            conn.close()


def _run_race(fixture: RaceFixture) -> tuple[list, list]:
    conninfo = _conninfo()
    results: list[Any] = [None] * fixture.n
    exceptions: list[Any] = [None] * fixture.n
    barrier = threading.Barrier(fixture.n)
    threads = [
        threading.Thread(
            target=_run_worker,
            args=(conninfo, fixture.requests[i], results, exceptions, i, barrier),
        )
        for i in range(fixture.n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results, exceptions


def test_fifty_concurrent_requests_produce_exactly_one_grant(race_fixture):
    results, exceptions = _run_race(race_fixture)

    # 4. Zero uncaught exceptions.
    assert all(e is None for e in exceptions), [repr(e) for e in exceptions if e is not None]

    granted = [r for r in results if isinstance(r, GrantIssued)]
    denied = [r for r in results if isinstance(r, BudgetDenial)]
    scope_denied = [r for r in results if r is not None and not isinstance(r, (GrantIssued, BudgetDenial))]

    assert len(scope_denied) == 0, "no request should be denied on scope alone — the fixture registers full scope"
    assert len(granted) == 1, f"expected exactly 1 grant, got {len(granted)}"
    assert len(denied) == race_fixture.n - 1

    for denial in denied:
        assert denial.reason_code in (CONTACT_CAP_24H, CONTACT_SLOT_TAKEN, CONTACT_CAP_7D)
        assert denial.next_eligible_at is not None

    conn = race_fixture.conn
    with conn.cursor() as cur:
        # 1. Exactly one active claim.
        cur.execute(
            "SELECT count(*) FROM contact_slot_claims WHERE customer_id = %s AND state IN "
            "('RESERVED','EXECUTING','CONFIRMED')",
            (race_fixture.customer_id,),
        )
        assert cur.fetchone()[0] == 1

        # 2. Exactly one grant row, referencing that claim, referencing a real request.
        cur.execute(
            "SELECT count(*) FROM grants g JOIN grant_requests r ON r.request_id = g.request_id "
            "WHERE r.customer_id = %s",
            (race_fixture.customer_id,),
        )
        assert cur.fetchone()[0] == 1

        cur.execute(
            "SELECT g.grant_id FROM grants g JOIN contact_slot_claims c ON c.grant_id = g.grant_id "
            "WHERE c.customer_id = %s",
            (race_fixture.customer_id,),
        )
        assert cur.fetchone()[0] == granted[0].grant.grant_id

        # Merchant budget not overspent; customer margin not overspent.
        cur.execute(
            "SELECT margin_budget_paise, margin_reserved_paise, margin_spent_paise "
            "FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = %s",
            (SEND_AFTER.date(),),
        )
        m_budget, m_reserved, m_spent = cur.fetchone()
        assert m_reserved + m_spent <= m_budget
        assert m_reserved == granted[0].grant.incentive_ceiling_paise  # exactly ONE grant's ceiling reserved

        cur.execute(
            "SELECT margin_budget_paise, margin_reserved_paise, margin_spent_paise "
            "FROM customer_margin_windows WHERE customer_id = %s AND window_id = %s",
            (race_fixture.customer_id, SEND_AFTER.date()),
        )
        c_budget, c_reserved, c_spent = cur.fetchone()
        assert c_reserved + c_spent <= c_budget
        assert c_reserved == granted[0].grant.incentive_ceiling_paise

        # 5. contact_states cache is correct: exactly 1, matching the single grant.
        cur.execute(
            "SELECT contacts_24h, contacts_7d FROM contact_states WHERE customer_id = %s",
            (race_fixture.customer_id,),
        )
        c24, c7 = cur.fetchone()
        assert c24 == 1
        assert c7 == 1

        # 6. Every failed attempt left no partial reservation: total
        #    contact_slot_claims rows for this customer equals exactly 1
        #    (a losing INSERT that hits the unique index leaves no row —
        #    it is rejected outright, not created-then-cleaned-up).
        cur.execute("SELECT count(*) FROM contact_slot_claims WHERE customer_id = %s", (race_fixture.customer_id,))
        assert cur.fetchone()[0] == 1

    # 7. Idempotent replay of the winning request creates no duplicate grant.
    winning_grant = granted[0].grant
    winning_request = next(
        req for req, res in zip(race_fixture.requests, results)
        if isinstance(res, GrantIssued) and res.grant.grant_id == winning_grant.grant_id
    )
    record = PostgresRiskItemRepository(conn).get_risk_item(winning_request.risk_id)
    replay_candidate = build_candidate(winning_request, record.risk_item, record.customer_id, SEND_AFTER)
    replay_result = issue_grant(conn, replay_candidate, 0, DECISION_AT)
    assert isinstance(replay_result, GrantIssued)
    assert replay_result.grant.grant_id == winning_grant.grant_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM grants g JOIN grant_requests r ON r.request_id = g.request_id "
            "WHERE r.customer_id = %s",
            (race_fixture.customer_id,),
        )
        assert cur.fetchone()[0] == 1  # still exactly one grant after the replay


# =============================================================================
# NEGATIVE CONTROL — proves the main test actually detects the race.
#
# Drops contact_slot_claims_active_uniq and runs the identical contended
# INSERT sequence under READ COMMITTED. Restored in `finally` regardless of
# outcome; never leaves the production schema weakened.
# =============================================================================


def _read_committed_naive_issue(conn: psycopg.Connection, customer_id: str, window_id, risk_id: str) -> None:
    """A deliberately naive re-implementation of ONLY the contended steps
    (grant + claim insert) from sampark/budget/issuance.py, run under
    READ COMMITTED instead of SERIALIZABLE — NOT the production path,
    exists solely to demonstrate what the schema's own partial unique
    index (normally) and SERIALIZABLE (for the rolling-cap read) prevent."""
    grant_id = uuid.uuid4()
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        conn.execute(
            "INSERT INTO grants (grant_id, request_id, budget_window_id, channel, "
            "incentive_ceiling_paise, send_after, expires_at, state) "
            "SELECT %s, request_id, "
            "(SELECT budget_window_id FROM budget_windows WHERE merchant_id='merchant-sim' AND window_id=%s LIMIT 1), "
            "'whatsapp', 0, %s, %s, 'RESERVED' "
            "FROM grant_requests WHERE risk_id = %s",
            (grant_id, window_id, SEND_AFTER, SEND_AFTER + dt.timedelta(hours=2), risk_id),
        )
        conn.execute(
            "INSERT INTO contact_slot_claims (claim_id, customer_id, window_id, grant_id, state, claimed_at) "
            "VALUES (%s, %s, %s, %s, 'RESERVED', %s)",
            (uuid.uuid4(), customer_id, window_id, grant_id, DECISION_AT),
        )


def test_negative_control_race_produces_more_than_one_winner_without_the_index(race_fixture):
    """Proves the concurrency test above is meaningful: without the
    partial unique index (and under READ COMMITTED), the identical race
    is NOT safe — more than one 'winner' is produced."""
    conn = race_fixture.conn
    n_probe = min(10, race_fixture.n)  # smaller N: this only needs to show >1, not exactly 1

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM budget_windows WHERE merchant_id='merchant-sim' AND window_id=%s", (SEND_AFTER.date(),))
        if cur.fetchone()[0] == 0:
            cur.execute(
                "INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise) "
                "VALUES (%s, 'merchant-sim', %s, 1000000)",
                (uuid.uuid4(), SEND_AFTER.date()),
            )
        for req in race_fixture.requests[:n_probe]:
            cur.execute(
                "INSERT INTO grant_requests (request_id, agent_id, customer_id, risk_id, intent, "
                "requested_channel, requested_max_incentive_bps, issued_at, signature) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (req.request_id, req.agent_id, req.customer_id, req.risk_id, req.intent,
                 req.requested_channel, req.requested_max_incentive_bps, req.issued_at, req.signature),
            )
        cur.execute("DROP INDEX IF EXISTS contact_slot_claims_active_uniq")

    try:
        conninfo = _conninfo()
        exceptions: list[Any] = [None] * n_probe
        barrier = threading.Barrier(n_probe)

        def worker(i: int) -> None:
            c = None
            try:
                c = psycopg.connect(conninfo, connect_timeout=5)
                c.autocommit = True
                barrier.wait(timeout=30)
                _read_committed_naive_issue(
                    c, race_fixture.customer_id, SEND_AFTER.date(), race_fixture.risk_items[i].risk_id
                )
            except Exception as exc:  # noqa: BLE001
                exceptions[i] = exc
            finally:
                if c is not None:
                    c.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_probe)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM contact_slot_claims WHERE customer_id = %s", (race_fixture.customer_id,))
            claim_count = cur.fetchone()[0]

        assert claim_count > 1, (
            "negative control failed to reproduce the race — without the unique index "
            f"and under READ COMMITTED, expected multiple winners, got {claim_count}"
        )
    finally:
        # Restore the real schema unconditionally. The race just proved
        # duplicate claims CAN exist without the index — that duplication
        # must be cleaned up before the unique index can be recreated at
        # all (this cleanup is test-fixture teardown, not evidence).
        with conn.cursor() as cur:
            cur.execute("DELETE FROM contact_slot_claims WHERE customer_id = %s", (race_fixture.customer_id,))
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS contact_slot_claims_active_uniq "
                "ON contact_slot_claims (customer_id, window_id) "
                "WHERE state IN ('RESERVED','EXECUTING','CONFIRMED')"
            )
            cur.execute("SELECT indexname FROM pg_indexes WHERE indexname = 'contact_slot_claims_active_uniq'")
            assert cur.fetchone() is not None, "failed to restore the production unique index"
