-- ---------------------------------------------------------------------------
-- SAMPARK — physical schema (PostgreSQL 16)
--
-- Source of truth for canonical entity fields: spec §6.3 (ER diagram).
-- Application-level contract types (UUID keys, request/grant field shapes):
-- CONTRACTS.md. Phase 4 additions (tables 9-12, grants.budget_window_id):
-- the approved Phase 4 Design Lock §1 / PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md §A.
--
-- Scope: agents, capability_scopes, customers, contact_states, risk_items,
-- grant_requests, grants, audit_events (Phase 0); merchants, budget_windows,
-- customer_margin_windows, contact_slot_claims (Phase 4).
--
-- Hand-written. Not to be regenerated or redesigned without approval.
-- ---------------------------------------------------------------------------


-- =============================================================================
-- 1. AGENTS
-- =============================================================================

CREATE TABLE agents (
    agent_id        TEXT    PRIMARY KEY,
    public_key      TEXT    NOT NULL,
    publisher       TEXT    NOT NULL,
    state           TEXT    NOT NULL,
    strike_count    INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT agents_strike_count_non_negative CHECK (strike_count >= 0),
    CONSTRAINT agents_state_valid CHECK (state IN ('ACTIVE', 'REVOKED'))
);


-- =============================================================================
-- 2. CAPABILITY_SCOPES
--    AGENT ||--|| CAPABILITY_SCOPE : agent_id is both PK and FK.
-- =============================================================================

CREATE TABLE capability_scopes (
    agent_id                TEXT    PRIMARY KEY
                                     REFERENCES agents (agent_id) ON DELETE CASCADE,
    allowed_channels        JSONB   NOT NULL,
    allowed_intents         JSONB   NOT NULL,
    allowed_risk_sources    JSONB   NOT NULL,
    max_incentive_bps       INTEGER NOT NULL,
    max_requests_per_hour   INTEGER NOT NULL,

    CONSTRAINT capability_scopes_max_incentive_bps_non_negative
        CHECK (max_incentive_bps >= 0),
    CONSTRAINT capability_scopes_max_requests_per_hour_non_negative
        CHECK (max_requests_per_hour >= 0),
    CONSTRAINT capability_scopes_allowed_channels_is_array
        CHECK (jsonb_typeof(allowed_channels) = 'array'),
    CONSTRAINT capability_scopes_allowed_intents_is_array
        CHECK (jsonb_typeof(allowed_intents) = 'array'),
    CONSTRAINT capability_scopes_allowed_risk_sources_is_array
        CHECK (jsonb_typeof(allowed_risk_sources) = 'array')
);


-- =============================================================================
-- 3. CUSTOMERS
--    No "must have phone or email" constraint — not yet specified.
-- =============================================================================

CREATE TABLE customers (
    customer_id     TEXT    PRIMARY KEY,
    phone_hash      TEXT,
    email_hash      TEXT
);


-- =============================================================================
-- 4. CONTACT_STATES
--    CUSTOMER ||--|| CONTACT_STATE : customer_id is both PK and FK.
-- =============================================================================

CREATE TABLE contact_states (
    customer_id             TEXT             PRIMARY KEY
                                              REFERENCES customers (customer_id) ON DELETE CASCADE,
    contacts_24h            INTEGER          NOT NULL DEFAULT 0,
    contacts_7d             INTEGER          NOT NULL DEFAULT 0,
    last_contact_at         TIMESTAMPTZ,
    optouts_by_channel      JSONB            NOT NULL,
    consent_scopes          JSONB            NOT NULL,
    fatigue_score           DOUBLE PRECISION NOT NULL,

    CONSTRAINT contact_states_contacts_24h_non_negative CHECK (contacts_24h >= 0),
    CONSTRAINT contact_states_contacts_7d_non_negative CHECK (contacts_7d >= 0),
    CONSTRAINT contact_states_contacts_7d_at_least_24h CHECK (contacts_7d >= contacts_24h),
    CONSTRAINT contact_states_fatigue_score_range CHECK (fatigue_score BETWEEN 0 AND 1),
    CONSTRAINT contact_states_optouts_by_channel_is_object
        CHECK (jsonb_typeof(optouts_by_channel) = 'object'),
    CONSTRAINT contact_states_consent_scopes_is_object
        CHECK (jsonb_typeof(consent_scopes) = 'object')
);


-- =============================================================================
-- 5. RISK_ITEMS
--    CUSTOMER ||--o{ RISK_ITEM : customer_id is a relational FK, not canonical.
--
--    amount_paise is authoritative ledger data. Agents must never redefine it
--    through a grant request — grant_requests does not carry an amount column.
--
--    UNIQUE (risk_id, customer_id) exists solely so grant_requests can hold a
--    composite FK that pins a request's customer_id to its risk_item's
--    customer_id at the database level (see §6 below).
-- =============================================================================

CREATE TABLE risk_items (
    risk_id         TEXT        PRIMARY KEY,
    customer_id     TEXT        NOT NULL REFERENCES customers (customer_id),
    source          TEXT        NOT NULL,
    amount_paise    BIGINT      NOT NULL,
    root_cause      TEXT        NOT NULL,
    detected_at     TIMESTAMPTZ NOT NULL,

    CONSTRAINT risk_items_amount_paise_positive CHECK (amount_paise > 0),
    CONSTRAINT risk_items_risk_customer_uniq UNIQUE (risk_id, customer_id)
);


-- =============================================================================
-- 6. GRANT_REQUESTS
--    AGENT ||--o{ GRANT_REQUEST, RISK_ITEM ||--o{ GRANT_REQUEST.
--
--    No amount_paise, no status, no decision fields, no allocator scores, no
--    budget fields — those are allocator/budget-phase concerns, not the
--    request as signed by the agent.
--
--    customer_id is enforced against the request's own risk_id via the
--    composite FK (risk_id, customer_id) -> risk_items(risk_id, customer_id)
--    rather than a plain single-column FK on risk_id, so the database itself
--    rejects a request whose customer_id disagrees with its risk_item's
--    customer_id.
-- =============================================================================

CREATE TABLE grant_requests (
    request_id                     UUID        PRIMARY KEY,
    agent_id                       TEXT        NOT NULL REFERENCES agents (agent_id),
    customer_id                    TEXT        NOT NULL REFERENCES customers (customer_id),
    risk_id                        TEXT        NOT NULL,
    intent                         TEXT        NOT NULL,
    requested_channel              TEXT        NOT NULL,
    requested_max_incentive_bps    INTEGER     NOT NULL,
    issued_at                      TIMESTAMPTZ NOT NULL,
    signature                      TEXT        NOT NULL,

    CONSTRAINT grant_requests_incentive_bps_non_negative
        CHECK (requested_max_incentive_bps >= 0),
    CONSTRAINT grant_requests_risk_item_customer_match
        FOREIGN KEY (risk_id, customer_id) REFERENCES risk_items (risk_id, customer_id)
);


-- =============================================================================
-- 7. GRANTS
--    GRANT_REQUEST ||--o| GRANT : one grant per request, at most.
--
--    No agent_id, customer_id, risk_id, action payload, allocator score,
--    expected net, TTL column, or budget window ID — those are reachable via
--    request_id -> grant_requests, or are deferred with BudgetWindow.
-- =============================================================================

CREATE TABLE grants (
    grant_id                    UUID        PRIMARY KEY,
    request_id                  UUID        NOT NULL UNIQUE
                                             REFERENCES grant_requests (request_id),
    channel                     TEXT        NOT NULL,
    incentive_ceiling_paise     BIGINT      NOT NULL,
    send_after                  TIMESTAMPTZ NOT NULL,
    expires_at                  TIMESTAMPTZ NOT NULL,
    state                       TEXT        NOT NULL,

    CONSTRAINT grants_incentive_ceiling_paise_non_negative
        CHECK (incentive_ceiling_paise >= 0),
    CONSTRAINT grants_send_after_before_expires CHECK (send_after < expires_at),
    CONSTRAINT grants_state_valid CHECK (
        state IN ('RESERVED', 'EXECUTING', 'CONFIRMED', 'ROLLED_BACK', 'EXPIRED')
    )
);


-- =============================================================================
-- 8. AUDIT_EVENTS
--    Append-only, hash-chained. prev_hash links entries in application logic;
--    an ordinary FK cannot and must not be used to fake that chain integrity.
--
--    grant_id / request_id / customer_id / agent_id are deliberately NOT
--    top-level columns — those identifiers live in payload, keyed per
--    event_type, so this table has no shape dependency on the entities it
--    describes.
-- =============================================================================

CREATE TABLE audit_events (
    event_id         UUID        PRIMARY KEY,
    event_type       TEXT        NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL,
    prev_hash        TEXT        NOT NULL,
    agent_signature  TEXT,
    reason_code      TEXT,
    payload          JSONB       NOT NULL
);


-- =============================================================================
-- 9. MERCHANTS  (Phase 4 — Design Lock §1.1)
--    One row in the simulation ('merchant-sim', seeded below). Exists because
--    spec §6.3 has MERCHANT ||--o{ BUDGET_WINDOW : "funds", and a margin
--    authority with no owner is not an authority.
-- =============================================================================

CREATE TABLE merchants (
    merchant_id     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL
);


-- =============================================================================
-- 10. BUDGET_WINDOWS  (Phase 4 — Design Lock §1.2)
--     The MERCHANT margin authority — spec §6.3's BUDGET_WINDOW, and the
--     target of grants.budget_window_id below.
-- =============================================================================

CREATE TABLE budget_windows (
    budget_window_id        UUID    PRIMARY KEY,
    merchant_id              TEXT    NOT NULL REFERENCES merchants (merchant_id) ON DELETE RESTRICT,
    window_id                DATE    NOT NULL,
    margin_budget_paise      BIGINT  NOT NULL,
    margin_reserved_paise    BIGINT  NOT NULL DEFAULT 0,
    margin_spent_paise       BIGINT  NOT NULL DEFAULT 0,

    CONSTRAINT budget_windows_merchant_window_uniq   UNIQUE (merchant_id, window_id),
    CONSTRAINT budget_windows_budget_non_negative    CHECK (margin_budget_paise   >= 0),
    CONSTRAINT budget_windows_reserved_non_negative  CHECK (margin_reserved_paise >= 0),
    CONSTRAINT budget_windows_spent_non_negative     CHECK (margin_spent_paise    >= 0),
    CONSTRAINT budget_windows_not_overdrawn
        CHECK (margin_reserved_paise + margin_spent_paise <= margin_budget_paise)
);


-- =============================================================================
-- 11. CUSTOMER_MARGIN_WINDOWS  (Phase 4 — Design Lock §1.3)
--     The CUSTOMER margin authority. Deliberately a separate table, not a
--     polymorphic scope_key column on budget_windows — the two authorities
--     have different owners and different FK targets. No FK from grants;
--     reached by the natural key (grant_requests.customer_id,
--     budget_windows.window_id).
--
--     NOTE (Design Lock §1.3, §18.1): with CONTACT_CAP_24H = 1, a customer
--     receives at most one grant per window, so this pool cannot bind in the
--     shipped configuration. Retained because it is the architecturally
--     correct fix for spec §3 failure 3 and goes live the moment the cap is
--     relaxed.
-- =============================================================================

CREATE TABLE customer_margin_windows (
    customer_margin_window_id   UUID    PRIMARY KEY,
    customer_id                  TEXT    NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    window_id                    DATE    NOT NULL,
    margin_budget_paise          BIGINT  NOT NULL,
    margin_reserved_paise        BIGINT  NOT NULL DEFAULT 0,
    margin_spent_paise           BIGINT  NOT NULL DEFAULT 0,

    CONSTRAINT customer_margin_windows_customer_window_uniq UNIQUE (customer_id, window_id),
    CONSTRAINT customer_margin_windows_budget_non_negative   CHECK (margin_budget_paise   >= 0),
    CONSTRAINT customer_margin_windows_reserved_non_negative CHECK (margin_reserved_paise >= 0),
    CONSTRAINT customer_margin_windows_spent_non_negative    CHECK (margin_spent_paise    >= 0),
    CONSTRAINT customer_margin_windows_not_overdrawn
        CHECK (margin_reserved_paise + margin_spent_paise <= margin_budget_paise)
);


-- =============================================================================
-- 12. CONTACT_SLOT_CLAIMS  (Phase 4 — Design Lock §1.4)
--     The contention key. A PARTIAL unique index, not a plain UNIQUE: a
--     ROLLED_BACK or EXPIRED claim must free the window for a retry (spec
--     §12.3's provider-timeout failure) — a plain UNIQUE would permanently
--     burn the window on the first provider failure.
-- =============================================================================

CREATE TABLE contact_slot_claims (
    claim_id        UUID        PRIMARY KEY,
    customer_id     TEXT        NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    window_id       DATE        NOT NULL,
    grant_id        UUID        NOT NULL UNIQUE REFERENCES grants (grant_id) ON DELETE RESTRICT,
    state           TEXT        NOT NULL,
    claimed_at      TIMESTAMPTZ NOT NULL,
    released_at     TIMESTAMPTZ,

    CONSTRAINT contact_slot_claims_state_valid CHECK (
        state IN ('RESERVED', 'EXECUTING', 'CONFIRMED', 'ROLLED_BACK', 'EXPIRED')
    ),
    CONSTRAINT contact_slot_claims_released_iff_terminal CHECK (
        (state IN ('ROLLED_BACK', 'EXPIRED')            AND released_at IS NOT NULL)
     OR (state IN ('RESERVED', 'EXECUTING', 'CONFIRMED') AND released_at IS NULL)
    )
);

-- THE constraint. One active claim per customer per window, enforced by
-- PostgreSQL, not application logic (Design Lock §11's central guarantee).
CREATE UNIQUE INDEX contact_slot_claims_active_uniq
    ON contact_slot_claims (customer_id, window_id)
    WHERE state IN ('RESERVED', 'EXECUTING', 'CONFIRMED');


-- =============================================================================
-- 7 (extended). GRANTS.budget_window_id  (Phase 4 — Design Lock §1.5)
--     Satisfies GRANT }o--|| BUDGET_WINDOW : "draws from". No other change to
--     grants — in particular, no customer_id: contact_slot_claims owns that
--     key (Design Lock §1.4/§19.3, deliberately not reversing the Phase 0
--     decision that grants excludes customer_id).
-- =============================================================================

ALTER TABLE grants
    ADD COLUMN budget_window_id UUID NOT NULL
        REFERENCES budget_windows (budget_window_id) ON DELETE RESTRICT;


-- =============================================================================
-- INDEXES
-- =============================================================================

-- Phase 0 set.
CREATE INDEX idx_risk_items_customer_id ON risk_items (customer_id);
CREATE INDEX idx_grant_requests_agent_id ON grant_requests (agent_id);
CREATE INDEX idx_grant_requests_customer_id ON grant_requests (customer_id);
CREATE INDEX idx_grant_requests_risk_id ON grant_requests (risk_id);
CREATE INDEX idx_audit_events_occurred_at ON audit_events (occurred_at);

-- Phase 4 set — supports the authoritative rolling-cap query (Design Lock
-- §3.4, §11 step 4): grants JOIN grant_requests ON request_id, filtered by
-- grant_requests.customer_id (already indexed above) and grants.state /
-- grants.send_after.
CREATE INDEX idx_grants_state_send_after ON grants (state, send_after);
CREATE INDEX idx_contact_slot_claims_customer_window ON contact_slot_claims (customer_id, window_id);
CREATE INDEX idx_budget_windows_merchant_window ON budget_windows (merchant_id, window_id);
CREATE INDEX idx_customer_margin_windows_customer_window ON customer_margin_windows (customer_id, window_id);


-- =============================================================================
-- REFERENCE DATA  (Phase 4)
-- =============================================================================

-- The simulation's single merchant. Not dynamically created per request —
-- unlike budget_windows/customer_margin_windows, which the issuance
-- transaction creates lazily on first use per window (Design Lock §1.7).
INSERT INTO merchants (merchant_id, display_name)
VALUES ('merchant-sim', 'SAMPARK Simulation Merchant')
ON CONFLICT (merchant_id) DO NOTHING;
