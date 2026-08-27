-- ---------------------------------------------------------------------------
-- Phase 5 audit schema migration (U-1).
--
-- STATUS: owner-applied to the live database and verified — seq, both
-- unique indexes, and all three append-only triggers confirmed present
-- on public.audit_events. This file is kept, unmodified, as the durable
-- record of EXACTLY what was applied.
--
-- sampark/schema.sql is human-owned (CLAUDE.md §3) and is NOT modified by
-- this file or by any code in sampark/audit/ — it still does not contain
-- this migration's DDL. A database built from sampark/schema.sql alone
-- (e.g. a fresh checkout) does not yet have U-1; only the specific,
-- already-running database this was applied to does. Folding this DDL
-- into sampark/schema.sql itself remains an owner action — exactly the
-- role PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md played for Phase 4's tables,
-- which the owner did fold into sampark/schema.sql by hand.
--
-- Adds ONE column and enforces THREE invariants PostgreSQL can hold
-- structurally rather than by application convention (Phase 5A §6):
--   1. seq          — an independent, indexed total order, separate from
--                      (and not authenticated by) the hash chain itself —
--                      the second ordering that must agree with hash
--                      linkage for tampering to be detectable (§5.3).
--   2. UNIQUE(prev_hash) — the structural fork guard: two events can never
--                      claim the same predecessor. Also pins exactly one
--                      genesis per database.
--   3. append-only triggers — UPDATE/DELETE/TRUNCATE on audit_events raise,
--      regardless of which role issues them (a BEFORE trigger fires even
--      for a superuser — column-privilege REVOKE alone would not).
--
-- Until this is applied, sampark.audit.chain.* raises
-- MissingSchemaMigrationError rather than silently operating in a weaker
-- mode — see that module's docstring.
--
-- What this does NOT close (say so in the README, Phase 5A §6.2): a
-- superuser can still DROP TRIGGER / DROP INDEX / DROP TABLE. Database-
-- enforced append-only is tamper-EVIDENT against application bugs and
-- ordinary roles, not tamper-PROOF against a DBA with superuser access.
-- ---------------------------------------------------------------------------

-- 1. Ordering + O(1) head lookup. Deliberately EXCLUDED from the hash
--    preimage (sampark/audit/canonical.py) — a persistence concern, the
--    same class as the surrogate UUID keys CONTRACTS.md already notes
--    schema.sql adds; no CONTRACTS.md change implied.
ALTER TABLE audit_events ADD COLUMN seq BIGSERIAL NOT NULL;
CREATE UNIQUE INDEX audit_events_seq_uniq ON audit_events (seq);

-- 2. THE structural fork guard.
CREATE UNIQUE INDEX audit_events_prev_hash_uniq ON audit_events (prev_hash);

-- 3. Append-only, enforced by the database.
CREATE OR REPLACE FUNCTION sampark_audit_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (attempted %)', TG_OP
        USING ERRCODE = 'raise_exception';
END;
$$;

CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION sampark_audit_immutable();
CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION sampark_audit_immutable();
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION sampark_audit_immutable();

-- Optional (U-5, deferred): speeds up sampark.audit.store's
-- events_for_customer_window at Phase 8 UI latency, unnecessary for
-- Phase 5 at ~10^5 rows (a sequential scan is fine).
-- CREATE INDEX idx_audit_events_customer_window
--     ON audit_events ((payload ->> 'customer_id'), (payload ->> 'window_id'));
