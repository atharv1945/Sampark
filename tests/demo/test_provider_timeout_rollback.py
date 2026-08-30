"""Failure 1 — provider timeout, rollback, idempotent retry (spec §12.3).

Two layers are tested separately, because they are two separate guarantees
(see `sampark/demo/provider.py`'s module docstring):

    no silently burned budget  ->  rollback returns margin + contact slot
    no double-send             ->  the provider retry is idempotent by grant_id
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from agents.types import ContactAction
from sampark.demo import isolation
from sampark.demo.provider import (
    MAX_ATTEMPTS,
    MockProvider,
    ProviderFailureMode,
    ProviderTimeout,
)
from sampark.demo.runner import DemoRunner


def _action(channel="whatsapp"):
    return ContactAction(
        agent_id="cart_recovery_agent", risk_id="r1", customer_id="c1", channel=channel,
        intent="cart_recovery", incentive_bps=500,
        scheduled_at=dt.datetime(2025, 9, 10, 10, 0, tzinfo=dt.timezone.utc),
    )


# --------------------------------------------------------------------------
# provider semantics (pure, no database)
# --------------------------------------------------------------------------


def test_normal_send_succeeds_and_records_acceptance():
    provider, grant_id = MockProvider(), uuid.uuid4()
    result = provider.send(grant_id, _action())
    assert result.deduplicated is False and result.attempts == 1
    assert provider.has_accepted(grant_id)


def test_timeout_then_success_retries_and_never_burned_an_acceptance():
    provider, grant_id = MockProvider(), uuid.uuid4()
    provider.arm(ProviderFailureMode.TIMEOUT_THEN_SUCCESS)
    with pytest.raises(ProviderTimeout):
        provider.send(grant_id, _action())
    assert not provider.has_accepted(grant_id), "nothing was delivered, so nothing may be recorded"
    result = provider.send(grant_id, _action())
    assert result.attempts == 2 and result.deduplicated is False


def test_accept_then_timeout_retry_returns_the_stored_receipt_and_does_not_double_send():
    """The hard case, and the one spec §12.3's "no double-send" is about: the
    provider DID deliver, then the caller timed out and never learned so."""
    provider, grant_id = MockProvider(), uuid.uuid4()
    provider.arm(ProviderFailureMode.ACCEPT_THEN_TIMEOUT)
    with pytest.raises(ProviderTimeout):
        provider.send(grant_id, _action())
    assert provider.has_accepted(grant_id)
    first = provider._accepted[grant_id]
    result = provider.send(grant_id, _action())
    assert result.deduplicated is True, "a second real send would double-contact the customer"
    assert result.receipt is first


def test_hard_down_never_succeeds_and_never_records_acceptance():
    provider, grant_id = MockProvider(), uuid.uuid4()
    provider.arm(ProviderFailureMode.HARD_DOWN)
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(ProviderTimeout):
            provider.send(grant_id, _action())
    assert not provider.has_accepted(grant_id)


def test_failure_is_targetable_to_one_grant():
    """So the demo shows ONE legible rollback, not a cascade."""
    provider, target, other = MockProvider(), uuid.uuid4(), uuid.uuid4()
    provider.arm(ProviderFailureMode.HARD_DOWN, grant_id=target)
    with pytest.raises(ProviderTimeout):
        provider.send(target, _action())
    assert provider.send(other, _action()).deduplicated is False


def test_provider_is_deterministic_under_replay():
    """Same mode, same call sequence, same outcomes — no clock, no RNG."""
    def run():
        provider, grant_id = MockProvider(), uuid.UUID(int=7)
        provider.arm(ProviderFailureMode.TIMEOUT_THEN_SUCCESS)
        out = []
        for _ in range(3):
            try:
                out.append(("ok", provider.send(grant_id, _action()).deduplicated))
            except ProviderTimeout as exc:
                out.append(("timeout", exc.attempt))
        return out

    assert run() == run()


# --------------------------------------------------------------------------
# rollback against real Postgres
# --------------------------------------------------------------------------


@pytest.mark.postgres
def test_rollback_releases_margin_and_the_contact_slot_and_burns_nothing(raw_conn, demo_scenario):
    """The scripted HARD_DOWN failure fires in window index 1 of every run."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()

        with raw_conn.cursor() as cur:
            cur.execute("SELECT grant_id, incentive_ceiling_paise FROM grants WHERE state = 'ROLLED_BACK'")
            rolled = cur.fetchall()
            assert rolled, "the scripted provider failure did not produce a rollback"
            grant_id, ceiling = rolled[0]

            # The contact slot was RESTORED, not silently consumed.
            cur.execute(
                "SELECT state, released_at FROM contact_slot_claims WHERE grant_id = %s", (grant_id,)
            )
            state, released_at = cur.fetchone()
            assert state == "ROLLED_BACK" and released_at is not None

            # Margin was RELEASED, not SPENT.
            cur.execute(
                "SELECT g.budget_window_id, b.margin_spent_paise FROM grants g "
                "JOIN budget_windows b ON b.budget_window_id = g.budget_window_id "
                "WHERE g.grant_id = %s", (grant_id,)
            )
            _bw, spent_after = cur.fetchone()

            # Every CONFIRMED grant's ceiling is spent; the rolled-back one's is not.
            cur.execute("SELECT COALESCE(sum(incentive_ceiling_paise),0) FROM grants WHERE state='CONFIRMED'")
            confirmed_total = cur.fetchone()[0]
            cur.execute("SELECT COALESCE(sum(margin_spent_paise),0) FROM budget_windows")
            assert cur.fetchone()[0] == confirmed_total, (
                "spent margin must equal the CONFIRMED grants only - the rolled-back "
                "reservation must not have been burned"
            )

            # No reservation leaked: nothing is still held for a terminal grant.
            cur.execute("SELECT COALESCE(sum(margin_reserved_paise),0) FROM budget_windows")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COALESCE(sum(margin_reserved_paise),0) FROM customer_margin_windows")
            assert cur.fetchone()[0] == 0

        assert runner.rollback_count == 1
        assert runner.retry_count >= 1, "a rollback must be preceded by real retry attempts"
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


@pytest.mark.postgres
def test_exactly_one_grant_and_one_claim_per_request_after_all_retries(raw_conn, demo_scenario):
    """spec §12.3's "no duplicate grant", asserted against the database."""
    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        with raw_conn.cursor() as cur:
            cur.execute("SELECT request_id, count(*) FROM grants GROUP BY 1 HAVING count(*) > 1")
            assert cur.fetchall() == [], "a request_id owns more than one grant"
            cur.execute("SELECT grant_id, count(*) FROM contact_slot_claims GROUP BY 1 HAVING count(*) > 1")
            assert cur.fetchall() == [], "a grant_id owns more than one contact slot claim"
            # One grant.reserved event per grant, and one terminal event each.
            cur.execute(
                "SELECT payload->>'grant_id', count(*) FROM audit_events "
                "WHERE event_type = 'grant.confirmed' GROUP BY 1 HAVING count(*) > 1"
            )
            assert cur.fetchall() == [], "a grant was confirmed more than once"
    finally:
        isolation.drop_demo_schema(raw_conn, schema)


@pytest.mark.postgres
def test_audit_reconciliation_holds_after_a_rollback(raw_conn, demo_scenario):
    """`verify_chain`'s own reconciliation: every `grants` row must have a
    `grant.reserved` event. A rollback must not break that."""
    from sampark.audit.chain import verify_chain

    schema = isolation.create_demo_schema(raw_conn)
    try:
        runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=schema, pace=False)
        runner.prepare()
        runner.run()
        report = verify_chain(raw_conn)
        assert report.ok
        assert report.missing_grant_reservations == ()
    finally:
        isolation.drop_demo_schema(raw_conn, schema)
