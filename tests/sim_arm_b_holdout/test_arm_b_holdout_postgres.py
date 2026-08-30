"""sim.arm_b — Arm B-H (postgres backend), Phase 7 (spec §8.9, §11).

Real, owner-authored SERIALIZABLE issuance transaction. Proves:
  1. the mechanism runs end-to-end against live Postgres without error;
  2. opt-out write-back (sim/optout_writeback.py) actually persists during
     the run and is fully reset by _cleanup_postgres_holdout_run afterward
     (mirroring _cleanup_postgres_run's existing contract — never leave
     Phase 7 residue for a later run to trip over, the exact class of bug
     the Phase 6 disk-full incident already taught this codebase to guard
     against);
  3. determinism across two independent runs of the same seed.

Module-marked `postgres` — every test here requires a live PostgreSQL
instance and is expected to skip without one, exactly like
tests/arm_b/test_arm_b_postgres_smoke.py.

`result` (module-scoped fixture) is computed ONCE and shared across the
first three tests — a full-month, Postgres-backed Arm B-H run costs real
wall-clock minutes (SERIALIZABLE issuance across the whole horizon), and
re-running it per-assertion would multiply that cost for no additional
evidence. Only the determinism test needs a genuinely SECOND, independent
run.
"""

from __future__ import annotations

import pytest

from sim.arm_b import BACKEND_POSTGRES, run_arm_b_holdout
from sim.cli import build_dataset
from sim.persistence import PostgresConfig

pytestmark = pytest.mark.postgres

SEED = 42


def _conn():
    import psycopg

    config = PostgresConfig.from_env()
    conn = psycopg.connect(config.conninfo())
    conn.autocommit = True
    return conn


@pytest.fixture(scope="module")
def result():
    return run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_POSTGRES)


def test_postgres_backend_runs_end_to_end_and_covers_every_item(result):
    _population, _signals, ledger = build_dataset(SEED)

    assert result.backend == BACKEND_POSTGRES
    contacted_ids = {o.risk_id for o in result.contact_outcomes}
    natural_ids = {o.risk_id for o in result.natural_outcomes}
    assert contacted_ids & natural_ids == set()
    assert contacted_ids | natural_ids == {item.risk_id for item in ledger.risk_items}
    assert len(result.holdout_customer_ids) > 0


def test_at_least_one_opt_out_was_recorded_during_the_run(result):
    """With OPTOUT_BASE=0.06 and thousands of contacts expected, at least
    one real opt-out label must be drawn — otherwise the fatigue-hazard
    model (Part 10) has no real labels to train on."""
    n_optout = sum(1 for o in result.contact_outcomes if o.opt_out)
    assert n_optout > 0


def test_cleanup_resets_optouts_by_channel_after_the_run(result):
    """The Phase 6 disk-full lesson, applied here: any NEW mutable state a
    Phase 7 run introduces must be reset by the SAME run's cleanup, or a
    later run over overlapping customer_ids sees stale opt-out state and
    gets spurious opt_out.py denials."""
    _population, _signals, ledger = build_dataset(SEED)
    assert sum(1 for o in result.contact_outcomes if o.opt_out) > 0  # precondition: something WAS written

    customer_ids = sorted(set(ledger.risk_customer_map.values()))
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM contact_states "
                "WHERE customer_id = ANY(%s) AND optouts_by_channel != '{}'::jsonb",
                (customer_ids,),
            )
            (residue_count,) = cur.fetchone()
    finally:
        conn.close()
    assert residue_count == 0


def test_deterministic_across_two_independent_postgres_runs(result):
    b = run_arm_b_holdout(seed=SEED, fraction=0.10, backend=BACKEND_POSTGRES)

    a_contact = sorted((o.risk_id, o.recovered, o.amount_recovered_paise) for o in result.contact_outcomes)
    b_contact = sorted((o.risk_id, o.recovered, o.amount_recovered_paise) for o in b.contact_outcomes)
    a_natural = sorted((o.risk_id, o.recovered, o.amount_recovered_paise) for o in result.natural_outcomes)
    b_natural = sorted((o.risk_id, o.recovered, o.amount_recovered_paise) for o in b.natural_outcomes)

    assert a_contact == b_contact
    assert a_natural == b_natural
    assert result.holdout_customer_ids == b.holdout_customer_ids
