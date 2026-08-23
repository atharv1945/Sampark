# CONTRACTS.md — SAMPARK domain & application contracts

Human-owned artifact (per `CLAUDE.md` §3, items 1–2). Not to be regenerated,
extended, or reinterpreted by an agentic session without approval.

**Sources for everything in this file:**
- `SAMPARK-razorpay-buildathon-spec.md` §6.2 (contact grant lifecycle sequence
  diagram) and §6.3 (ER diagram) — the canonical domain model.
- The literal field lists dictated for `sampark/schema.sql` during this
  repository's Phase 0 schema-authoring session — the one place the
  application-level `GrantRequest` contract was specified field-by-field.

---

## The two artifacts in this file are not the same thing

**Part 1 — Canonical ER entities** is the domain model exactly as diagrammed
in spec §6.3. It describes *what SAMPARK's world is made of*.

**Part 2 — Application/API contracts** describes the *messages* exchanged
during the grant request/decision lifecycle (spec §6.2): what an agent sends,
and what SAMPARK sends back. `GrantRequest` and `GrantDecision` are not ER
entities — they do not each necessarily correspond to one persisted row.

The distinction matters concretely: `GrantRequest` **is** persisted (as
`grant_requests` in `sampark/schema.sql`), but its outcome, `GrantDecision`,
is **not** persisted as its own table — a `GRANT` outcome becomes a `Grant`
row (Part 1); a `DENY` outcome is captured only as an `AuditEvent`.

Neither part of this file is the physical schema. `sampark/schema.sql` adds
things that belong to persistence, not to either contract: relational foreign
keys, surrogate key types (e.g. `UUID` on `grant_requests`/`grants`), the
`(risk_id, customer_id)` composite-uniqueness support column, and SQL-level
`CHECK` constraints. Column presence or absence in `schema.sql` is not
authoritative for what's written here, and vice versa — read
`sampark/schema.sql`'s own header for what it draws from this file.

---

## Part 1 — Canonical ER entities (spec §6.3)

### Relationships (verbatim from the ER diagram)

```
CUSTOMER        ||--o{  RISK_ITEM      : "has"
CUSTOMER        ||--||  CONTACT_STATE  : "has"
CUSTOMER        ||--o{  GRANT          : "receives"
RISK_ITEM       ||--o{  GRANT_REQUEST  : "triggers"
AGENT           ||--||  CAPABILITY_SCOPE : "declares"
AGENT           ||--o{  GRANT_REQUEST  : "signs"
GRANT_REQUEST   ||--o|  GRANT          : "may become"
GRANT           ||--o{  ACTION         : "authorises"
ACTION          ||--o|  OUTCOME        : "produces"
MERCHANT        ||--o{  BUDGET_WINDOW  : "funds"
GRANT           }o--||  BUDGET_WINDOW  : "draws from"
GRANT           ||--|{  AUDIT_EVENT    : "emits"
GRANT_REQUEST   ||--|{  AUDIT_EVENT    : "emits"
```

`MERCHANT`, `BUDGET_WINDOW`, `ACTION`, and `OUTCOME` appear in the diagram's
relationships but are **out of Phase 0 scope** — not detailed as entities
below, and deliberately not created in `sampark/schema.sql` (`MERCHANT` /
`BUDGET_WINDOW` are explicitly deferred to the budget/mediation phase;
`ACTION` / `OUTCOME` have not been scoped for any phase yet).

### Agent

| Field | Type (as diagrammed) | Note |
|---|---|---|
| `agent_id` | string | PK |
| `public_key` | string | |
| `publisher` | string | |
| `state` | string | |
| `strike_count` | int | |

### CapabilityScope

Related to `Agent` 1:1 (`AGENT \|\|--\|\| CAPABILITY_SCOPE : "declares"`).

| Field | Type (as diagrammed) |
|---|---|
| `allowed_channels` | json |
| `allowed_intents` | json |
| `allowed_risk_sources` | json |
| `max_incentive_bps` | int |
| `max_requests_per_hour` | int |

**Note on the three `json` fields:** each is a JSON array of string enum
values — `allowed_channels` an array of channel-name strings,
`allowed_intents` an array of intent-name strings, `allowed_risk_sources` an
array of risk-source-name strings. This describes the array's element shape,
not a change to the canonical field type, which remains `json`/`JSONB`.
These fields are represented as arrays of strings in the application layer.
A closed enum vocabulary may be introduced only after the exact allowed
values are explicitly approved; it must not be expanded silently.

### Customer

| Field | Type (as diagrammed) | Note |
|---|---|---|
| `customer_id` | string | PK |
| `phone_hash` | string | |
| `email_hash` | string | |

### RiskItem

| Field | Type (as diagrammed) | Note |
|---|---|---|
| `risk_id` | string | PK |
| `source` | string | |
| `amount_paise` | int | |
| `root_cause` | string | |
| `detected_at` | timestamp | |

**Invariant (spec §8, restated during schema authoring):** `amount_paise` is
authoritative ledger data. No downstream contract — including `GrantRequest`
— may redefine or restate it.

### ContactState

Related to `Customer` 1:1 (`CUSTOMER \|\|--\|\| CONTACT_STATE : "has"`).

| Field | Type (as diagrammed) |
|---|---|
| `contacts_24h` | int |
| `contacts_7d` | int |
| `last_contact_at` | timestamp |
| `optouts_by_channel` | json |
| `consent_scopes` | json |
| `fatigue_score` | float |

### Grant

| Field | Type (as diagrammed) | Note |
|---|---|---|
| `grant_id` | string | PK |
| `channel` | string | |
| `incentive_ceiling_paise` | int | |
| `send_after` | timestamp | |
| `expires_at` | timestamp | |
| `state` | string | |

### AuditEvent

`event_type` and `occurred_at` are not in the spec's bare §6.3 ER diagram —
they are approved application-level additions to the canonical fields,
dictated during this repository's schema-authoring session (they are also
`NOT NULL` columns in `sampark/schema.sql`). The remaining five fields are
exactly as diagrammed.

| Field | Type (as diagrammed) | Note |
|---|---|---|
| `event_id` | string | PK |
| `event_type` | string | approved application-level addition |
| `occurred_at` | timestamp | approved application-level addition |
| `prev_hash` | string | |
| `agent_signature` | string | |
| `reason_code` | string | |
| `payload` | json | |

**Invariant (spec §12.1, trace-integrity rule):** the audit log is the only
thing any live-trace UI may render. If a stage does not write a durable,
hash-chained `AuditEvent`, it does not exist for display purposes.

**Invariant (spec §8.10):** every registration, revocation, request, grant,
denial, reservation, rollback, and outcome is an `AuditEvent`. `prev_hash`
forms an append-only hash chain — this must never be approximated with an
ordinary foreign key.

---

## Part 2 — Application/API contracts (grant request/decision lifecycle, spec §6.2)

### GrantRequest

Signed by the requesting agent. Persisted verbatim as `grant_requests` in
`sampark/schema.sql`. Field list and types below are exactly as dictated when
that table was authored — these are physical/SQL types, not an abstracted
API type; no separate Pydantic-level type mapping has been approved yet (see
Open items, below).

| Field | Type | Note |
|---|---|---|
| `request_id` | UUID | PK |
| `agent_id` | TEXT | references `Agent` |
| `customer_id` | TEXT | references `Customer` |
| `risk_id` | TEXT | references `RiskItem` |
| `intent` | TEXT | |
| `requested_channel` | TEXT | |
| `requested_max_incentive_bps` | INTEGER | |
| `issued_at` | TIMESTAMPTZ | |
| `signature` | TEXT | |

**Invariants:**

- `GrantRequest.customer_id` must equal the `customer_id` of the `RiskItem`
  it references. Enforced at the database level in `sampark/schema.sql` via a
  composite foreign key on `(risk_id, customer_id)`, in preference to a plain
  single-column FK on `risk_id` alone.
- `GrantRequest` never carries `amount_paise`. `RiskItem.amount_paise` is the
  sole authoritative source; an agent cannot redefine the amount at risk
  through a request.
- `GrantRequest` carries no status, no decision outcome, no allocator score,
  and no budget field. Those belong to `GrantDecision` and to allocator-
  internal state — not to the request as signed by the agent.

### GrantDecision

Approved application/API contract. Not a canonical ER entity, and does not
require a separate PostgreSQL table.

**Fields:**

| Field | Type | Note |
|---|---|---|
| `decision_id` | UUID | |
| `request_id` | UUID | |
| `outcome` | `DecisionOutcome` | |
| `reason_code` | `str \| null` | see note below |
| `human_readable` | `str \| null` | |
| `next_eligible_at` | `datetime \| null` | |
| `grant` | `Grant \| null` | |

**`DecisionOutcome`:**

- `GRANTED`
- `DENIED`
- `DEFERRED`

**Invariants, per outcome:**

`GRANTED`
- `grant != null`
- `reason_code == null`
- `next_eligible_at == null`

`DENIED`
- `grant == null`
- `reason_code != null`
- `next_eligible_at` may be `null`

`DEFERRED`
- `grant == null`
- `reason_code != null`
- `next_eligible_at != null`

**Additional rules:**

- `human_readable` is explanatory only and never authoritative.
- `reason_code` is machine-readable.
- Reason codes are intended to be namespaced strings.
- The controlled vocabulary for `reason_code` will be finalized during
  policy/audit implementation. No closed `ReasonCode` enum has been approved
  yet — do not invent or enumerate reason codes now.
- Scope denials and policy/allocation denials remain distinguishable. This
  reflects spec §6.2's explicit two-tier structure — a scope violation is
  answered by the Registry without the allocator ever running, while a
  budget or interlock denial requires the full comparative evaluation — even
  though both now surface through the same `GrantDecision` shape rather than
  two different response shapes.
- A successful decision references a `Grant`.
- A denied or deferred decision has no `Grant`.

---

## Provenance summary

| Section | Source |
|---|---|
| Part 1, all entities and relationships | Spec §6.3 ER diagram, verbatim |
| Part 1, `RiskItem.amount_paise` invariant | Spec §8 (via schema-authoring session restatement) |
| Part 1, `AuditEvent` invariants | Spec §8.10, §12.1 |
| Part 1, `AuditEvent.event_type` / `occurred_at` fields | Approved application-level additions, dictated during the `sampark/schema.sql` authoring session; also `NOT NULL` in that schema. Not in the bare ER diagram |
| Part 1, `CapabilityScope`'s three `json` fields are arrays of string enum values | Provided directly by the user; canonical field type remains `json`/`JSONB` |
| Part 2, `GrantRequest` fields and types | This repository's `sampark/schema.sql` authoring session (literal, dictated) |
| Part 2, `GrantRequest` invariants | Same session, stated as explicit domain rules |
| Part 2, `GrantDecision` fields, `DecisionOutcome`, invariants | Provided directly by the user as the approved application/API contract |
| Part 2, `GrantDecision.reason_code` typed `str \| null` (not a closed `ReasonCode` enum) | Provided directly by the user; no `ReasonCode` vocabulary has been approved |
| Part 2, `GrantDecision` scope-vs-policy denial distinguishability | Spec §6.2 (two-tier denial-path separation), restated as an explicit rule |
