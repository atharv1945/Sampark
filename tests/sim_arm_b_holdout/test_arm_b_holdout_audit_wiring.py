"""sim.arm_b — Phase 7 audit_sink wiring (spec §8.9). Memory backend,
no Postgres needed: proves record_holdout_assigned / record_contact_opt_out
are called with the RIGHT arguments, using a fake AuditSink that records
calls rather than a real database.

Note: `record_recovery_credited` is NOT exercised here — that emission is
the CALLER's responsibility (computing a credit requires the full
holdout-derived baseline, only known after this function returns; see
run_arm_b_holdout's own docstring) and is proven separately in
tests/audit/test_emit_phase7.py at the emitter-function level. This test
proves the two events run_arm_b_holdout DOES own are correctly wired
into the real runner, not merely correctly constructed in isolation.
"""

from __future__ import annotations

from sim.arm_b import BACKEND_MEMORY, run_arm_b_holdout

SEED = 42


class _FakeAuditSink:
    def __init__(self):
        self.holdout_assigned_calls = []
        self.contact_opt_out_calls = []
        self.other_calls = []

    def record_holdout_assigned(self, **kwargs):
        self.holdout_assigned_calls.append(kwargs)

    def record_contact_opt_out(self, grant, request, channel, contact_index, at):
        self.contact_opt_out_calls.append(
            {"grant_id": grant.grant_id, "customer_id": request.customer_id, "channel": channel, "contact_index": contact_index, "at": at}
        )

    def record_recovery_credited(self, credit, request, at):
        raise AssertionError("record_recovery_credited must NOT be called by run_arm_b_holdout itself")

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.other_calls.append((name, args, kwargs))
        return _record


def test_holdout_assigned_is_emitted_exactly_once():
    sink = _FakeAuditSink()
    run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY, audit_sink=sink)
    assert len(sink.holdout_assigned_calls) == 1
    call = sink.holdout_assigned_calls[0]
    assert call["seed"] == SEED
    assert call["fraction_bps"] == 1000
    assert call["assignment_version"] == 1
    assert call["holdout_customer_count"] > 0
    assert len(call["holdout_customer_set_sha256"]) == 64


def test_holdout_assigned_digest_matches_the_real_holdout_set():
    from sim.holdout import assign, customer_amounts_from_risk_items, membership_digest
    from sim.cli import build_dataset

    sink = _FakeAuditSink()
    result = run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY, audit_sink=sink)

    _population, _signals, ledger = build_dataset(SEED)
    amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    expected_held_out = assign(SEED, 0.10, amounts)

    assert sink.holdout_assigned_calls[0]["holdout_customer_set_sha256"] == membership_digest(expected_held_out)
    assert result.holdout_customer_set_sha256 == membership_digest(expected_held_out)


def test_contact_opt_out_is_emitted_for_every_opted_out_contact():
    sink = _FakeAuditSink()
    result = run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY, audit_sink=sink)

    real_opt_outs = [o for o in result.contact_outcomes if o.opt_out]
    assert len(sink.contact_opt_out_calls) == len(real_opt_outs)
    assert len(real_opt_outs) > 0  # precondition: the mechanism actually produced some


def test_no_audit_calls_at_all_when_sink_is_none():
    """The zero-cost default path — every branch reading audit_sink is
    skipped, matching run_arm_b's own established convention."""
    result = run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY, audit_sink=None)
    assert len(result.contact_outcomes) > 0  # sanity: the run still worked


def test_recovery_credited_is_never_called_by_the_runner_itself():
    sink = _FakeAuditSink()
    run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_MEMORY, audit_sink=sink)
    # If record_recovery_credited had been called, _FakeAuditSink would
    # have raised AssertionError already -- reaching here proves it wasn't.
