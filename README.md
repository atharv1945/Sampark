# SAMPARK

**A bounded recovery decision layer for failed payments.**
A proposed integration alongside Razorpay's payment infrastructure, running
against **Razorpay Test Mode**. No real money moves. SAMPARK is not deployed
inside Razorpay and Razorpay does not use it.

> **Authorization decides whether an agent *may* act.
> Authorization alone must not decide which of several equally-authorized agents *should* act.**

---

## In one screen

**A Razorpay payment fails. SAMPARK detects the revenue at risk, decides
whether recovery is worthwhile *and permitted*, selects a bounded intervention,
and records exactly what happened.**

```
Razorpay Test Mode          SAMPARK
──────────────────          ───────
₹1,000 payment link  ─┐
customer pays with    │
a failing test card   │
       ↓              │
  payment.failed ─────┴──►  normalise → one customer, one risk item
  pay_xxx                        ↓
  error_code                agent raises a SIGNED request  (Ed25519)
  contact, email                 ↓
                            registry · capability scope
                                 ↓
                            hard policy · consent, quiet hours, contact caps
                                 ↓
                            score & allocate · expected NET value
                                 ↓
                            reserve (SERIALIZABLE) → execute → confirm | roll back
                                 ↓
                            hash-chained audit log + a deterministic explanation
```

**What Razorpay provides:** payment infrastructure, orders, payment links,
payment status, the failure event and its error code, webhooks, payment
execution primitives.

**What SAMPARK adds:** revenue-at-risk detection, cryptographic agent identity
and capability scope, hard policy enforcement, expected-value scoring and
allocation across competing agents, bounded intervention, auditability, and
explanation.

SAMPARK does not replace Razorpay's payment infrastructure. It adds a decision
and recovery layer around failed and recoverable payments.

### Why retrying isn't enough — demonstrated, not asserted

Run the demo and the ₹1,000 payment is **declined**, with
`allocation.negative_expected_net`.

That is not a bug and nothing was tuned to produce it. With the frozen Phase 4
constants, one contact costs a customer's *future* about **₹541** in expected
recovery — every later attempt on them moves one step down the decay curve — so
a `failed_payment` only clears break-even above roughly **₹1,978**. SAMPARK
declines to spend that customer's single contact slot on a recovery worth less
than what spending it destroys.

**That is the answer to "why can't Razorpay just retry everything?", and it is
arithmetic from committed evidence rather than a rule written for a demo.**
Recovery capacity is a constrained, shared, depletable resource: the customer's
attention. Deciding *which* failures deserve intervention, *which* intervention,
and *which agent* may execute it is the actual problem.

The demo also creates a second, clearly-labelled payment above that threshold,
so the grant → execute → confirm path is visible too. Prioritisation is **not**
"the bigger payment wins": the threshold is expected *net* value, and the
fatigue term depends on what else that customer already has open.

Full detail, including exactly which Razorpay MCP operations were used and what
the webhook does and does not verify:
**[RAZORPAY_INTEGRATION.md](RAZORPAY_INTEGRATION.md)**.

---

## The problem

A merchant runs four AI recovery agents. All four notice Priya's failed payment.
All four hold valid consent, an approved scope, an in-policy discount and a
registered template. Three of them contact her; one at 9:40 PM. Two later claim
the same recovery.

**Every single one of those actions was authorized.**

A permission system evaluates one request at a time and is stateless with respect
to the others — that is what makes it fast and composable, and exactly what makes
it blind here. It permits all four calls, correctly, one at a time. The harm is
*emergent*: it lives in the composition, and no per-request authorizer can express
it, because the cost of burning a customer falls on whichever agent needs her next
week.

SAMPARK is the layer that can express it, because it is the only component that
sees every agent's demand on the same human in the same window. It does not
replace authorization — it takes it as the floor and builds the layer above it.

## The results

Five precommitted seeds, one synthetic merchant month, four identical agents in
both arms. **Arm A** = unmediated status quo. **Arm B** = SAMPARK. **Arm H** =
nobody contacted, natural recovery only.

| Metric | Arm A | Arm B | Δ |
|---|---:|---:|---:|
| Contacts sent | 90,080 | 46,377 | **−48.5 %** |
| Total ₹ recovered (paise) | 8,314,405,039 | 7,633,415,148 | **−8.2 %** |
| ₹ recovered per contact | 90,534 | 152,729 | **+68.7 %** |
| Compliance violations (quiet hours, contact caps, interlocks) | thousands | **0** | — |
| **Scope violations caught by the registry** | **0** | **0** | **0** |

Read the second and third rows together, because they are the whole argument:
**SAMPARK recovers less total money and it is not hiding that.** It recovers it
with half the contacts, because customer attention is a scarce, shared,
depletable resource that a marketplace of independently-authorized agents will
over-consume.

The last row is the **control**. It reads 0/0 and it is *expected* to: the four
agents are correctly scoped in both arms. Every other row moves and that one does
not — which is the cleanest available demonstration that authorization was never
the binding constraint.

*(World-v2 holdout family, f=0.10, 5 seeds — `results/phase9_abh_table.json`.
The frozen Phase 4 gate, world v1, reports mean A 89,387.38 and mean B 156,957.37
paise/contact, uplift range [1.7114, 1.8822], `GATE: PASS`.)*

**Where the improvement actually comes from** — hard policy and contact caps
alone buy ~1.09×; expected-value ranking buys the rest, to ~1.76×; the margin
budget is near-inert at headline capacity; and **the ML models contribute exactly
0.00 %**, because the uplift model is honestly unavailable on this data. That row
reads zero and stays in the table.

## Does it survive when the assumptions move?

A 50-point sensitivity sweep over the two ground-truth response coefficients,
with the grid, the metric and six predictions **committed to git before the sweep
ran** ([`results/phase9_precommitment.json`](results/phase9_precommitment.json)).
All six predictions passed. Three findings, one of which qualifies our own pitch:

| `BETA_FATIGUE` (frozen = 1.0) | 0.0 | 0.5 | **1.0** | 1.5 | 2.0 |
|---|---:|---:|---:|---:|---:|
| Uplift in ₹/contact | 1.5513 | 1.6574 | **1.7559** | 1.8568 | 1.9282 |
| Arm B ÷ Arm A total ₹ | 0.7996 | 0.8542 | **0.9050** | 0.9570 | 0.9938 |

1. **SAMPARK never stops winning on ₹/contact** inside the tested range.
2. **Most of that win is selection, not fatigue.** Switch the cross-agent fatigue
   externality off entirely (`BETA_FATIGUE = 0`) and Arm B still wins by 1.55× —
   about **73 %** of the advantage survives. The spec calls the fatigue term "the
   whole thesis expressed as arithmetic"; the measurement says ranking and
   declining low-value contacts does most of the work. We are reporting that
   against ourselves.
3. **The honest losing condition is total revenue** — Arm B recovers less at every
   tested value. But the gap closes as fatigue worsens, reaching **99.4 %** of Arm
   A's revenue on half the contacts. **Mediation is most valuable exactly where
   customer attention is most fragile.**

## Quickstart

Requires Docker, and Python **3.11** (pinned; `tests/test_environment.py` fails
loudly on anything else).

```bash
# 1. infrastructure — PostgreSQL 16 + Redis
cp .env.example .env          # then fill in POSTGRES_* values
docker compose up -d

# 2. schema (hand-authored, applied explicitly — never on container start)
psql "$DATABASE_URL" -f sampark/schema.sql

# 3. dependencies
python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

**Reproduce the headline in seconds** (these read committed evidence; they run no
simulation):

```bash
python -m sim.gate              # → GATE (mean B ₹/contact > mean A ₹/contact): PASS
python -m sampark.audit.verify  # → VALID: True   (560 events, chain intact)
python -m sim.abh_table         # → rebuilds the A/B/H table from committed evidence
```

**Watch the system make decisions live** — two surfaces, one system:

```bash
uvicorn ui.app:app --host 127.0.0.1 --port 8000
```

| | |
|---|---|
| <http://127.0.0.1:8000> | **Product demo.** One real Razorpay **Test Mode** payment failure through the decision layer. Needs `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`; add `RAZORPAY_MCP_TOKEN` to route it through the Razorpay MCP Server instead of the REST test API. |
| <http://127.0.0.1:8000/system> | **System demo.** The Phase 8 deterministic ~40-second replay: contention between four authorized agents, a provider timeout and rollback, a rogue agent struck and revoked, the model killed mid-run, compliance holding at zero. **Synthetic** — its data comes from the committed seeded generator, not from Razorpay. |

Check the integration without a browser:

```bash
python scripts/verify_razorpay_product_flow.py --probe        # read-only
python scripts/verify_razorpay_product_flow.py --create-link  # one ₹1,000 test link
python scripts/verify_razorpay_product_flow.py --decide plink_XXXX
```

One hands-off ~40-second replay produces all three failure modes with no input:
a provider timeout that rolls back, a two-stage rogue agent that gets struck and
revoked, and a model degradation that falls back to the deterministic heuristic.
The chaos panel lets you trigger seven more on demand.

**Re-run the experiments yourself** (minutes to ~80 minutes; no database needed):

```bash
python -m sim.sensitivity --dimension all   # the §11 sensitivity sweep, ~80 min
python -m pytest -q --ignore=tests/sim_arm_b_holdout   # fast suite, ~6 min
python -m pytest                                        # everything, ~1h45m
```

## Where AI is used — and where it deliberately is not

**Used:** a fatigue-hazard model and an uplift T-learner behind a model-agnostic
scoring seam; an LLM that turns English policy into an **intermediate
representation**, which deterministic code then validates, compiles, and
**generates a pytest case for** — a rule activates only if its own generated test
passes; and a read-only LLM explanation of the audit log.

**Deliberately not used:** root-cause classification was an LLM call and **it was
cut** for a YAML lookup table, because a dictionary is correct and a model is
probabilistic. Allocation is a greedy heuristic with a **published optimality
gap** (within ~0.04 % of a measured per-window optimum), not a solver nobody can
defend under questioning.

**Never LLM-driven:** signature verification, scope checks, compliance filtering,
budget arithmetic, contact counting, margin calculation, the allocation decision,
transactional issuance, attribution arithmetic, audit hashing.

> **A model that can hallucinate must never sit on the path to a money action.**

## Honest limitations, up front

- **It is a simulation.** Synthetic data, a hand-specified response process, and
  mocked channels — you cannot lawfully contact real numbers on synthetic consent.
- **The ML did not work.** The uplift model is structurally unavailable on this
  data and its measured contribution is zero. Nothing here claims otherwise.
- **The live LLM compiler leg was never exercised** (no API key configured); the
  committed fidelity number measures the deterministic pipeline.
- **The allocator trusts agent-declared risk amounts** — a self-interested agent
  could win every grant by overstating. Flagged, not fixed.
- **Contact-cap sensitivity could not be tested** because those constants live in
  protected Phase 4 files.

The complete record is in **[`DISCLAIMER.md`](DISCLAIMER.md)**, which separates
*demonstrated by evidence* from *architectural capability* from *not validated*.

## Where to look

| Document | What it answers |
|---|---|
| **[`ARCHITECTURE.md`](ARCHITECTURE.md)** | How the system works, end to end, with the AI boundaries drawn explicitly |
| **[`DISCLAIMER.md`](DISCLAIMER.md)** | What this cannot honestly claim |
| **[`DECISIONS.md`](DECISIONS.md)** | The build log — what broke, and what was done about it |
| [`results/phase9_metrics_table.md`](results/phase9_metrics_table.md) | The full A/B/H table and the sensitivity sweep, human-readable |
| [`results/phase9_precommitment.json`](results/phase9_precommitment.json) | Sweep predictions, committed to git *before* the sweep ran |
| `SAMPARK-razorpay-buildathon-spec.md` | The original design specification |
| `CLAUDE.md` | Engineering discipline for this repository |

Three tests are worth reading on their own:
`tests/test_concurrent_grant_issuance.py` (50 agents race for the last contact
slot; exactly one wins — and a negative control proves the test can fail),
`tests/test_scope_enforcement.py` (an out-of-scope request is rejected on
signature-verified scope alone, with **no allocator involvement**), and
`tests/test_ui_renders_only_audit_events.py` (the UI renders the audit log and
nothing else — enforced, not asserted).

---

## Build log

The sections below were written one phase at a time as the project was built
(spec §18.0), and are left as they were written.

## Phase 0 — Foundations & Contracts

Exit criterion (spec §18.1): *a test-mode payment link created from code,
and CI passing.* Both demonstrated: `scripts/verify_razorpay_payment_link.py
--create` produced one real Razorpay test-mode Payment Link
(`rzp_test_` credentials only, CLAUDE.md §8) and confirmed it by fetching
the created link back by ID; `.github/workflows/ci.yml` runs the suite on
Python 3.11 (pinned; `tests/test_environment.py` enforces the pin at
runtime).

- `sampark/schema.sql` — the hand-authored, human-owned PostgreSQL schema
  (CLAUDE.md §3).
- `sampark/contracts/**` — Pydantic domain/API contracts, mirrored in
  `CONTRACTS.md`; `tests/contracts` (73 tests).
- `sampark/integrations/razorpay.py` — the SDK wrapper, `rzp_test_`-only,
  SDK calls mocked in `tests/integrations` (11 tests) so CI never reaches
  the real API.
- `.gitignore` (secrets-first), `.env.example` (names only, no values),
  `pyproject.toml` (config-only, no packaging tables — imports resolve via
  the root `conftest.py` instead).

## Phase 1 — Data spine

Exit criterion: *20,000 risk items generated, seeded, reproducible across
two runs.* Demonstrated by `tests/sim_generator/test_generator_volume.py`
and `test_generator_reproducibility.py` (byte/content equality across two
independent generations of the same seed).

- `sim/generator.py`, `sim/population.py`, `sim/seeding.py` — the seeded,
  deterministic simulator (no Python `random`); ~5,000 customers, 20,000
  risk items, 4 canonical sources.
- `sampark/identity/resolution.py` — canonicalize -> SHA-256 ->
  equivalence classes, no `person_id` leakage.
- `sampark/rootcause/{lookup.py,taxonomy.yaml}` — deterministic YAML
  root-cause lookup, 8-value taxonomy, `unmapped -> unknown` (CLAUDE.md §7:
  root-cause classification is explicitly not an LLM task).
- `risk_id` is seed-scoped and non-colliding across seeds; the Postgres
  loader (`sim/persistence.py`) surfaces a conflicting existing row as an
  explicit error rather than masking it with `ON CONFLICT DO NOTHING` —
  see `DECISIONS.md`'s Phase 1 entry for the incident that found this.
- **Known, accepted limitation:** the generator emits no opt-out events
  and no consent-scope data. `sampark/policy/hard/consent_scope.py`'s own
  docstring documents `consent_scopes = {}` as a placeholder, routed to
  `FACT_UNAVAILABLE` (never silently passed). This is why
  `post_optout_contacts`/`consent_scope_violations` read `null` in every
  Phase 4 compliance report.

## Phase 2 — Arm A baseline

Exit criterion: *Arm A runs end to end and emits a metrics file.*
Demonstrated by `sim/arm_a_cli.py` producing
`results/arm_a_metrics_{7,42,101,2024,31337}.json`; `tests/arm_a` (17
tests) plus `tests/agents` (24 tests) plus `tests/sim_environment` (11
tests).

- `agents/{payment_retry,cart_recovery,mandate_recovery,receivables}.py`
  — four thin, genuinely unmediated agents (zero import of
  `sampark.registry`/`allocator`/`budget`/`policy`, mechanically enforced
  by `tests/sim_environment/test_hidden_response_isolation.py`).
- `sim/environment.py` — the sole outcome authority; agents never see
  `HiddenResponseProfile`.
- `recovery_unit = "risk_item"` is stamped explicitly in the metrics
  output — the recovered-amount metric is per risk item, not per unique
  customer or unique economic payment (Phase 7 attribution will later
  define credited recovery against a holdout).
- Exactly-once action/outcome invariant: `tests/arm_a/test_exactly_once_invariant.py`.

## Phase 3 — Agent Registry

Exit criterion: *an out-of-scope request is rejected on signature-verified
scope alone, with no allocator involvement.* Demonstrated by
`tests/test_scope_enforcement.py` — a real Ed25519 keypair, a real
registration, a genuinely valid signature on a request for a channel the
agent never declared, denial via `evaluate_scope` alone, **and** two
structural proofs: an AST parse of `sampark/registry/scope.py`'s own
imports (nothing allocator-shaped) and a signature-parameter check on
`evaluate_scope` itself (no allocator-shaped parameter).

- `sampark/registry/{keys,store,signing,strikes,scope}.py` — Ed25519
  keypairs, registration, capability scopes, detached signatures,
  strikes, revocation; `tests/registry` (42 tests).
- Registration's own check-then-insert race is a consciously **accepted**
  limitation, recorded in `DECISIONS.md`: SERIALIZABLE semantics and the
  concurrency test were deliberately reserved for Phase 4's grant
  issuance, "the central correctness problem," not registration.
- `sampark/registry/strikes.py::record_scope_denial` is a pure, tested
  orchestration function with **no production call site**: no code path
  in `sampark/mediation/service.py` or `sim/arm_b.py` invokes it today.
  Spec §12.3's two-stage rogue-agent demo strikes on stage-two
  *hard-policy* denials (rate ceiling / quiet hours) — a mechanism that
  does not exist in this codebase yet — not on the scope-denial path
  `record_scope_denial` covers. Flagged, not silently wired; see
  `CLAUDE.md` §15.

## Phase 4 — Mediation core

Implements the SAMPARK mediation layer per the Phase 4 Design Lock
(`C:\Users\athar\.claude\plans\phase-4a-mediation-valiant-pudding.md`):
hard-policy filters, an interlock matrix, a budgeted-greedy allocator
scoring `expected_net` from a forward-looking fatigue term, and a
mediated Arm B runner that reuses the unchanged Phase 2 agents and
Phase 3 registry.

**Status: implementation complete; five-seed evidence gate PASS.** The
owner-authored PostgreSQL schema additions
(`sampark/schema.sql`, tables 9–12), the `SERIALIZABLE` grant-issuance
transaction (`sampark/budget/issuance.py`), and its 50-way concurrency
test plus negative control (`tests/test_concurrent_grant_issuance.py`
— the phase's most important test, per CLAUDE.md §12) have landed and
pass against real PostgreSQL. The official evidence CLI
(`sim/arm_b_cli.py`) always runs the mediated batch against this real
transaction — never the in-memory reference — and supports the four
precommitted ablations (Design Lock §14.4). The five-seed evidence run
(`7, 42, 101, 2024, 31337`) across the headline configuration and all
four required ablations has been executed; see "Evidence status" below
for the result.

### What's in

- `sampark/policy/hard/` — opt-out, consent-scope, DLT template, the
  six-row interlock matrix, quiet hours, contact caps. A verdict is
  `ADMISSIBLE`, `INADMISSIBLE` (deny or defer), or `FACT_UNAVAILABLE`
  — the last never silently passes a candidate.
- `sampark/policy/soft/` — the calibrated recovery prior and the
  corrected, forward-looking fatigue term (Design Lock §6).
- `sampark/allocator/` — `expected_net` scoring, the budgeted-greedy
  allocator, calibrated constants (`sim/calibration.py`, run once
  against Arm A's seed-42 log), and frozen policy constants.
- `sampark/budget/` — contact/margin arithmetic;
  `InMemoryMediationLedger` / `InMemoryGrantIssuer` (fast in-memory
  reference, used by unit tests and the "memory" backend only) and
  `sampark/budget/issuance.py` / `sampark/budget/postgres_ledger.py`
  (the real, owner-authored `SERIALIZABLE` transaction and its
  Postgres-backed `MediationLedgerView`) — both conform to the same
  `GrantIssuer` protocol, so the mediation layer is unchanged either
  way.
- `sampark/mediation/` — the request→decision service, hard-policy
  filtering (`sampark/mediation/hard_filter.py` — kept structurally
  separate from `sampark/allocator/`, which has no dependency on
  `sampark.policy.hard`), and grant lifecycle transitions.
- `agents/mediated.py`, `sim/arm_b.py`, `sim/arm_b_cli.py`,
  `sim/gate.py`, `sim/mediation_metrics.py` — the mediated runner, the
  official Postgres-only evidence CLI, and the five-seed gate
  aggregator. Arm A's four agent classes, `Environment`,
  `MockChannelAdapter`, and `sim.metrics.compute_metrics` are reused
  unchanged; mediation is the only experimental difference.

### Evidence status

The five-seed precommitted gate (`sim/gate.py`:
`mean(B ₹/contact) > mean(A ₹/contact)` over seeds `7, 42, 101, 2024,
31337`) is **PASS**, at `constants_commit_sha
aa87123aafdc9d812f5a01c04766c60b9198a2ce`:

| | Arm A | Arm B |
|---|---|---|
| mean ₹/contact (paise) | 89,387.38 | 156,957.37 |
| total contacts | 100,000 | 51,542 |
| total recovered (paise) | 8,938,738,057 | 8,089,920,774 |
| total incentive spend (paise) | 128,017,454 | 84,670,484 |

Uplift ranges 1.7114–1.8822× across the five seeds (stdev 0.0633). Arm B's
enforced-compliance violations are **zero** — quiet-hour, both contact
caps, conflicting-action, and interlock-dispute — across all five seeds.
All four required ablations (Design Lock §14.4) also PASS: `aging_zero`
1.66–1.81×, `merchant_margin_half` 1.71–1.88×, `fifo_under_cap`
1.06–1.14× (the FIFO ablation isolates pure chronological throttling from
value-aware allocation — most of the headline uplift is allocation, not
throttling).

**Reported plainly, because it goes against the thesis's simplest
telling:** Arm B recovers **0.905×** of Arm A's total ₹ (8.09B vs 8.94B
paise) — it wins decisively on ₹/contact by contacting far fewer people,
not by recovering more money overall. The locked gate is ₹/contact
(Design Lock §18.6), which passes; the total-₹ comparison is reported
here so it is not discovered later as an omission.

**Evidence durability, stated plainly:** `results/*.json` (20 metrics
files + 4 gate files backing the table above) was gitignored at Phase 4
close — durable only in the local working tree, not in git — which
conflicted with the precommitment device's own premise (Design Lock
§13.4). **Closed in the Phase 7 session** (Phase 7 design-lock Decision
11): `.gitignore` now tracks `results/*.json`, `.gitattributes` pins it
to LF so `core.autocrlf=true` cannot make the same evidence
byte-different across platforms, and the full Phase 4 + Phase 6 record
is staged. See `CLAUDE.md` §15 for commit status.

### Known gap, stated plainly

The margin-downgrade "abandon if still non-viable" branch
(`sampark/allocator/greedy.py`, Design Lock §8 point 5) is structurally
unreachable under the current scoring formula: `expected_net` is
non-increasing in `incentive_bps` (the heuristic's `p_hat` does not
depend on the incentive offered), so downgrading a candidate's
incentive can only raise or preserve its score, never push an
already-admitted candidate negative. The code path is kept as
defensive handling for a future scoring function (e.g. a Phase 6
uplift model) where incentive genuinely affects conversion probability
— see `tests/allocator/test_greedy.py`'s
`test_margin_shortfall_downgrades_the_winning_grant_but_still_succeeds`
for the test that documents this instead of asserting unreachable
behaviour.

### Out of scope for Phase 4 (Design Lock §19)

The audit chain (Phase 5), uplift/fatigue models (Phase 6), holdout
attribution (Phase 7), the LLM policy compiler (Phase 7), the UI (Phase
8), and the chaos panel (Phase 8). None of these are implemented here.

### CI/Postgres gap — CLOSED (owner-applied since this section was written)

`.github/workflows/ci.yml` had no PostgreSQL service at Phase 4 close
(Design Lock §17.3). **This is now applied**: CI runs a `postgres:16`
service and a schema-apply step, per `CI_POSTGRES_SERVICE_PROPOSAL.md`
(retained as the durable record of the reviewed proposal). See the
Phase 6 section below for current suite counts, which supersede the
542/481+64 figures reported here at Phase 4 close.

## Phase 5 — Audit & Explainability

Implements the tamper-evident audit chain, deterministic
explainability layer, and its wiring into the real Phase 4 decision
path, per the approved Phase 5A design (session-reported) and the two
Phase 5B passes (U-1 schema alignment, then U-2/U-3 integration).

**Status: core implemented and independently verified against real
PostgreSQL; phase not yet formally closed — three items remain, listed
below.** U-1 (the owner-applied schema migration — `seq`,
`UNIQUE(prev_hash)`, append-only triggers) is live on `public.audit_events`,
verified (`python -m sampark.audit.verify`: 560 events, `genesis_ok: True`,
`linkage_ok: True`, `VALID: True`). U-2 (real Phase 4 execution wired to
real audit emission) and U-3 (`AllocationOutcome.score` threaded from
the allocator's own already-computed value, not recomputed) are both
implemented, tested against real PostgreSQL, and demonstrated against
the live audit store.

**Not yet closed:**

- **U-7 (CI/Postgres, approved).** Not applied — see the "CI/Postgres
  gap" note under Phase 4 above; the same gap blocks nine Phase 5 tests,
  including T-18 and T-26, from running in CI at all.
- **U-1 in `sampark/schema.sql` (human-owned).** Live-applied and
  verified against the running database; not yet folded into the
  canonical schema file, so a fresh checkout cannot run Phase 5.
- **U-8 (registry writes -> audit events, approved), partially wired.**
  `agent.registered` is now emitted from `sim/arm_b.py`'s registry setup
  (`_build_agent_registry_memory`/`_build_agent_registry_postgres` ->
  `AuditSink.record_agent_registered`, `audit_sink=None` by default,
  byte-identical to before when omitted — see
  `tests/audit/test_integration.py`'s
  `test_real_agent_registration_produces_agent_registered_events` and
  `test_audit_sink_none_leaves_registration_behavior_unchanged`).
  `agent.struck`/`agent.revoked` remain library-only: no code path
  anywhere calls `sampark.registry.strikes.record_scope_denial` in
  production. Spec §12.3's rogue-agent demo strikes on stage-two
  *hard-policy* denials (rate ceiling / quiet hours) — a mechanism this
  codebase does not implement — not on the scope-denial path
  `record_scope_denial` covers, so wiring that specific function into
  the live decision path would not produce the demo the spec describes.
  Flagged for an explicit owner decision rather than silently wired; see
  `CLAUDE.md` §15.

Phase 4's decision *behavior* is unchanged: only 4 files carry any
diff (`sampark/mediation/service.py`, `sampark/allocator/greedy.py`,
`sampark/allocator/outcomes.py`, `sim/arm_b.py`), every change is an
additive, default-`None` parameter read by no ranking/admission/budget
logic, and `sampark/allocator/constants.py`, `calibrated.py`,
`policies/`, `sampark/policy/`, `sampark/budget/issuance.py`,
`sim/gate.py`, and `sim/arm_b_cli.py` carry zero diff. The five-seed
evidence gate (constants commit `aa87123aafdc9d812f5a01c04766c60b9198a2ce`)
was not rerun — Phase 5 does not touch anything the gate result depends
on — and remains **PASS** (mean B ₹/contact 156,957.37 vs mean A
89,387.38 paise).

### What's in

- `sampark/audit/canonical.py` — the ONE canonical byte representation
  (`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True` —
  reusing exactly `sampark/contracts/grant_request.py`'s existing
  precedent, not a second convention) and SHA-256 hashing. Rejects naive
  datetimes and float payload values outright; every payload string must
  be a controlled ASCII identifier (the same regex enforces both
  determinism and the privacy rule).
- `sampark/audit/chain.py` — the append-only hash chain: `append()`
  (advisory-lock-protected, idempotent, transaction-agnostic),
  `head()`, `verify_chain()` (full linkage check + the grant-
  reservation reconciliation query), `all_events_ordered()`.
- `sampark/audit/emit.py` — the emitter: turns a real Phase 4 decision
  object (`GrantDecision`, `AllocationOutcome`, `Grant`, `Agent`) into a
  draft `AuditEvent`. Structurally forbidden (enforced by AST, matching
  `tests/allocator/test_structural_boundaries.py`'s technique) from
  importing `sampark.policy`, `sampark.allocator.scoring`, or
  `sampark.allocator.greedy` — it copies, it never decides.
- `sampark/audit/sink.py` — `AuditSink` (a structural `Protocol`) and
  `PostgresAuditSink`, the object `sampark.mediation.service.mediate_window`
  and `sim/arm_b.py` call when given one. Owns the read-only
  `budget_window_id`/`claim_id` lookup against `grants`/`contact_slot_claims`
  (`Grant` doesn't carry either — CONTRACTS.md — and neither does
  `issue_grant`'s return value). Also carries `record_agent_registered`
  (U-8's registration half), called from `sim/arm_b.py`'s registry setup.
- `sampark/audit/explain.py` — `explain_request()` and
  `explain_contested_window()`: pure functions of an `AuditEvent`
  sequence, nothing else. No ledger, no database, no policy evaluator.
  Raise `IncompleteLogError` rather than inventing a fact the log
  doesn't contain. `format_explanation()` is a deterministic formatter
  — no LLM (CLAUDE.md §7).
- `sampark/audit/store.py` — targeted read queries (by request, by
  grant, by (customer, window), by agent).
- `sampark/audit/verify.py` — `python -m sampark.audit.verify`, the
  exit-criterion CLI. Run against the live store: 560 events, genesis
  correct, linkage correct, `VALID: True`.
- `sampark/audit/export.py` — streaming canonical JSONL export.
- `sampark/audit/schema_proposal.sql` — the U-1 migration text,
  owner-applied to the live database and verified there; kept as the
  durable record of exactly what was applied. **Not yet folded into
  `sampark/schema.sql`** (human-owned) — a fresh checkout does not yet
  have U-1; see `CLAUDE.md` §15.

### The U-2 integration point

`sampark/mediation/service.py::mediate_window` and `sim/arm_b.py` each
gained one new keyword parameter, `audit_sink: AuditSink | None = None`.
`None` (every pre-U-2 call site, including the official evidence CLI)
means the parameter is never read — byte-identical behavior. The hook
sits inside `mediate_window`'s existing `for outcome in outcomes:` loop
(not a `sim/arm_b.py`-only hook, which an earlier sketch proposed and
which the implementation found would have lost `fact_unavailable_reason_codes`
and `windows_deferred` — neither survives the projection down to
`GrantDecision` that `sim/arm_b.py` alone can see) plus two call sites
in `sim/arm_b.py`'s own loop for `grant.executing`/`grant.confirmed`
(no `AllocationOutcome` needed there, so no fidelity loss from hooking
at that level).

### U-3: score threading, not scoring

`sampark/allocator/greedy.py` already computed a `ScoreBreakdown` for
every admitted candidate; only the *return value* was discarded for two
of the three outcome-producing branches. U-3 keeps a
`{risk_id: ScoreBreakdown}` map built during the existing admission loop
and reads from it when constructing the GRANTED and competitive-loss
`AllocationOutcome`s — zero new `scoring.score()` calls on either path,
proven by an object-identity assertion (`outcome.score is <the exact
object scoring.score returned at admission>`) in
`tests/allocator/test_score_threading.py`.

### Known gap in the payload shapes, stated plainly

`grant.reserved`'s payload does not carry `amount_paise` (its field
list — `grant_id`, `request_id`, `agent_id`, `customer_id`, `risk_id`,
`window_id`, `channel`, `incentive_ceiling_paise`,
`effective_incentive_bps`, `send_after`, `expires_at`,
`budget_window_id`, `claim_id` — was specified exactly by the approved
design). A request that is granted WITHOUT ever passing through a
`decision.deferred`/`decision.denied` event first therefore has no
`amount_paise` recoverable from the log alone —
`DecisionExplanation.risk.amount_paise` is `None` in that case, by
design (never invented), not silently wrong.

### Tests

`tests/audit/` — 82 tests, all passing with a live PostgreSQL configured
(57 of those also run and pass with no Postgres — the pure-Python
canonicalization/emitter/explain/privacy/structural subset; the two new
U-8 registration tests live in `test_integration.py`, which is
module-marked `postgres` throughout, so both require a live database).
`tests/allocator/test_score_threading.py` — 4 tests, no database. Zero
skips in `tests/audit/` when Postgres is configured. Full repository
suite with Postgres configured: **542 passed, 3 skipped** (the 3 are
`tests/budget/test_precheck.py`'s Redis-only tests — legitimate, `redis`
is deliberately not a project dependency). Without Postgres configured
(what CI currently sees — see "CI/Postgres gap" above): **481 passed, 64
skipped**, all `postgres`-marked, none hidden. Every Phase 4 test
directory (`tests/arm_a`, `tests/arm_b`, `tests/mediation`,
`tests/allocator`, `tests/policy`, `tests/budget`, `tests/registry`,
`tests/test_concurrent_grant_issuance.py`) is confirmed to have actually
run, not merely "passed" in aggregate.

`public.audit_events` carries 560 permanent rows as of this phase (558
pre-existing test-fixture rows from before schema isolation existed,
documented and left alone per instruction, plus 2 new rows from one
deliberate, labeled live-store demonstration run as part of this
phase's verification — see the completion report for its exact
request_id/grant_id and the `python -m sampark.audit.verify` output
before and after).

## Phase 6 — Intelligence layer

Adds a model-agnostic scorer seam behind the allocator, a T-learner
uplift model and a fatigue-hazard model (both implemented and tested as
real, working infrastructure), isotonic calibration, and an exact
per-window optimality-gap measurement — per spec §18.1's Phase 6 exit
criterion: *"Models beat the heuristic — or are honestly reported as not
doing so, with the ablation committed."*

**Result: the honest-non-improvement branch, not the models-win branch.**
Both models report `available=False` on this dataset, for concrete,
structural, non-tunable reasons — not a training failure.

### What's in

- `sampark/allocator/scorer.py` — `Scorer` protocol + `HeuristicScorer`,
  wrapping `sampark.allocator.scoring.score` verbatim. `HARD_RULES`,
  `sampark.allocator.greedy`, and every pre-Phase-6 call site are
  behaviourally unaffected (`scorer=None` constructs the identical
  heuristic).
- `sampark/models/uplift.py` — a genuine T-learner (`fit_uplift_model`),
  exercised against synthetic treated/control data in
  `tests/models/test_uplift.py`. `train_uplift_model(seed=42)` reports
  `available=False`: every eligible RiskItem is contacted by its
  matching Phase 2 agent exactly once (max uncontacted fraction
  observed: 0.0000), so there is no untreated population, and
  `incentive_bps` is a fixed per-agent constant within each source, so
  there is no incentive-level split either. A T-learner needs real
  treated/control variation, which requires a holdout arm — Phase 7.
- `sampark/models/fatigue_hazard.py` — same shape. `agents.types.ContactOutcome`
  carries no opt-out field, and `sim/ledger.py` constructs every
  `ContactState` with `optouts_by_channel={}`, unwritten by any
  generator code — no real opt-out signal exists in this dataset.
- `sampark/models/calibration.py` — a thin, deterministic
  `sklearn.isotonic.IsotonicRegression` wrapper, tested on its own
  synthetic data. Not yet wired into `ModelBackedScorer` — nothing real
  reaches it while both upstream models report unavailable.
- `sampark/models/scorer.py::build_scorer()` — the one fallback decision
  point, made once at construction: a missing or invalid model artifact
  → `HeuristicScorer`, reason logged at WARNING. Never raises.
- `sim/optimality_gap.py` — an exact per-window multiple-choice-knapsack
  DP (no external solver — CLAUDE.md §2), tested against brute force
  (`tests/sim_optimality_gap/`, 8/8 passing).

### Evidence status

`phase6_heuristic` (the new scorer seam, run as the frozen
`HeuristicScorer`) reproduces the Phase 4 headline **exactly** —
bit-for-bit on every reported metric — across all five precommitted
seeds. This is the regression proof for the new seam, not new evidence
of an improvement. `phase6_model` (`build_scorer()` against the
committed artifact) falls back to the same heuristic for the same
honest reason and also reproduces the headline exactly. **No unsupported
ML improvement is claimed anywhere in this evidence.**

Optimality gap (seed 42, top-5 windows by admitted ceiling): mean gap
ratio ≈0.9996 / worst-case ≈0.9994 at headline merchant-margin capacity;
≈0.9994 / worst-case ≈0.9985 at half capacity (an artificial stress
test — headline capacity rarely binds). I.e. the shipped greedy
allocator is empirically within ~0.05–0.15% of the measured per-window
optimum — a lower bound on the true achievable optimum, since the DP
does not itself search incentive downgrades (see the module docstring
for the exact caveats: per-window not whole-horizon, no downgrade
search inside the DP).

**Gate summaries for both Phase 6 ablations** (`results/gate_phase6_heuristic.json`,
`results/gate_phase6_model.json`) were generated in the Phase 7 session,
closing the "with the ablation committed" gap — see "Evidence commit
status" below.

### Known failure/recovery incident

Mid-evidence-run, the host `C:` drive filled to 100% free space, crashing
Docker Desktop and severing the live Postgres connection the run was
using. `sim/arm_b.py`'s per-run cleanup needs a live connection to reset
its own transactional rows, so it never ran, leaving 399 orphaned rows;
the next rerun's registry cleanup then failed on an FK constraint. Fixed
by extending `run_phase6_evidence.sh`'s cleanup to also clear the same
tables `_cleanup_postgres_run` already documents as safe. Two further
self-inflicted false starts on the same rerun (forgetting to activate
`.venv`; a fresh shell missing `.env`'s Postgres vars) were fixed the
same session.

### Evidence commit status

At Phase 6 close, `results/*.json` was gitignored despite the exit
criterion's "with the ablation committed" — a version-control gap, not
an engineering one; the models were correctly implemented, correctly
gated, and honestly reported. **Closed in the Phase 7 session**: the
`.gitignore` rule was replaced, `.gitattributes` pins `results/*.json`
to LF, and the two missing gate summaries were generated by pure read of
already-existing result files (no re-run). 45 files staged; see
`CLAUDE.md` §15 for commit status.

### Test suite (as of Phase 6, superseding the Phase 4/5 counts reported in
their own sections above; later phases add to it — see the Phase 8 and
Phase 9 sections for the counts at those points)

Full repository suite with Postgres configured, CI's `postgres:16`
service now applied, and the U-1 audit migration folded into
`sampark/schema.sql`: **588 passed, 3 skipped** (the 3 are
`tests/budget/test_precheck.py`'s Redis-only tests — `redis` is
deliberately not a project dependency). `python -m sampark.audit.verify`:
`VALID: True`, 560 events, `genesis_ok: True`, `linkage_ok: True` — the
audit chain was not disturbed by Phase 6 work.

## Phase 7 — Attribution & policy compiler

Implements spec §8.9 (holdout, natural recovery, credited-recovery
ledger) and §8.4 (English → PolicyIR → deterministic compiled rule).
**Status: engineering closed — implemented, tested against real
PostgreSQL, and evidenced at the scope documented below (single seed for
the `phase7_*` Arm B ablations, no live LLM compile — both explicit,
stated scope reductions, not gaps). Design decisions and evidence are
recorded in `DECISIONS.md`'s Phase 7 entry; the Phase 6 evidence and
this phase's implementation are committed. Phase 8 (demo surface) and
Phase 9 (sensitivity sweep, final A/B/H table, `ARCHITECTURE.md`,
`DISCLAIMER.md`) are deferred and have not been started.**

### World v2 and the holdout

`sim/holdout.py` — deterministic, customer-level assignment (SHA-256
hash-rank within amount-at-risk quintile strata; no RNG). At seed 42:
4,913 real customers, 490 held out at f=0.10 (nested inside 980 held out
at f=0.20), standardized mean difference in amount-at-risk **-0.0074**
(near-perfect balance).

`sim/environment.py` gains `world="v1"|"v2"` (default `"v1"`, byte-
identical to every pre-Phase-7 caller) and two independent RNG
namespaces for opt-out labels and natural recovery. Proven, at real
20k-item scale, that the response-model draw sequence is untouched by
world v2's existence (`tests/sim_environment/test_world_v2.py`), and
that `world="v1"` reproduces `sim.arm_a.run_arm_a` bit-for-bit
(`tests/sim_arm_a_holdout/`).

### Natural recovery / Decision 17 precommitment

`sim/natural.py`'s multiplier table (owner-authored prior; every value
in `[0.05, 0.40]`, `unknown` strictly interior, locked ordering) was
committed before any world-v2 evidence ran. A closed-form prediction
for Arm A-H (seed 42, f=0.10) — computable in advance because Arm A's
uncontacted set is exactly the held-out customers — was computed as
**30,658,277 paise** over 1,962 items; the realized outcome was
**31,947,441 paise** (4.2% relative error, consistent with sampling
noise around the predicted expectation). See
`results/phase7_decision17_precommitment_seed42_f10.json`.

### Holdout validity (seed 42, f=0.10)

The production-realistic holdout estimate (5.10%, n=1,962, 95% Wilson
CI **[4.21%, 6.16%]**) is validated against Arm H's full-population
ground-truth counterfactual (5.29%, n=20,000) — **the ground truth falls
inside the holdout's confidence interval.** Arm H is used only for this
validation, never to compute a production credit (`results/phase7_holdout_validity_seed42_f10.json`).

### Attribution (seed 42, f=0.10, from real Arm A-H data)

18,038 credits computed; the database-style arithmetic invariant
(`credited = observed − expected_natural`) holds for every one. 16,532
resolve at `source_root_cause` baseline precision, 1,506 at `source`.
13,852 credits are negative (an unrecovered contact against a positive
baseline) — **not clamped**, contributing a real -207,579,256 paise tail
to the total (`results/phase7_attribution_seed42_f10.json`). The
Postgres-backed ledger (`sampark/attribution/`, schema proposal only —
`sampark/schema.sql` untouched, CLAUDE.md §3) is proven against real
PostgreSQL in an isolated schema: the arithmetic `CHECK` constraint
rejects a deliberately unbalanced row, the `UNIQUE` constraint rejects a
second credit for one grant, and idempotent retry/conflict semantics
both hold (`tests/sampark_attribution/test_store.py`, 6/6 passing).

### Uplift and fatigue-hazard models

At seed 42, f=0.10: **uplift remains unavailable** — most
`(source, root_cause)` buckets fall below the 200-observation floor in
the control arm (raised from Phase 6's declared-but-never-enforced 30).
At f=0.20, 5 of 16 buckets are still under floor. **The fatigue-hazard
model IS available** — the first Phase 6/7 model to clear its adequacy
gate — with every `(source, root_cause, n)` bucket resolved through a
hierarchical, shrunk fallback (no bucket ever priced at the old
anti-conservative silent zero); every bucket in this run resolved at
`source` or `global` level (0 at `source_root_cause`), matching the
advance prediction that the person-level `fatigue_hazard` parameter
carries no source/root-cause signal by construction. `sampark/models/artifact_data_phase7.py`
(committed, generated) therefore reports `is_valid_for_scoring() = False`
— Phase 7's all-or-nothing gate (both components required) correctly
falls back to `HeuristicScorer`, exactly as `phase6_model` does, for a
different and more specific reason.

### Policy compiler

Full deterministic pipeline (English-authored IR → grammar/bounds/fact-
availability/conflict validation → rule-function generation → back-
rendering) implemented and tested against a 13-case golden corpus (9
canonical + 4 paraphrase, **13/13** reaching their expected verdict —
`results/phase7_compiler_fidelity.json`). Includes both spec §8.4
example sentences that reference facts this system does not have (the
90-day chargeback rule, the RTO Shield rule) — both correctly compile to
`FACT_UNAVAILABLE`, never silently admitted or denied. One deliberately
wrong rule is demonstrated failing its generated test
(`tests/policy/compiler/test_generate.py`), proving the activation gate
is real. `sampark.policy.compiled.composed_hard_rules()` is proven
byte-identical to the frozen 11 `HARD_RULES` when the activation set is
empty (`tests/policy/test_activation_empty_in_protected_evidence.py`) —
the state every Phase 4/6 evidence run sees. **The live English→IR LLM
call was not exercised**: `.env`'s `ANTHROPIC_API_KEY` is present but
empty in this environment, and `sampark/policy/compiler/llm.py` fails
loudly rather than fabricating a response (CLAUDE.md §8) — the golden
corpus's expected IRs are hand-authored, proving the deterministic half
of the pipeline independent of a live model call.

### Phase 4 protection

`git diff aa87123 HEAD -- sampark/allocator/constants.py sampark/allocator/calibrated.py sampark/budget/issuance.py sampark/policy/`
is empty throughout Phase 7. `python -m sim.gate` still reports
**PASS** with the identical headline (mean A 89,387.38 / mean B
156,957.37 paise, uplift 1.7114–1.8822×, `constants_commit_sha
aa87123aafdc9d812f5a01c04766c60b9198a2ce`) after every Phase 7 change in
this session, including a ~108-minute real Postgres-backed Arm B-H
evidence run.

### Attribution → audit wiring, and the official CLI

`sampark/attribution/store.py::insert_credit()` emits `recovery.credited`
from the actual credit-creating operation (optional `audit_sink`/`request`
params, both `None` by default) — after the row is verified durable,
never before, and from the verified row rather than the caller's
argument, so a retry can never desynchronize the ledger and the audit
trail. Proven with 3 real-Postgres integration tests
(`tests/sampark_attribution/test_attribution_audit_integration.py`):
the event is emitted, a retry does not duplicate it, and omitting the
sink (every pre-existing caller) emits nothing.

`sim/arm_b_cli.py` — the official evidence runner — gained three
ablations: `phase7_heuristic`, `phase7_model`, `phase7_model_uplift`.
Run once each against real PostgreSQL at seed 42: all three reproduce
the frozen headline **exactly**, each for a distinct, honest reason
(`phase7_heuristic`: the same `HeuristicScorer`; the two model ablations:
the committed Phase 7 artifact's all-or-nothing gate falling back to
`HeuristicScorer` because uplift is structurally unavailable on this
data). The full 5-seed × 3-ablation matrix was not run — one real run
measured at 48 minutes makes the full matrix ~12 hours, disproportionate
for confirming a code-path property (identical scorer/fallback logic)
that does not vary by seed.

`tests/audit/test_world_v2_does_not_affect_audit_generation.py` proves
world v2 cannot perturb audit-event generation or `prev_hash`: none of
the 11 pre-Phase-7 `sampark.audit.emit.event_for_*` functions accept a
`world` parameter or reference a world-v2-only type.

Final full regression this session (`python -m pytest -q`, run after
every change above): **779 passed, 3 skipped**, exit 0. The concurrency
test (`tests/test_concurrent_grant_issuance.py`) and `python -m sampark.audit.verify`
(`VALID: True`, 560 events) were both re-confirmed standalone
afterward.

---

## Phase 8 — Demo surface

Exit criterion (spec §18.1): *"Someone who hasn't heard the pitch can watch
it and tell you what got denied and why."*

A one-screen live trace over the already-complete Phases 0–7. The UI is
roughly 5% of the engineering and is deliberately a thin observation surface:
every decision it renders was made by unmodified Phase 3/4/6 code.

### Launch

```bash
# 1. infrastructure (Postgres 16 + Redis); schema applied once
docker compose up -d
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" \
     -d "$POSTGRES_DB" -f sampark/schema.sql        # first time only

# 2. environment (same convention as every other CLI in this repo)
set -a; source .env; set +a

# 3. the demo
python -m uvicorn ui.app:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000 and press "Run replay"
```

Headless — the same run, all three failures, no browser:

```bash
python -m sampark.demo.cli --verify
```

`ui/` is a transport shell. Delete it and every Phase 8 behaviour is still
implemented and still tested; `sampark.demo.cli` demonstrates all three
failures with no HTTP layer at all. The UI must never be the thing that makes
a failure "work".

### The trace-integrity rule (spec §12.1) — enforced, not asserted

> The UI renders the audit log and nothing else.

`ui/sse.py` contains exactly one SQL statement and it names exactly one
table, `audit_events`. The frontend keeps three stores that are never merged:
`auditState` (system truth, written only by `ingestAuditEvents`, reachable
only from the SSE handler and its gap-repair fetch), `controlState` (demo
control state — run status, chaos arming — rendered in its own marked
region), and presentation state. There is no `emit_demo_event()`, no
websocket, no second channel.

`tests/test_ui_renders_only_audit_events.py` enforces this four ways:
statically on the backend query and imports; statically on the frontend's
store writer and its call sites; by proving the banned tokens are *used*
nowhere (they may be quoted — comments and string literals are stripped
before scanning, because `ui/sse.py` correctly cites the rule verbatim); and
adversarially against a live run, where every served event is matched to a
real row and a fabricated fact pushed at the API is refused (400/422) without
touching the chain.

One row is honestly **not** audit-derived and is labelled as such on screen:
the Arm A reference numbers, from the committed `results/gate_headline.json`.
Arm A has no audit log, so it cannot be audit-derived; spec §12.2 wants the
side-by-side, so it is shown and marked rather than blended in.

### The three live failures (spec §12.3)

All three occur in a single hands-off replay, with no chaos input at all.

**1. Provider timeout → rollback → idempotent retry.**
`agents/channel.py`'s Phase 2 mock always succeeded, so no failure path
existed. `sampark/demo/provider.py` wraps it unchanged and adds a
`grant_id`-keyed idempotency store plus three failure modes. `HARD_DOWN`
exhausts the retries and the runner calls the existing `rollback_grant`:
margin is released in both pools, the contact slot's `released_at` is set,
and `margin_spent_paise` is untouched — the reservation was returned, not
burned.

The retry is **provider-level**, and that is a finding rather than a
shortcut. `budget/issuance.py` step (1) returns any existing grant for a
`request_id` regardless of state, and `grant_id = uuid5(NS_GRANT,
request_id)`, so one request owns one grant permanently; `ROLLED_BACK` is
terminal in both lifecycle modules. Re-issuing after a rollback would mean
editing the human-owned SERIALIZABLE transaction. Spec §6.2 never asked for
it: its guarantee is *"slot is NOT silently consumed; no double-send on
retry"* — two separate promises, kept by the rollback and by the idempotency
store respectively. `ACCEPT_THEN_TIMEOUT` covers the hard case where the
provider delivered and *then* the caller timed out: the retry returns the
stored receipt and contacts nobody twice.

**2. Rogue agent, two stages.**
Stage 1 — a `voice` channel it never declared, and 4000 bps against its
declared 200 — denied by the Registry on signature-verified scope, with the
allocator never invoked. Stage 2 — in scope: a 23:15 request deferred on
`policy.quiet_hours`, then six correctly-scoped requests inside one simulated
minute against its declared `max_requests_per_hour = 3`, so requests 4–6 are
denied `agent.rate_ceiling_exceeded`, accumulate three strikes, and the key is
revoked. Afterwards it cannot produce a verifiable request at all
(`scope.agent_revoked`).

`CapabilityScope.max_requests_per_hour` had been declared, persisted and
CHECK-constrained since Phase 3 and read by **no evaluation code anywhere**.
`sampark/demo/enforcement.py` is that missing enforcement. It sits after
`evaluate_scope` and before candidate construction — not inside
`evaluate_scope` (which would collapse the two stages), not as a 12th
`HARD_RULES` entry (protected, and would change committed `fact_unavailable`
counts), and not inside `mediate_window` (the Phase 4 decision path).

**Only `agent.rate_ceiling_exceeded` strikes.** Budget and allocation denials
and quiet-hours deferrals must never strike: they are the normal, correct
outcome for a well-behaved agent and occur in the thousands in every committed
Arm B run, so striking on them would revoke all four honest agents within one
run. An agent is struck for misusing the protocol — asking too often — never
for losing a fair contest. This is deliberately narrower than §12.3's literal
list ("budgets, rate ceiling and quiet hours deny. Strikes accumulate"); the
denials all still happen and are all shown, only the *strike* is narrowed.
`record_scope_denial` remains unwired, so the revocation on screen is
unambiguously caused by stage two.

**3. Model kill → heuristic fallback, compliance intact.**
`sampark/demo/scorer_kill.py` wraps the Phase 6 `Scorer` protocol; a kill
makes `score()` raise, the runner emits `model.degraded`, swaps in
`default_scorer()` (the frozen Phase 4 heuristic, bit-for-bit) and re-runs the
window.

**Stated plainly: the uplift model is unavailable on this dataset.**
`build_scorer()` already returns a `HeuristicScorer` because the T-learner has
no untreated control population — the committed Phase 6 finding, which Phase 8
does not reinterpret. So the demo surfaces *both* real degradation reasons and
treats them identically: `model.artifact_unavailable` (true on every run,
emitted at start) and `model.killed_by_operator` (injected live). That
equivalence is the argument — SAMPARK handles "never had a model" and "the
model died mid-run" the same way: detect, degrade, log, keep issuing compliant
grants. Recovery quality may drop; quiet-hour violations, cap breaches and
scope violations stay at zero across the boundary, and that is asserted.

### The seven chaos controls (spec §12.4)

Recovered verbatim from §12.4's table — not invented, not padded. Each maps to
a real backend mechanism whose *effect* reaches the chain. Arming a control is
never itself audited: the log is the decision record, not a UI activity feed.
A control that cannot apply returns **409**, changes nothing, and writes
nothing — it never fakes an effect.

| # | Control | Mechanism |
|---|---|---|
| 1 | Kill uplift model | `KillableScorer.kill()` → `model.degraded` |
| 2 | Revoke agent key | `strikes.revoke()` → `agent.revoked` → `scope.agent_revoked` |
| 3 | Set clock to 21:40 | re-times the next request's `proposed_send_after` |
| 4 | Force provider timeout | `MockProvider.arm()` → `grant.rolled_back` |
| 5 | Flood rogue agent to 6 req/min | rate ceiling → 3 strikes → revocation |
| 6 | Mark customer opted-out mid-run | `UPDATE contact_states` → `policy.opt_out_active` |
| 7 | Trigger RTO flag on an active cart | **substituted** — see below |

**Control 7 is a documented substitution.** `sampark/policy/hard/interlocks.py`
declares the `rto_flag` row with a condition that returns `None`
unconditionally: it never reads the ledger and can only ever report
`FACT_UNAVAILABLE`. Making it deny would require editing that file *and*
`sampark/policy/types.py` (both protected) and would flip
`fact_unavailable.rto_flag` from *recorded* to *resolved*, changing the
committed Phase 4/6/7 counts. The control therefore drives `dispute_open` — a
real, working DENY row of the same interlock matrix, reading
`RiskItem.root_cause`. Same mechanism demonstrated, zero protected-file
changes. The substitution is carried in the control's own `spec_note`, so it
surfaces in the UI as well as here.

### Clock and time compression (spec §12.1)

Simulated time and wall-clock time are kept in separate compartments. Every
instant handed to the system is a real simulated `datetime` derived from the
scenario — nothing is monkeypatched, and `datetime.now` is never read on the
decision path (still structurally tested). Wall-clock pacing is presentation
only and cannot change a decision, a reason code, or the event order. The
compression ratio is **computed** from the scenario's actual span and
displayed; §12.1's illustrative "1 sim-hour ≈ 0.4s" is not hard-coded, because
printing a figure that is not the real one would itself be the unlabelled time
manipulation the same paragraph forbids. At seed 42 the badge reads
`1 sim-hour ~ 0.67s` over a 40-second replay.

Chaos control 3 is *not* a clock mock — it sets a request's
`proposed_send_after`, and `policy.hard.quiet_hours.evaluate` is a pure
function of the instant it is given.

### Deterministic replay

Seed 42, the committed generator, in memory: 8 customers, 56 risk items, 29
honest actions across all four agents, 10 scripted rogue requests, 5 windows,
**113 audit events**, ~40 seconds. The subset is selected by a documented,
random-free rule (rank customers by contended windows, then amount, then id).
There is no second generator and no hand-authored fixture world.

Two determinism tiers are asserted separately rather than conflated:

- **Tier 1 (logical)** — same events, same order, same `event_id`s and reason
  codes.
- **Tier 2 (byte)** — same canonical bytes and the same chain **head hash**,
  achievable because `sim/arm_b.py::_deterministic_keypair` is reused rather
  than reimplemented, so signatures are reproducible too.

Measured: head hash
`333be7b8129a988ae3822079ad5279902093435cc2023ebe45994a3e0382b318` reproduced
identically across the headless CLI, the FastAPI `TestClient`, and a live
`uvicorn` server. `seq` is the SSE transport cursor only and is never treated
as logical identity.

### Isolation — the demo cannot touch the protected chain

`sampark/audit/chain.py` maintains one hash chain per PostgreSQL schema, so a
demo append into `public.audit_events` would extend the real 560-event
Phase 0–7 chain irreversibly (the table is append-only by trigger). Each run
therefore gets its own `sampark_demo_<unix_ts>_<hex>` schema, built by applying
`sampark/schema.sql` **verbatim** under a `search_path` that deliberately omits
`public` — so the demo cannot even read the shared 120k-row `risk_items` table,
let alone write the chain. Cleanup is `DROP SCHEMA CASCADE`, which is DDL and
therefore never intercepted by the append-only triggers.

Four cleanup layers: reset drops; a new run drops the prior; shutdown drops;
and startup sweeps `sampark_demo_%` schemas older than six hours — the only
layer that recovers from a hard crash, which is exactly what the Phase 6
disk-full incident produced.

`tests/demo/test_public_audit_untouched.py` asserts the `public` fingerprint
before and after a complete run.

### One new audit event type, and only one

`model.degraded`. Spec §12.3 requires the allocator to "log a degradation
event" and no existing type carries that fact. Everything else reuses the
existing vocabulary: the rate-ceiling denial is a `decision.denied` with a new
reason-code *string* (`event_for_decision` copies reason codes verbatim and
validates nothing against a closed set), and `agent.struck` / `agent.revoked`
had existed and been unit-tested since Phase 5 with no caller — Phase 8 is the
wiring, not a new mechanism.

### What broke during Phase 8, and what fixed it

A post-run residue check found a row in `public.budget_windows` dated
`2025-09-10` — a *demo* window date — beside the one documented pre-existing
`2099-01-01` fixture artifact. Diagnosis: `DemoSession.reset()` dropped the
demo schema while the runner thread was still mid-run, and `drop_demo_schema`
then reset that shared connection's `search_path` to `public`; the surviving
daemon thread's next unqualified `seed_budget_window` INSERT resolved against
`public`. Fixed in three layers — a cooperative `DemoRunner.request_stop()`
checked at window boundaries; teardown now stops and joins the thread *before*
dropping; and `drop_demo_schema` now leaves `search_path` **empty** rather than
`public`, so anything that escapes the first two layers fails loudly with
"relation does not exist" instead of silently writing to the real database.
`tests/demo/test_reset_never_leaks_into_public.py` pins all three. The stray
row was removed; `public.audit_events` was never affected.

### Cold-viewer validation

Spec §18.1's exit criterion is *"someone who hasn't heard the pitch can watch
it and tell you what got denied and why."* That is the one criterion pytest
cannot settle, so it is split honestly:

**What was verified mechanically.** A harness applies `ui/static/app.js`'s own
classification logic to a live run and checks, for each of the seven questions
a first-time viewer must be able to answer, whether the information is present
and visible in what the UI renders — what happened (7 pipeline stages light),
what was denied (38 denial rows in the loudest region, each with its reason
code), why (every denial carries a machine reason code; click-through yields
*"DENIED on scope: scope.channel_not_allowed. The allocator never ran."*),
what rolled back and recovered, what became of the rogue agent (both stages,
strikes 1→2→3, revocation, then `scope.agent_revoked`), what happened when the
model was killed, and that every displayed fact carries its `event_id`,
`prev_hash` and recomputed `hash` and chains. All seven pass.

**What that validation changed.** It found one real clarity defect:
**"compliance held" was not visible anywhere**, even though §12.3 calls the
recovery-drops-compliance-does-not distinction the whole design philosophy.
Three compliance tiles were added — quiet-hour violations, contact-cap
breaches, and scope violations by honest agents — computed entirely from
fields already on the streamed audit events, so they stay audit-derived system
truth rather than a second source. They are green at zero and **red if ever
non-zero**. All three read 0 on a real run.

**What remains an owner action.** Showing the running demo to a person who has
not heard the pitch. No harness can substitute for that, and this README does
not claim it was done.

### Deliberately out of scope, stated plainly (spec §12.5)

- **The RTO-flag interlock** — `FACT_UNAVAILABLE` by Phase 4 design; control 7
  substitutes `dispute_open`. Named above.
- **LLM-rendered explanations** (spec §8.10) — `ANTHROPIC_API_KEY` is present
  but empty, so the call cannot be exercised or verified. Phase 8 ships the
  deterministic `format_explanation` only, and returns the raw events the
  sentence was derived from so it can be checked against the record rather
  than trusted. No LLM is called anywhere in Phase 8.
- **Authentication** — the demo binds to `127.0.0.1` and has none (§13
  "Out"). Anyone who can reach the port can start, reset and inject faults
  into a throwaway schema. Do not expose it. This is acceptable only because
  the isolation above is structural.
- **Recovery-outcome modelling in the demo** — Phase 8 is a decision-trace
  demo, not an evidence run; grants settle at their reserved ceiling. Arm A/B
  recovery economics remain `sim/`'s job and the committed evidence's.
- **Network partition, clock skew across agents, Postgres failover
  mid-reservation** — named in §12.5 and still not handled.
