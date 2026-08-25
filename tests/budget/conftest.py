"""Shared fixtures for sampark/budget/ tests.

Postgres fixtures (`pg_env`) mirror tests/registry/test_registration.py's
`_connect_or_skip()` / cleanup pattern exactly, extended with a registered
agent + a fresh customer, so Phase 4 issuance tests can build real,
FK-satisfying rows. Tests using `pg_env` must ALSO be marked
`@pytest.mark.postgres` (Design Lock §12.5) so CI can select them
deliberately; the skip-if-unreachable fallback stays for contributors
without Docker.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Callable
from uuid import uuid4

import psycopg
import pytest

from sampark.allocator.candidate import build_candidate
from sampark.contracts import GrantRequest, RiskItem
from sim.persistence import PostgresConfig, PostgresConfigError

DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


@pytest.fixture()
def make_candidate():
    def _make(
        customer_id: str = "cust-1",
        risk_id: str = "risk-1",
        amount_paise: int = 500_000,
        bps: int = 500,
        agent_id: str = "cart_recovery_agent",
        intent: str = "cart_recovery",
        channel: str = "whatsapp",
        proposed_send_after: dt.datetime = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        source: str = "abandoned_checkout",
        root_cause: str = "price_hesitation",
    ):
        item = RiskItem(
            risk_id=risk_id, source=source, amount_paise=amount_paise,
            root_cause=root_cause, detected_at=DETECTED_AT,
        )
        request = GrantRequest(
            request_id=uuid4(), agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
            intent=intent, requested_channel=channel, requested_max_incentive_bps=bps,
            issued_at=DETECTED_AT, signature="sig",
        )
        return build_candidate(request, item, customer_id, proposed_send_after)

    return _make


@pytest.fixture()
def risk_items_by_customer():
    return {}


# --- Postgres ------------------------------------------------------------


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


@dataclasses.dataclass
class PgEnv:
    conn: psycopg.Connection
    agent_id: str
    customer_id: str
    insert_risk_item: Callable[..., RiskItem]
    insert_customer: Callable[[str], None]
    track_window: Callable[[object], None]
    created_risk_ids: list[str]  # live-updating — every insert_risk_item() call appends here;
    # tests pass frozenset(pg_env.created_risk_ids) as issue_grant's run_seed_risk_ids,
    # always exactly "every risk item this fixture instance has created so far" (W5).


@pytest.fixture()
def pg_env():
    """A registered agent + a fresh customer with a contact_states row,
    real rows in real PostgreSQL. Everything created is deleted at
    teardown, in FK-safe order; nothing in the pre-existing Phase 0-3
    dataset is touched."""
    conn = _connect_or_skip()
    conn.autocommit = True
    suffix = uuid4().hex[:12]
    agent_id = f"test-issuance-agent-{suffix}"
    customer_id = f"test-issuance-cust-{suffix}"
    created_customer_ids = [customer_id]
    created_risk_ids: list[str] = []
    touched_window_ids: list = []

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agents (agent_id, public_key, publisher, state, strike_count) "
            "VALUES (%s, %s, %s, 'ACTIVE', 0)",
            (agent_id, "test-public-key", "test-publisher"),
        )
        cur.execute(
            "INSERT INTO capability_scopes "
            "(agent_id, allowed_channels, allowed_intents, allowed_risk_sources, "
            " max_incentive_bps, max_requests_per_hour) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (agent_id, '["whatsapp","sms","voice"]', '["cart_recovery","payment_retry","mandate_retry","receivables_followup"]',
             '["abandoned_checkout","failed_payment","mandate_failure","overdue_invoice"]', 500, 100_000),
        )
        cur.execute(
            "INSERT INTO customers (customer_id) VALUES (%s)",
            (customer_id,),
        )
        cur.execute(
            "INSERT INTO contact_states (customer_id, contacts_24h, contacts_7d, optouts_by_channel, "
            "consent_scopes, fatigue_score) VALUES (%s, 0, 0, '{}', '{}', 0.0)",
            (customer_id,),
        )

    def _insert_customer(extra_customer_id: str) -> None:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO customers (customer_id) VALUES (%s)", (extra_customer_id,))
            cur.execute(
                "INSERT INTO contact_states (customer_id, contacts_24h, contacts_7d, "
                "optouts_by_channel, consent_scopes, fatigue_score) VALUES (%s, 0, 0, '{}', '{}', 0.0)",
                (extra_customer_id,),
            )
        created_customer_ids.append(extra_customer_id)

    def _insert_risk_item(
        risk_id: str,
        amount_paise: int = 500_000,
        customer_id_override: str | None = None,
        source: str = "abandoned_checkout",
        root_cause: str = "price_hesitation",
        detected_at: dt.datetime = DETECTED_AT,
    ) -> RiskItem:
        target_customer = customer_id_override or customer_id
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO risk_items (risk_id, customer_id, source, amount_paise, root_cause, detected_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (risk_id, target_customer, source, amount_paise, root_cause, detected_at),
            )
        created_risk_ids.append(risk_id)
        return RiskItem(
            risk_id=risk_id, source=source, amount_paise=amount_paise,
            root_cause=root_cause, detected_at=detected_at,
        )

    def _track_window(window_id) -> None:
        touched_window_ids.append(window_id)

    try:
        yield PgEnv(
            conn=conn, agent_id=agent_id, customer_id=customer_id,
            insert_risk_item=_insert_risk_item, insert_customer=_insert_customer,
            track_window=_track_window, created_risk_ids=created_risk_ids,
        )
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM contact_slot_claims WHERE customer_id = ANY(%s)", (created_customer_ids,)
            )
            cur.execute(
                "DELETE FROM grants WHERE request_id IN "
                "(SELECT request_id FROM grant_requests WHERE customer_id = ANY(%s))",
                (created_customer_ids,),
            )
            cur.execute("DELETE FROM grant_requests WHERE customer_id = ANY(%s)", (created_customer_ids,))
            cur.execute("DELETE FROM customer_margin_windows WHERE customer_id = ANY(%s)", (created_customer_ids,))
            # budget_windows is a SHARED (merchant-scoped, not customer-scoped)
            # pool — delete only the specific (merchant, window) rows this
            # test touched, via track_window(), so its reservation doesn't
            # leak into a later test run that reuses the same window_id.
            if touched_window_ids:
                cur.execute(
                    "DELETE FROM budget_windows WHERE merchant_id = 'merchant-sim' AND window_id = ANY(%s)",
                    (touched_window_ids,),
                )
            if created_risk_ids:
                cur.execute("DELETE FROM risk_items WHERE risk_id = ANY(%s)", (created_risk_ids,))
            cur.execute("DELETE FROM contact_states WHERE customer_id = ANY(%s)", (created_customer_ids,))
            cur.execute("DELETE FROM customers WHERE customer_id = ANY(%s)", (created_customer_ids,))
            cur.execute("DELETE FROM agents WHERE agent_id = %s", (agent_id,))  # CASCADEs capability_scopes
        conn.close()
