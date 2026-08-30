"""sampark.attribution.store — Phase 7 (spec §8.9), real PostgreSQL.

Isolated-schema copy of attribution_credits (tests/sampark_attribution/conftest.py)
— proves the arithmetic CHECK constraint, the UNIQUE constraint, and the
idempotent insert-or-verify contract against REAL Postgres, without
touching sampark/schema.sql or the human-owned public schema.
"""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest

from sampark.attribution.baseline import BaselineRate
from sampark.attribution.credit import compute_credit
from sampark.attribution.store import CreditConflictError, MissingAttributionSchemaError, insert_credit

pytestmark = pytest.mark.postgres

OBSERVED_AT = dt.datetime(2025, 10, 9, tzinfo=dt.timezone.utc)


def _credit_for(grant_id, observed=100_000, amount=100_000, rate=0.05):
    baseline = BaselineRate(stratum="mandate_failure.insufficient_funds", level="source_root_cause", rate=rate, n=500)
    return compute_credit(
        grant_id=grant_id, observed_recovered_paise=observed, amount_paise=amount,
        baseline=baseline, holdout_fraction=0.10, observed_at=OBSERVED_AT,
    )


def test_insert_and_readback(pg_conn, real_grant_id):
    credit = _credit_for(real_grant_id)
    result = insert_credit(pg_conn, credit)
    assert result.credit_id == credit.credit_id
    assert result.credited_recovery_paise == credit.credited_recovery_paise

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM attribution_credits WHERE grant_id = %s", (real_grant_id,))
        (count,) = cur.fetchone()
    assert count == 1


def test_database_rejects_unbalanced_arithmetic_via_check_constraint(pg_conn, real_grant_id):
    """THE constraint. A deliberately unbalanced INSERT (bypassing
    compute_credit's own correct arithmetic) must be rejected by the
    database itself, not by Python."""
    credit_id = uuid.uuid4()
    with pytest.raises(psycopg.errors.CheckViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO attribution_credits "
                "(credit_id, grant_id, observed_recovered_paise, natural_rate_bps, "
                " expected_natural_paise, credited_recovery_paise, baseline_stratum, "
                " baseline_level, baseline_holdout_n, holdout_fraction_bps, "
                " natural_model_version, observed_at) "
                "VALUES (%s, %s, 100000, 500, 5000, 999999, 'src.rc', 'source_root_cause', 500, 1000, 1, %s)",
                (credit_id, real_grant_id, OBSERVED_AT),  # 999999 != 100000 - 5000
            )


def test_database_rejects_second_credit_for_same_grant_via_unique_constraint(pg_conn, real_grant_id):
    credit = _credit_for(real_grant_id)
    insert_credit(pg_conn, credit)

    other_credit_id = uuid.uuid4()
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO attribution_credits "
                "(credit_id, grant_id, observed_recovered_paise, natural_rate_bps, "
                " expected_natural_paise, credited_recovery_paise, baseline_stratum, "
                " baseline_level, baseline_holdout_n, holdout_fraction_bps, "
                " natural_model_version, observed_at) "
                "VALUES (%s, %s, 0, 0, 0, 0, 'src.rc', 'global', 500, 1000, 1, %s)",
                (other_credit_id, real_grant_id, OBSERVED_AT),
            )


def test_idempotent_retry_with_identical_credit_returns_the_same_row(pg_conn, real_grant_id):
    credit = _credit_for(real_grant_id)
    first = insert_credit(pg_conn, credit)
    second = insert_credit(pg_conn, credit)  # retry with the IDENTICAL computed credit
    assert first.credit_id == second.credit_id == credit.credit_id

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM attribution_credits WHERE grant_id = %s", (real_grant_id,))
        (count,) = cur.fetchone()
    assert count == 1  # never a duplicate row


def test_conflicting_retry_raises_rather_than_silently_keeping_either(pg_conn, real_grant_id):
    """A DIFFERENT computed credit for the SAME grant_id (e.g. a bug that
    recomputed the baseline differently between two runs) must raise,
    never silently keep whichever run got there first."""
    first = _credit_for(real_grant_id, rate=0.05)
    insert_credit(pg_conn, first)

    conflicting = _credit_for(real_grant_id, rate=0.30)  # SAME grant_id, DIFFERENT baseline -> different credit
    with pytest.raises(CreditConflictError):
        insert_credit(pg_conn, conflicting)


def test_missing_schema_raises_loudly(pg_raw_conn):
    """No isolated schema fixture here — attribution_credits does not
    exist on this connection's default search_path (public, which has
    NOT had the Phase 7 proposal applied — CLAUDE.md §3)."""
    with pg_raw_conn.cursor() as cur:
        cur.execute("SET search_path TO public")
    credit = _credit_for(uuid.uuid4())
    with pytest.raises(MissingAttributionSchemaError):
        insert_credit(pg_raw_conn, credit)
