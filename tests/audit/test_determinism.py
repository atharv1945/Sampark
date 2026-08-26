"""T-26 — audit stream determinism (Phase 5A §7.4, §8.7).

The Phase 5A finding that matters most operationally: `agent_signature`
enters the hash preimage, so the chain is reproducible ONLY where agent
keypairs are reproducible. sim/arm_b.py's "memory" backend generates
keypairs from OS randomness (non-reproducible); its "postgres" backend
derives them from `(seed, agent_id)` via SHA-256 (reproducible). The
official evidence CLI (sim/arm_b_cli.py) is Postgres-only, so the
determinism claim holds on the evidence path — and MUST NEVER be tested
against the memory backend, per the Phase 5B brief.

As of U-2 (second Phase 5B pass), sim/arm_b.py IS wired to
sampark.audit — `run_arm_b`/`_run_arm_b_postgres` accept `audit_sink`.
This file:

  1. Proves the determinism PROPERTY at the layer that actually carries
     it — identical inputs (including identical signatures) through
     sampark.audit.emit + sampark.audit.canonical produce byte-identical
     output, twice (cheap, no database).
  2. Runs the REAL end-to-end comparison: `_run_arm_b_postgres` (the
     exact official evidence path, minus the CLI wrapper) TWICE on the
     IDENTICAL small hand-built fixture
     `tests/arm_b/test_arm_b_postgres_determinism.py` already uses for
     Phase 4's own determinism guarantee — never the full 20k-item
     seed-42 dataset (~10 minutes per run, explicitly out of scope for a
     unit test; see that module's docstring) — with `audit_sink` pointed
     at TWO SEPARATE isolated schemas (one per run), and asserts the two
     resulting canonical event streams are byte-identical.

     Two separate schemas, not one shared one: request_id/event_id are
     BOTH uuid5-derived from (seed, agent_id, risk_id) — identical
     between the two runs of the identical fixture — so a second run
     writing into the SAME schema as the first would just hit
     append()'s idempotency probe (Phase 5A §8.6) and silently no-op
     rather than producing a second, independently comparable stream.

  What IS and is NOT expected to be deterministic (the persistence-only
  exclusion the brief asks to be explicit about): `seq` (BIGSERIAL) is
  NEVER part of `AuditEvent`/`canonical_bytes` in the first place — it
  is a persistence-only ordering aid (Phase 5A §4.2), never compared
  here because there is nothing to compare (it's not on the object).
  Every OTHER identifier this test touches — `event_id`, `grant_id`,
  `budget_window_id`, `claim_id`, `request_id` — is uuid5-derived
  (Design Lock §16 / Phase 5A §3.1: no uuid4 anywhere on this path), so
  it is NOT persistence-only noise; it is exactly the reproducible
  logical content the determinism claim is about, and IS compared.
"""

from __future__ import annotations

import datetime as dt
import uuid

import numpy as np
import pytest

from sampark.allocator.candidate import build_candidate
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit import emit
from sampark.audit.canonical import canonical_bytes
from sampark.contracts import GrantRequest, RiskItem
from sim.environment import Environment

pytestmark = pytest.mark.postgres

SEED = 42  # matches tests/arm_b/test_arm_b_postgres_determinism.py's SEED —
# reuses whatever this seed's 4 global agent_ids are already registered
# under (idempotent either way; see that module's own comment).

ISSUED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _deterministic_signature(seed: int, agent_id: str, risk_id: str) -> str:
    """Stands in for sim/arm_b.py's `_deterministic_keypair` +
    `agents.mediated.to_grant_request` — a signature derived purely from
    (seed, agent_id, risk_id), reproducing what the Postgres backend
    actually produces across two runs of the same seed."""
    import base64
    import hashlib

    return base64.b64encode(hashlib.sha256(f"{seed}:{agent_id}:{risk_id}".encode()).digest()).decode("ascii")


def _build_events_for_run(seed: int) -> list:
    request = GrantRequest(
        request_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}:cart_recovery_agent:risk-1"),
        agent_id="cart_recovery_agent", customer_id="cust-1", risk_id="risk-1", intent="cart_recovery",
        requested_channel="whatsapp", requested_max_incentive_bps=500, issued_at=ISSUED_AT,
        signature=_deterministic_signature(seed, "cart_recovery_agent", "risk-1"),
    )
    item = RiskItem(risk_id="risk-1", source="abandoned_checkout", amount_paise=500_000,
                     root_cause="price_hesitation", detected_at=ISSUED_AT)
    candidate = build_candidate(request, item, "cust-1", dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))
    outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DENIED, reason_code="policy.opt_out_active",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    return [emit.event_for_request_received(request), emit.event_for_decision(outcome, ISSUED_AT)]


def test_audit_stream_is_deterministic_given_identical_signed_inputs():
    """Proxy for T-26 at the emit+canonical layer: the SAME seed, run
    "twice" (two independent calls, mirroring two separate Arm B
    processes), produces byte-identical canonical event streams — this
    is the property the Postgres backend's deterministic keypair
    derivation is FOR. Postgres-marked because it documents a Postgres-
    backend-specific guarantee, even though this particular test issues
    no queries."""
    run1 = _build_events_for_run(seed=42)
    run2 = _build_events_for_run(seed=42)

    assert [e.event_id for e in run1] == [e.event_id for e in run2]
    assert [canonical_bytes(e) for e in run1] == [canonical_bytes(e) for e in run2]


def test_audit_stream_differs_for_a_different_seed():
    # Sanity check on the proxy above: determinism must not be a
    # tautology (e.g. from a constant signature) — a different seed
    # genuinely produces a different signature and therefore different
    # canonical bytes for the "same" logical event.
    run_a = _build_events_for_run(seed=42)
    run_b = _build_events_for_run(seed=7)
    assert canonical_bytes(run_a[0]) != canonical_bytes(run_b[0])


def test_audit_stream_is_deterministic_across_two_real_arm_b_runs(pg_raw_conn):
    """T-26, implemented. Runs `_run_arm_b_postgres` — the real official
    evidence path — twice on the identical small fixture, each into its
    own isolated audit schema, and asserts byte-identical canonical
    event streams. MUST use backend='postgres' (the only backend
    `_run_arm_b_postgres` runs) — never `backend='memory'`, which
    generates fresh Ed25519 keypairs per run and can never satisfy this
    property (module docstring)."""
    import uuid as _uuid

    from sim.arm_b import _run_arm_b_postgres
    from tests.arm_b.test_arm_b_postgres_determinism import _build_fixture, _cleanup_reference_data
    from tests.audit.conftest import create_isolated_audit_schema, drop_isolated_audit_schema, new_schema_name

    from sampark.allocator.constants import AGING_BONUS_PAISE, MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW
    from sampark.audit.chain import all_events_ordered
    from sampark.audit.sink import PostgresAuditSink

    suffix = _uuid.uuid4().hex[:10]
    ledger, all_actions, profile_by_customer, customer_ids = _build_fixture(suffix)
    risk_ids = [item.risk_id for item in ledger.risk_items]
    assert len(all_actions) == 5

    schema_a, schema_b = new_schema_name(), new_schema_name()
    create_isolated_audit_schema(pg_raw_conn, schema_a)
    create_isolated_audit_schema(pg_raw_conn, schema_b)

    def _run_into(schema_name: str):
        conn = None
        try:
            import psycopg

            from sim.persistence import PostgresConfig

            conn = psycopg.connect(PostgresConfig.from_env().conninfo())
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {schema_name}, public")
            sink = PostgresAuditSink(conn)
            environment = Environment(dict(profile_by_customer), np.random.default_rng(SEED))
            _run_arm_b_postgres(
                SEED, ledger, None, environment, all_actions, AGING_BONUS_PAISE, False,
                MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW, audit_sink=sink,
            )
            return all_events_ordered(conn)
        finally:
            if conn is not None:
                conn.close()

    try:
        events_a = _run_into(schema_a)
        events_b = _run_into(schema_b)
    finally:
        _cleanup_reference_data(pg_raw_conn, customer_ids, risk_ids)
        drop_isolated_audit_schema(pg_raw_conn, schema_a)
        drop_isolated_audit_schema(pg_raw_conn, schema_b)

    assert len(events_a) > 0, "the fixture must produce at least one audit event"
    assert len(events_a) == len(events_b)
    assert [canonical_bytes(e) for e in events_a] == [canonical_bytes(e) for e in events_b], (
        "identical seed, identical fixture, two independent Postgres-backed Arm B runs "
        "-> byte-identical canonical audit event streams"
    )
    # And a sanity check that this exercised more than one event type —
    # otherwise "byte-identical" would be a weak claim.
    assert len({e.event_type for e in events_a}) >= 2
