-- ---------------------------------------------------------------------------
-- SAMPARK — Phase 0 physical schema (PostgreSQL 16)
--
-- Source of truth for canonical entity fields: spec §6.3 (ER diagram).
-- Application-level contract types (UUID keys, request/grant field shapes):
-- CONTRACTS.md.
--
-- Scope: agents, capability_scopes, customers, contact_states, risk_items,
-- grant_requests, grants, audit_events.
--
-- Deliberately NOT created in this phase: merchants, budget_windows.
-- These belong to the budget/mediation implementation phase.
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
-- INDEXES (Phase 0 set only — no speculative indexes)
-- =============================================================================

CREATE INDEX idx_risk_items_customer_id ON risk_items (customer_id);
CREATE INDEX idx_grant_requests_agent_id ON grant_requests (agent_id);
CREATE INDEX idx_grant_requests_customer_id ON grant_requests (customer_id);
CREATE INDEX idx_grant_requests_risk_id ON grant_requests (risk_id);
CREATE INDEX idx_audit_events_occurred_at ON audit_events (occurred_at);
