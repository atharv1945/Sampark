# SAMPARK

A mediation layer for revenue-recovery agents. See
`SAMPARK-razorpay-buildathon-spec.md` for the full design, `CLAUDE.md`
for engineering discipline, and `DECISIONS.md` for the build log.

This file grows one phase at a time (spec §18.0); earlier phases have
not yet appended their sections.

## Phase 4 — Mediation core

Implements the SAMPARK mediation layer per the Phase 4 Design Lock
(`C:\Users\athar\.claude\plans\phase-4a-mediation-valiant-pudding.md`):
hard-policy filters, an interlock matrix, a budgeted-greedy allocator
scoring `expected_net` from a forward-looking fatigue term, and a
mediated Arm B runner that reuses the unchanged Phase 2 agents and
Phase 3 registry.

**Status: implementation complete; final evidence run pending.** The
owner-authored PostgreSQL schema additions
(`sampark/schema.sql`, tables 9–12), the `SERIALIZABLE` grant-issuance
transaction (`sampark/budget/issuance.py`), and its 50-way concurrency
test plus negative control (`tests/test_concurrent_grant_issuance.py`
— the phase's most important test, per CLAUDE.md §12) have landed and
pass against real PostgreSQL. The official evidence CLI
(`sim/arm_b_cli.py`) always runs the mediated batch against this real
transaction — never the in-memory reference — and supports the four
precommitted ablations (Design Lock §14.4). The five-seed evidence run
itself (`7, 42, 101, 2024, 31337`) has not yet been executed; see "Not
done" below.

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
31337`) has **not yet been run**. No result is claimed here — see "Not
done" below for what remains before it can be.

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

### Not done in this session

- The full five-seed precommitted evidence run (`7, 42, 101, 2024,
  31337`) across the headline configuration and the four required
  ablations (Design Lock §14.4) — `sim/arm_b_cli.py --seed S --ablation
  A` and `sim/gate.py` are ready; the run itself has not been executed.
- Everything explicitly out of scope for Phase 4 (Design Lock §19):
  the audit chain, uplift/fatigue models, holdout attribution, the LLM
  policy compiler, the UI, and the chaos panel.

## Phase 5 — Audit & Explainability

Implements the tamper-evident audit chain, deterministic
explainability layer, and its wiring into the real Phase 4 decision
path, per the approved Phase 5A design (session-reported) and the two
Phase 5B passes (U-1 schema alignment, then U-2/U-3 integration).

**Status: PHASE 5 COMPLETE.** U-1 (the owner-applied schema migration —
`seq`, `UNIQUE(prev_hash)`, append-only triggers) is live on
`public.audit_events`, verified. U-2 (real Phase 4 execution wired to
real audit emission) and U-3 (`AllocationOutcome.score` threaded from
the allocator's own already-computed value, not recomputed) are both
implemented, tested against real PostgreSQL, and demonstrated against
the live audit store — see the Phase 5 completion report for the full
evidence trail, exact files changed, and test output.

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
- `sampark/audit/sink.py` — **new in U-2.** `AuditSink` (a structural
  `Protocol`) and `PostgresAuditSink`, the object
  `sampark.mediation.service.mediate_window` and `sim/arm_b.py` call
  when given one. Owns the read-only `budget_window_id`/`claim_id`
  lookup against `grants`/`contact_slot_claims` (`Grant` doesn't carry
  either — CONTRACTS.md — and neither does `issue_grant`'s return value).
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
- `sampark/audit/schema_proposal.sql` — the U-1 migration text, now
  applied; kept as the durable record of exactly what was applied.

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

`tests/audit/` — 80 tests, all passing with a live PostgreSQL
configured (57 of those also run and pass with no Postgres — the
pure-Python canonicalization/emitter/explain/privacy/structural
subset). `tests/allocator/test_score_threading.py` — 4 tests, no
database. Zero skips in `tests/audit/` as of U-2/U-3 (the prior
session's single documented skip — the end-to-end determinism test —
is now implemented, not skipped). Full repository suite: see the Phase
5 completion report for the exact final count; zero failures, and every
Phase 4 test directory (`tests/arm_a`, `tests/arm_b`, `tests/mediation`,
`tests/allocator`, `tests/policy`, `tests/budget`, `tests/registry`,
`tests/test_concurrent_grant_issuance.py`) is confirmed to have
actually run, not merely "passed" in aggregate.

`public.audit_events` carries 560 permanent rows as of this phase (558
pre-existing test-fixture rows from before schema isolation existed,
documented and left alone per instruction, plus 2 new rows from one
deliberate, labeled live-store demonstration run as part of this
phase's verification — see the completion report for its exact
request_id/grant_id and the `python -m sampark.audit.verify` output
before and after).
