# Phase 4 — schema, issuance transaction, and concurrency-test proposal

**This is a proposal artifact, not an implementation.** Nothing in this file has
been written into `sampark/schema.sql`, `sampark/budget/issuance.py`, or
`tests/test_concurrent_grant_issuance.py` — those three are human-owned
(CLAUDE.md §3; Phase 4 Design Lock §17.3) and remain untouched. This document
exists so the owner can review, adjust, and hand-author them without having to
re-derive the design from the Design Lock's prose.

Everything below traces to the approved Phase 4 Design Lock
(`phase-4a-mediation-valiant-pudding.md`), §1, §11, and §12. Where this
document and the Design Lock disagree, the Design Lock is authoritative — this
is a convenience extraction, not a second source of truth.

---

## A. Exact SQL DDL

Four new tables, one new column on `grants`. `grants` itself is **not**
otherwise modified — no `customer_id`, no `window_id` added to it.

```sql
-- =============================================================================
-- 9. MERCHANTS
--    One row in the simulation ('merchant-sim'). Exists because spec §6.3 has
--    MERCHANT ||--o{ BUDGET_WINDOW : "funds", and a margin authority with no
--    owner is not an authority.
-- =============================================================================

CREATE TABLE merchants (
    merchant_id     TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL
);


-- =============================================================================
-- 10. BUDGET_WINDOWS — the MERCHANT margin authority (spec §6.3's BUDGET_WINDOW)
-- =============================================================================

CREATE TABLE budget_windows (
    budget_window_id        UUID    PRIMARY KEY,
    merchant_id             TEXT    NOT NULL REFERENCES merchants (merchant_id) ON DELETE RESTRICT,
    window_id               DATE    NOT NULL,
    margin_budget_paise     BIGINT  NOT NULL,
    margin_reserved_paise   BIGINT  NOT NULL DEFAULT 0,
    margin_spent_paise      BIGINT  NOT NULL DEFAULT 0,

    CONSTRAINT budget_windows_merchant_window_uniq   UNIQUE (merchant_id, window_id),
    CONSTRAINT budget_windows_budget_non_negative    CHECK (margin_budget_paise   >= 0),
    CONSTRAINT budget_windows_reserved_non_negative  CHECK (margin_reserved_paise >= 0),
    CONSTRAINT budget_windows_spent_non_negative     CHECK (margin_spent_paise    >= 0),
    CONSTRAINT budget_windows_not_overdrawn
        CHECK (margin_reserved_paise + margin_spent_paise <= margin_budget_paise)
);


-- =============================================================================
-- 11. CUSTOMER_MARGIN_WINDOWS — the CUSTOMER margin authority
--     Deliberately a separate table, not a polymorphic scope_key column on
--     budget_windows — the two authorities have different owners and
--     different FK targets. No FK from grants; reached by the natural key
--     (grant_requests.customer_id, budget_windows.window_id).
--
--     NOTE (Design Lock §1.3, §18.1): with CONTACT_CAP_24H = 1, a customer
--     receives at most one grant per window, so this pool cannot bind in the
--     shipped configuration. It is the architecturally correct fix for spec
--     §3 failure 3 and goes live the moment the cap is relaxed.
-- =============================================================================

CREATE TABLE customer_margin_windows (
    customer_margin_window_id   UUID    PRIMARY KEY,
    customer_id                 TEXT    NOT NULL REFERENCES customers (customer_id) ON DELETE CASCADE,
    window_id                   DATE    NOT NULL,
    margin_budget_paise         BIGINT  NOT NULL,
    margin_reserved_paise       BIGINT  NOT NULL DEFAULT 0,
    margin_spent_paise          BIGINT  NOT NULL DEFAULT 0,

    CONSTRAINT customer_margin_windows_customer_window_uniq UNIQUE (customer_id, window_id),
    CONSTRAINT customer_margin_windows_budget_non_negative   CHECK (margin_budget_paise   >= 0),
    CONSTRAINT customer_margin_windows_reserved_non_negative CHECK (margin_reserved_paise >= 0),
    CONSTRAINT customer_margin_windows_spent_non_negative    CHECK (margin_spent_paise    >= 0),
    CONSTRAINT customer_margin_windows_not_overdrawn
        CHECK (margin_reserved_paise + margin_spent_paise <= margin_budget_paise)
);


-- =============================================================================
-- 12. CONTACT_SLOT_CLAIMS — the contention key
--     THE constraint: a partial unique index, not a plain UNIQUE, because a
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
        state IN ('RESERVED','EXECUTING','CONFIRMED','ROLLED_BACK','EXPIRED')),
    CONSTRAINT contact_slot_claims_released_iff_terminal CHECK (
        (state IN ('ROLLED_BACK','EXPIRED')            AND released_at IS NOT NULL)
     OR (state IN ('RESERVED','EXECUTING','CONFIRMED')  AND released_at IS NULL))
);

CREATE UNIQUE INDEX contact_slot_claims_active_uniq
    ON contact_slot_claims (customer_id, window_id)
    WHERE state IN ('RESERVED','EXECUTING','CONFIRMED');

CREATE INDEX idx_contact_slot_claims_customer_window
    ON contact_slot_claims (customer_id, window_id);


-- =============================================================================
-- 7 (extended). GRANTS — one added column
--     Satisfies GRANT }o--|| BUDGET_WINDOW : "draws from". No other change —
--     in particular, do NOT add customer_id; contact_slot_claims owns that key.
-- =============================================================================

ALTER TABLE grants
    ADD COLUMN budget_window_id UUID NOT NULL
        REFERENCES budget_windows (budget_window_id) ON DELETE RESTRICT;
```

**FK ordering**: the issuance transaction inserts `grants` before
`contact_slot_claims`, so `contact_slot_claims.grant_id → grants.grant_id` is
satisfied immediately. No `DEFERRABLE` constraint is needed anywhere above.

**Open naming question for the owner (Design Lock §18.4):** `budget_windows`
(merchant) + `customer_margin_windows` reads asymmetrically. A symmetric
alternative is `merchant_budget_windows` + `customer_budget_windows`, with
`grants.budget_window_id` pointing at the merchant table — but that renames
the ER diagram's `BUDGET_WINDOW`. Not resolved here; pick one before applying.

---

## B. Implementation proposal for `sampark/budget/issuance.py`

```python
def issue_grant(
    conn: psycopg.Connection,
    candidate: Candidate,
    effective_incentive_bps: int,
    decision_at: datetime,       # explicit; never now()
) -> GrantIssued | BudgetDenial:
    ...
```

`GrantIssued` / `BudgetDenial` are already defined in
`sampark/budget/store.py` (not owner-owned) — the human-owned function should
return those same two types, so `sampark/mediation/service.py` and
`sampark/allocator/greedy.py` need no changes to swap the reference
`InMemoryGrantIssuer` for the real one. Both already satisfy the `GrantIssuer`
Protocol in `sampark/budget/store.py`.

### Statement sequence, one transaction, `ISOLATION LEVEL SERIALIZABLE`

```sql
BEGIN ISOLATION LEVEL SERIALIZABLE;

-- (1) IDEMPOTENCY. A committed grant for this request_id wins outright.
SELECT grant_id, channel, incentive_ceiling_paise, send_after, expires_at, state
  FROM grants WHERE request_id = :request_id;
--    hit -> COMMIT, return it. No re-allocation, no second reservation.

-- (2) Persist the signed request verbatim (no-op if already present).
INSERT INTO grant_requests (request_id, agent_id, customer_id, risk_id, intent,
                            requested_channel, requested_max_incentive_bps, issued_at, signature)
VALUES (...) ON CONFLICT (request_id) DO NOTHING;

-- (3) LOCK ORDER: merchant pool, then customer pool. Always this order, every caller.
INSERT INTO budget_windows (budget_window_id, merchant_id, window_id, margin_budget_paise)
VALUES (:bw_id, :merchant_id, :window_id, :merchant_budget)
ON CONFLICT (merchant_id, window_id) DO NOTHING;

SELECT budget_window_id, margin_budget_paise, margin_reserved_paise, margin_spent_paise
  FROM budget_windows
 WHERE merchant_id = :merchant_id AND window_id = :window_id
   FOR UPDATE;

INSERT INTO customer_margin_windows (customer_margin_window_id, customer_id, window_id, margin_budget_paise)
VALUES (:cmw_id, :customer_id, :window_id, :customer_budget)
ON CONFLICT (customer_id, window_id) DO NOTHING;

SELECT customer_margin_window_id, margin_budget_paise, margin_reserved_paise, margin_spent_paise
  FROM customer_margin_windows
 WHERE customer_id = :customer_id AND window_id = :window_id
   FOR UPDATE;
--    either pool short -> ROLLBACK, BudgetDenial(budget.*_margin_exhausted, next_eligible_at)

-- (4) AUTHORITATIVE CONTACT CAPS. This read is what SERIALIZABLE protects —
--     it cannot be replaced by a unique index.
SELECT
  count(*) FILTER (WHERE g.send_after > :decision_at - INTERVAL '24 hours') AS c24,
  count(*) FILTER (WHERE g.send_after > :decision_at - INTERVAL '7 days')   AS c7
  FROM grants g JOIN grant_requests r ON r.request_id = g.request_id
 WHERE r.customer_id = :customer_id
   AND g.state IN ('RESERVED','EXECUTING','CONFIRMED');
--    c24 >= 1 -> ROLLBACK, BudgetDenial(budget.contact_cap_24h, next_eligible_at)
--    c7  >= 2 -> ROLLBACK, BudgetDenial(budget.contact_cap_7d, next_eligible_at)

-- (5) GRANT first, so the claim's FK is satisfied immediately (no DEFERRABLE).
INSERT INTO grants (grant_id, request_id, budget_window_id, channel,
                    incentive_ceiling_paise, send_after, expires_at, state)
VALUES (:grant_id, :request_id, :budget_window_id, :channel,
        :ceiling, :send_after, :expires_at, 'RESERVED');

-- (6) THE CONTENDED WRITE. Partial unique index fires here.
INSERT INTO contact_slot_claims (claim_id, customer_id, window_id, grant_id, state, claimed_at)
VALUES (:claim_id, :customer_id, :window_id, :grant_id, 'RESERVED', :decision_at);
--    23505 unique_violation -> ROLLBACK, BudgetDenial(budget.contact_slot_taken, next_eligible_at)

-- (7) RESERVE MARGIN in both pools. The not_overdrawn CHECK is the last line
--     of defence against an arithmetic bug.
UPDATE budget_windows          SET margin_reserved_paise = margin_reserved_paise + :ceiling
 WHERE budget_window_id = :budget_window_id;
UPDATE customer_margin_windows SET margin_reserved_paise = margin_reserved_paise + :ceiling
 WHERE customer_margin_window_id = :customer_margin_window_id;

-- (8) CACHE. Recomputed values, never blind increments — :c24/:c7 are the
--     values just read in step (4), not a stale prior cache read.
UPDATE contact_states
   SET contacts_24h = :c24 + 1, contacts_7d = :c7 + 1, last_contact_at = :send_after
 WHERE customer_id = :customer_id;

COMMIT;
```

### Locking, retries, exception mapping

| Concern | Resolution |
|---|---|
| Lock order | merchant pool → customer pool → `grants` → claim. Identical for every caller, so no deadlock cycle exists |
| `FOR UPDATE` vs bare SSI | Either works for correctness. `FOR UPDATE` turns the margin race into blocking rather than a `40001` storm and makes the lock visible in the code — recommended |
| Unique claim conflict (`23505`) | **Not retried.** Another agent legitimately won the window. Map to `BudgetDenial(budget.contact_slot_taken, next_eligible_at)`; the allocator falls through to the next candidate |
| Serialization failure (`40001`) | Retried, **whole transaction from statement (1)**, up to `MAX_SERIALIZATION_RETRIES = 5` (`sampark/allocator/constants.py`), deterministic backoff — **no jitter**, jitter breaks reproducibility. Exhausted retries → `BudgetDenial(budget.contact_slot_taken, next_eligible_at)` |
| Partial retry | Forbidden — no code path may re-run a subset of the eight statements |
| Any other failure | Roll back the entire transaction. Nothing partial is ever visible |

### `next_eligible_at` for each `BudgetDenial`

- Margin exhaustion (either pool): `sampark.budget.windows.next_window_start(candidate.window_id)`
- Contact cap (24h or 7d): same — `next_window_start(candidate.window_id)`
- Claim taken: same

(All three converge on "try again next window" — Design Lock §8's `next_eligible_at`
column already specifies this uniformly for budget/claim denials.)

---

## C. Implementation proposal for `tests/test_concurrent_grant_issuance.py`

**Fixture**
- One customer; one `window_id`.
- `contact_states` and `grants` seeded so exactly **one** slot remains under
  the 24h cap.
- 50 distinct risk items owned by that customer; 50 distinct `request_id`s.
- Real registered agents with genuinely valid Ed25519 signatures — the race
  runs through `sampark.registry.scope.evaluate_scope` for real, not a stub
  (mirror `tests/registry/conftest.py`'s pattern).
- Both margin pools funded generously so **the contact slot is the binding
  constraint**, not margin (mirror `tests/allocator/conftest.py::make_ledger`'s
  "well-funded" pattern — do not let margin exhaustion mask the concurrency
  result).

**Execution**
- 50 OS threads, **one `psycopg.Connection` per thread** (connections are not
  thread-safe).
- `threading.Barrier(50)` released immediately before each thread calls
  `issue_grant`.
- Every thread's return value and any exception captured into a list, not
  raised across threads.

**Invariants**
1. Exactly **one** row in `contact_slot_claims` for `(customer_id, window_id)`
   with `state IN ('RESERVED','EXECUTING','CONFIRMED')`.
2. Exactly **one** `grants` row referencing that claim.
3. Exactly **49** non-grant results, each a `BudgetDenial` carrying a `budget.*`
   or `interlock.*` reason code — never `scope.*`, never `None`.
4. **Zero uncaught exceptions** across all 50 threads.
5. `contact_states.contacts_24h` equals exactly the cap — not one more, not
   one fewer.
6. `budget_windows.margin_reserved_paise` **and**
   `customer_margin_windows.margin_reserved_paise` each equal exactly one
   grant's ceiling.
7. Every `grants` row references an existing `grant_requests` row.
8. **Idempotent replay:** re-running all 50 with the same 50 `request_id`s
   still yields exactly one grant, and reserved margin is unchanged.

**Negative control — keep this, it is what proves the test is real**

A companion test runs the identical 50-way race against a schema with the
partial unique index dropped and `READ COMMITTED` isolation, and asserts it
**produces more than one grant, or a wrong counter**. Needs its own schema
(separate test database, or create/drop the index inside the test) — it must
not leave the main schema mutated. Without this control, the main test could
pass for the wrong reason (e.g. Python's GIL accidentally serializing
everything) and nobody would know.

**Marker:** `@pytest.mark.postgres` (already registered in `pyproject.toml` by
this session, per §E below). CI **must** run it — it may not silently skip,
because the Phase 4 exit criterion depends on it executing.

---

## D. Redis dependency note

**No Redis dependency was added.** `requirements.txt` is unchanged.
`sampark/budget/precheck.py` imports `redis` inside a `try/except ImportError`
guard and exposes `REDIS_AVAILABLE: bool`; every Redis-specific test in
`tests/budget/test_precheck.py` is `@pytest.mark.skipif(not REDIS_AVAILABLE,
...)` and skips cleanly in this environment (confirmed: 3 skipped, 0 failed).

If the owner wants Redis's advisory pre-check or TTL-index assistance live,
add `redis==5.2.1` to `requirements.txt` — that is an owner decision (Design
Lock §16/§18.3 already records the pushback on making the pre-check binding;
it must remain shadow-only regardless of whether the dependency is added).

---

## E. CI marker change required

`pyproject.toml`'s `[tool.pytest.ini_options]` has `--strict-markers`, which
makes an unregistered `@pytest.mark.postgres` **error out** rather than run.
This session added the registration (the one `pyproject.toml` change Design
Lock §17.2 permits):

```toml
markers = [
    "postgres: requires a live PostgreSQL instance (Design Lock §12, §20) — must run in CI, never silently skip",
]
```

The CI workflow itself (`.github/workflows/`) still needs a job that actually
provisions Postgres, applies `sampark/schema.sql` (including this proposal's
DDL), and runs `pytest -m postgres`. That workflow file was not modified this
session (Design Lock §17.3 — owner-applied).

---

## F. Known integration blockers

1. **Docker was not running this session** (per `CLAUDE.md`'s Phase 0 status
   note) — none of this was verified against a live PostgreSQL instance.
   Everything in Phase 4 was validated against `InMemoryMediationLedger` /
   `InMemoryGrantIssuer` (`sampark/budget/store.py`), which conforms to the
   same `GrantIssuer` protocol but gives **no concurrency guarantee** — see
   that module's docstring.
2. **The one-grant-per-customer-per-window constraint has nowhere to live
   until §A's DDL is applied.** Every Phase 4 test that exercises "one active
   claim" does so against the in-memory reference's own bookkeeping, not a
   real partial unique index.
3. **`tests/test_concurrent_grant_issuance.py` does not exist.** Its design is
   §C above; it cannot be written until `sampark/budget/issuance.py` exists to
   test against.
4. **The customer margin pool is unexercised in the live gate configuration**
   (Design Lock §1.3, §18.1) — `CONTACT_CAP_24H = 1` means it structurally
   cannot bind. A unit test that temporarily relaxes the cap
   (`tests/budget/test_store_issuance.py` does this in-memory today) is the
   only place it is proven to work at all.
5. **The five-seed precommitted evidence run and the four ablations (Design
   Lock §14.4) were not executed this session** — only seed 42 was run
   end-to-end (see `README.md`'s Phase 4 section for that single-seed result,
   labeled as such, not as the evidence run).
