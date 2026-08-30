"""Attribution ledger persistence — Phase 7, spec §8.9.

Requires `attribution_credits` (`sampark/attribution/schema_proposal.sql`)
to exist on the target connection/schema. `require_migration` fails
LOUDLY (never silently degrades) if it does not, mirroring
`sampark.audit.chain.require_migration`'s exact precedent.

Idempotency mirrors `sim/persistence.py::load_ledger`'s established
pattern (never a blind `ON CONFLICT DO NOTHING`): insert with
`ON CONFLICT (grant_id) DO NOTHING`, then re-read the existing row and
verify it agrees with what this call would have written. A disagreeing
existing row is a genuine data-integrity problem — most commonly two
different runs computing different credits for the same grant — and is
raised as `CreditConflictError`, never silently kept.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row

from sampark.attribution.credit import Credit

if TYPE_CHECKING:
    from sampark.audit.sink import AuditSink
    from sampark.contracts import GrantRequest


class MissingAttributionSchemaError(RuntimeError):
    """attribution_credits does not exist on this connection's
    search_path. The Phase 7 schema proposal
    (sampark/attribution/schema_proposal.sql) has not been applied —
    apply it (or, for tests, create the isolated equivalent — see
    tests/sampark_attribution/conftest.py) before using this module."""


class CreditConflictError(RuntimeError):
    """An existing attribution_credits row for this grant_id disagrees
    with the credit this call would have written. Raised instead of
    silently keeping whichever run got there first (mirrors
    sim/persistence.py::LedgerConflictError's exact precedent)."""


def require_migration(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('attribution_credits')")
        if cur.fetchone()[0] is None:
            raise MissingAttributionSchemaError(
                "attribution_credits does not exist on this connection's search_path. "
                "Apply sampark/attribution/schema_proposal.sql (owner-approved) first."
            )


def insert_credit(
    conn: psycopg.Connection,
    credit: Credit,
    request: "GrantRequest | None" = None,
    audit_sink: "AuditSink | None" = None,
) -> Credit:
    """Insert-or-verify. Returns the credit that IS durable after this
    call — either the one just inserted, or the pre-existing row it was
    found to agree with. Never returns a value that disagrees with what
    is actually in the database.

    `request` / `audit_sink` (Phase 7, additive — both `None` by default,
    every pre-existing caller unaffected): when both are given, emits
    `recovery.credited` AFTER the credit row is durable (Phase 5A §8.2
    Option B — append strictly follows a committed business action,
    the same ordering `sampark.audit.sink.PostgresAuditSink.record_grant_reserved`
    already uses). Emitted from the EXISTING (verified-durable) credit,
    never the caller's possibly-stale argument — on a conflict-then-verify
    retry, the ledger row and the audit event are guaranteed to describe
    the identical fact. `chain.append`'s own idempotency (deterministic
    `event_id`, probed before insert) makes a retried call here a safe
    no-op, exactly as it does for every other event type."""
    require_migration(conn)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO attribution_credits "
            "(credit_id, grant_id, observed_recovered_paise, natural_rate_bps, "
            " expected_natural_paise, credited_recovery_paise, baseline_stratum, "
            " baseline_level, baseline_holdout_n, holdout_fraction_bps, "
            " natural_model_version, observed_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (grant_id) DO NOTHING",
            (
                credit.credit_id, credit.grant_id, credit.observed_recovered_paise,
                credit.natural_rate_bps, credit.expected_natural_paise, credit.credited_recovery_paise,
                credit.baseline_stratum, credit.baseline_level, credit.baseline_holdout_n,
                credit.holdout_fraction_bps, credit.natural_model_version, credit.observed_at,
            ),
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM attribution_credits WHERE grant_id = %s", (credit.grant_id,))
        row = cur.fetchone()

    assert row is not None  # the INSERT above guarantees a row exists, conflict or not

    existing = Credit(
        credit_id=row["credit_id"], grant_id=row["grant_id"],
        observed_recovered_paise=row["observed_recovered_paise"],
        natural_rate_bps=row["natural_rate_bps"], expected_natural_paise=row["expected_natural_paise"],
        credited_recovery_paise=row["credited_recovery_paise"], baseline_stratum=row["baseline_stratum"],
        baseline_level=row["baseline_level"], baseline_holdout_n=row["baseline_holdout_n"],
        holdout_fraction_bps=row["holdout_fraction_bps"], natural_model_version=row["natural_model_version"],
        observed_at=row["observed_at"],
    )

    if existing.credit_id != credit.credit_id or existing.credited_recovery_paise != credit.credited_recovery_paise:
        raise CreditConflictError(
            f"grant_id={credit.grant_id!r} already has a durable credit that disagrees with this "
            f"call's computed credit: existing={existing!r}, attempted={credit!r}"
        )

    if audit_sink is not None:
        assert request is not None, "audit_sink requires the originating request to sign the event with"
        audit_sink.record_recovery_credited(existing, request, at=existing.observed_at)

    return existing
