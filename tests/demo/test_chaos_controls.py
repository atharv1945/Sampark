"""The seven chaos controls — spec §12.4.

    "A small control strip that lets SOMEONE ELSE break the system... Then
     hand over the laptop and say 'try to break it.'"

Two properties are asserted for every control:

    it does something REAL   — a specific backend mechanism, whose effect
                               reaches the audit chain
    it fails HONESTLY        — when it cannot apply, it raises
                               ChaosInapplicableError (HTTP 409), changes no
                               state, and writes no event. It never fakes an
                               effect, which is the trace-integrity rule
                               applied to the chaos panel.
"""

from __future__ import annotations

import pytest

from sampark.demo import isolation
from sampark.demo.chaos import CONTROLS, CONTROLS_BY_ID, ChaosControlId, ChaosInapplicableError
from sampark.demo.provider import ProviderFailureMode
from sampark.demo.runner import DemoRunner
from sampark.demo.scenario import ROGUE_AGENT_ID


# --------------------------------------------------------------------------
# the catalogue
# --------------------------------------------------------------------------


def test_there_are_exactly_seven_controls_and_they_match_the_specification():
    """Recovered verbatim from spec §12.4's table — not invented, and not
    padded out to seven."""
    assert len(CONTROLS) == 7
    assert [c.spec_name for c in CONTROLS] == [
        "Kill uplift model",
        "Revoke agent key",
        "Set clock to 21:40",
        "Force provider timeout",
        "Flood rogue agent to 6 req/min",
        "Mark customer opted-out mid-run",
        "Trigger RTO flag on an active cart",
    ]
    assert [c.exercises for c in CONTROLS] == [
        "Graceful degradation",
        "Registry quarantine",
        "TCCCPR quiet-hour filter",
        "Reservation rollback",
        "Rate ceiling + strikes",
        "Permanent suppression",
        "Interlock matrix",
    ]


def test_every_control_declares_a_real_mechanism_and_expected_audit_effect():
    for control in CONTROLS:
        assert control.mechanism.strip(), control.control_id
        assert control.expected_audit.strip(), control.control_id


def test_deviations_from_the_specification_are_declared_not_hidden():
    """Control 7 substitutes `dispute_open` for the RTO flag, because the
    rto_flag interlock row is declared with a condition that always returns
    FACT_UNAVAILABLE and cannot deny without editing protected Phase 4 policy
    files and changing committed evidence. The substitution must be visible
    in the control itself, so it reaches the UI and the reviewer."""
    interlock = CONTROLS_BY_ID[ChaosControlId.TRIGGER_INTERLOCK_ON_CART]
    assert "SUBSTITUTION" in interlock.spec_note
    assert "dispute_open" in interlock.spec_note
    assert "rto_flag" in interlock.spec_note

    model = CONTROLS_BY_ID[ChaosControlId.KILL_MODEL]
    assert "already unavailable" in model.spec_note.lower()


def test_the_rto_flag_interlock_genuinely_cannot_deny():
    """The repository fact that forces the substitution above. If this ever
    stops being true, control 7 should be revisited."""
    from sampark.policy.hard.interlocks import INTERLOCKS

    rto = next(i for i in INTERLOCKS if i.interlock_id == "rto_flag")
    assert rto.condition(None, None) is None, "rto_flag can now resolve; revisit chaos control 7"
    assert rto.unavailable_reason_code == "fact_unavailable.rto_flag"

    dispute = next(i for i in INTERLOCKS if i.interlock_id == "dispute_open")
    assert dispute.defers is False, "dispute_open must be a DENY interlock"


# --------------------------------------------------------------------------
# each control, fired for real
# --------------------------------------------------------------------------


@pytest.fixture()
def live(raw_conn, demo_schema, demo_scenario):
    runner = DemoRunner(conn=raw_conn, scenario=demo_scenario, schema=demo_schema, pace=False)
    runner.prepare()
    return runner


def _events(conn, event_type):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events WHERE event_type = %s", (event_type,))
        return cur.fetchone()[0]


@pytest.mark.postgres
def test_1_kill_model(live):
    assert live.scorer.killed is False
    effect = live.fire_chaos(ChaosControlId.KILL_MODEL)
    assert live.scorer.killed is True and "killed" in effect
    # Firing twice is inapplicable, not a silent no-op.
    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.KILL_MODEL)


@pytest.mark.postgres
def test_2_revoke_agent_key(live, raw_conn):
    before = _events(raw_conn, "agent.revoked")
    live.fire_chaos(ChaosControlId.REVOKE_AGENT_KEY)
    with raw_conn.cursor() as cur:
        cur.execute("SELECT state FROM agents WHERE agent_id = %s", (ROGUE_AGENT_ID,))
        assert cur.fetchone()[0] == "REVOKED"
    assert _events(raw_conn, "agent.revoked") == before + 1

    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.REVOKE_AGENT_KEY)  # already revoked
    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.REVOKE_AGENT_KEY, target="no_such_agent")


@pytest.mark.postgres
def test_3_set_clock_quiet_hours(live, raw_conn, demo_scenario):
    live.status.window_index = 0
    live.fire_chaos(ChaosControlId.SET_CLOCK_QUIET_HOURS)
    target = demo_scenario.windows[1]
    assert live._quiet_hours_override.get(target) is True
    live.status.window_index = 1
    live.run_window(target)
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_events WHERE event_type = 'decision.deferred' "
            "AND reason_code = 'policy.quiet_hours'"
        )
        assert cur.fetchone()[0] > 0, "no quiet-hour deferral resulted"


@pytest.mark.postgres
def test_4_force_provider_timeout(live, raw_conn, demo_scenario):
    """The armed mode is consumed by the next GRANT EXECUTION, not by the
    next window: window 0 of this scenario grants nothing (every candidate
    that window is denied or deferred), so the control legitimately waits.
    Driving windows until a grant occurs is what actually exercises it."""
    live.fire_chaos(ChaosControlId.FORCE_PROVIDER_TIMEOUT)
    assert live.chaos.pending_provider_mode is ProviderFailureMode.HARD_DOWN

    for index, window in enumerate(demo_scenario.windows):
        live.status.window_index = index
        live.run_window(window)
        if live.rollback_count:
            break

    assert live.rollback_count >= 1
    assert _events(raw_conn, "grant.rolled_back") >= 1
    assert live.retry_count >= 1, "a rollback must follow real retry attempts"
    # The provider disarms itself after firing, so one armed control produces
    # ONE legible rollback rather than a cascade.
    assert live.provider.is_armed() is False


@pytest.mark.postgres
def test_4b_force_provider_timeout_accepts_a_retry_mode(live):
    live.fire_chaos(ChaosControlId.FORCE_PROVIDER_TIMEOUT, target="accept_then_timeout")
    assert live.chaos.pending_provider_mode is ProviderFailureMode.ACCEPT_THEN_TIMEOUT


@pytest.mark.postgres
def test_5_flood_rogue_agent(live, raw_conn, demo_scenario):
    live.status.window_index = 0
    live.fire_chaos(ChaosControlId.FLOOD_ROGUE_AGENT)
    assert live.chaos.pending_flood is True
    live.status.window_index = 1
    live._inject_flood(demo_scenario.windows[1])
    live.run_window(demo_scenario.windows[1])
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit_events WHERE event_type = 'decision.denied' "
            "AND reason_code = 'agent.rate_ceiling_exceeded'"
        )
        assert cur.fetchone()[0] == 3
    assert _events(raw_conn, "agent.struck") == 3
    assert _events(raw_conn, "agent.revoked") == 1

    # Now inapplicable: the key is gone.
    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.FLOOD_ROGUE_AGENT)


@pytest.mark.postgres
def test_6_mark_customer_opted_out(live, raw_conn, demo_scenario):
    customer = demo_scenario.customer_ids[0]
    live.fire_chaos(ChaosControlId.MARK_CUSTOMER_OPTED_OUT, target=customer)
    with raw_conn.cursor() as cur:
        cur.execute("SELECT optouts_by_channel FROM contact_states WHERE customer_id = %s", (customer,))
        assert "whatsapp" in cur.fetchone()[0]
    # The hard rule now denies that channel permanently for that customer.
    assert live.mediation_ledger.optouts_by_channel(customer).get("whatsapp")

    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.MARK_CUSTOMER_OPTED_OUT, target="cust_not_in_this_demo")


@pytest.mark.postgres
def test_7_trigger_interlock_on_cart(live, raw_conn):
    effect = live.fire_chaos(ChaosControlId.TRIGGER_INTERLOCK_ON_CART)
    assert "dispute_open" in effect
    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM risk_items WHERE root_cause = 'disputed'")
        assert cur.fetchone()[0] > 0

    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.TRIGGER_INTERLOCK_ON_CART, target="cust_not_in_this_demo")


@pytest.mark.postgres
def test_an_inapplicable_control_writes_no_audit_event(live, raw_conn):
    """Honest failure: 409, no state change, nothing on the chain."""
    live.fire_chaos(ChaosControlId.KILL_MODEL)
    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        before = cur.fetchone()[0]
    with pytest.raises(ChaosInapplicableError):
        live.fire_chaos(ChaosControlId.KILL_MODEL)
    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == before


@pytest.mark.postgres
def test_arming_a_control_is_never_itself_audited(live, raw_conn):
    """spec §12.1: the chain is the DECISION record, not a UI activity feed.
    Arming the provider changes no decision yet, so it writes nothing."""
    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        before = cur.fetchone()[0]
    live.fire_chaos(ChaosControlId.FORCE_PROVIDER_TIMEOUT)
    live.fire_chaos(ChaosControlId.SET_CLOCK_QUIET_HOURS)
    with raw_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_events")
        assert cur.fetchone()[0] == before
