-- ---------------------------------------------------------------------------
-- SAMPARK — Phase 7 attribution ledger schema PROPOSAL (spec §8.9)
--
-- This is a PROPOSAL, not an applied migration. sampark/schema.sql is
-- human-owned (CLAUDE.md §3 item 1) and has NOT been modified by this
-- Phase 7 session. This file mirrors the U-1 precedent exactly: Claude
-- writes the exact DDL here; the owner reviews, applies it to the live
-- database, and folds it into sampark/schema.sql; this file is then
-- retained UNMODIFIED as the durable record of what was applied (see
-- sampark/audit/schema_proposal.sql for the precedent this repeats).
--
-- ONE new table. No changes to any existing table. No `holdout_assignments`
-- table (membership is a pure deterministic function of
-- (assignment_version, seed, fraction_bps, customer_id) — sim/holdout.py —
-- materializing it would be derivable data with no information gain).
-- ---------------------------------------------------------------------------


-- =============================================================================
-- 13. ATTRIBUTION_CREDITS  (Phase 7 — spec §8.9)
--
--     One row per CONFIRMED grant. No agent_id/customer_id/risk_id columns:
--     reachable via grant_id -> grants.request_id -> grant_requests, the same
--     deliberate exclusion the `grants` table itself already makes (see
--     sampark/schema.sql section 7's own header comment).
--
--     credited_recovery_paise carries NO non-negative CHECK, deliberately.
--     An item that did not recover still consumed a contact against a
--     positive natural baseline; clamping at zero would bias the aggregate
--     upward by exactly the negative tail. Do not "fix" this.
--
--     Cancellation/refund is NOT modelled (Phase 7 design lock, natural-
--     recovery design §2.17). A reversal would be a second, negative row
--     referencing the same grant — never an UPDATE, which the append-only
--     ethos this project already applies to audit_events would forbid in
--     spirit here too — and would require relaxing grant_id UNIQUE.
--     Considered and declined; recorded here so it does not read as an
--     oversight later.
--
--     baseline_holdout_n is on the row so a reader can see the ESTIMATE'S
--     PRECISION without re-deriving it: an n=31 source-level rate and an
--     n=1,900 one are not the same claim.
-- =============================================================================

CREATE TABLE attribution_credits (
    credit_id                 UUID        PRIMARY KEY,
    grant_id                  UUID        NOT NULL UNIQUE
                                           REFERENCES grants (grant_id) ON DELETE RESTRICT,
    observed_recovered_paise  BIGINT      NOT NULL,
    natural_rate_bps          INTEGER     NOT NULL,
    expected_natural_paise    BIGINT      NOT NULL,
    credited_recovery_paise   BIGINT      NOT NULL,
    baseline_stratum          TEXT        NOT NULL,
    baseline_level            TEXT        NOT NULL,
    baseline_holdout_n        INTEGER     NOT NULL,
    holdout_fraction_bps      INTEGER     NOT NULL,
    natural_model_version     INTEGER     NOT NULL,
    observed_at               TIMESTAMPTZ NOT NULL,

    CONSTRAINT attribution_credits_observed_non_negative
        CHECK (observed_recovered_paise >= 0),
    CONSTRAINT attribution_credits_expected_natural_non_negative
        CHECK (expected_natural_paise >= 0),
    CONSTRAINT attribution_credits_natural_rate_range
        CHECK (natural_rate_bps BETWEEN 0 AND 10000),
    CONSTRAINT attribution_credits_holdout_fraction_range
        CHECK (holdout_fraction_bps BETWEEN 0 AND 10000),
    CONSTRAINT attribution_credits_baseline_n_positive
        CHECK (baseline_holdout_n > 0),
    CONSTRAINT attribution_credits_baseline_level_valid
        CHECK (baseline_level IN ('source_root_cause', 'source', 'global')),
    -- THE constraint. The database itself refuses a credit whose
    -- arithmetic does not balance — same class of guarantee as
    -- budget_windows_not_overdrawn (sampark/schema.sql section 10).
    CONSTRAINT attribution_credits_arithmetic
        CHECK (credited_recovery_paise = observed_recovered_paise - expected_natural_paise)
);

CREATE INDEX idx_attribution_credits_grant_id ON attribution_credits (grant_id);
