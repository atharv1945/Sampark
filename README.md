# SAMPARK

A mediation layer for revenue-recovery agents. See
`SAMPARK-razorpay-buildathon-spec.md` for the full design, `CLAUDE.md`
for engineering discipline, and `DECISIONS.md` for the build log.

This file grows one phase at a time (spec §18.0).

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
  by `tests/agents/test_hidden_response_isolation.py`).
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

### Test suite (current, superseding the Phase 4/5 counts reported in
their own sections above)

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
