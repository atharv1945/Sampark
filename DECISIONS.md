Phase 0 — Foundations & Contracts

- Foundation/contracts/schema/payment integration:
  Repo skeleton, hand-authored PostgreSQL schema, Pydantic/domain contracts,
  and the Razorpay Python SDK integration are complete. One real test-mode
  Payment Link was created programmatically through the SDK and confirmed
  by fetching the resulting link back by ID (rzp_test_ credentials only).

- CI requirement:
  CI runs the suite on Python 3.11 (pinned; tests/test_environment.py
  enforces the pin at runtime). CI originally had no PostgreSQL service, so
  every postgres-marked test silently skipped instead of running — see the
  Phase 5 CI/Postgres entry below for the fix (a postgres:16 service added
  to .github/workflows/ci.yml).

Phase 1 — Data Spine

- PostgreSQL 5432 conflict:
  A native PostgreSQL installation on the development machine was already
  listening on 5432. Docker PostgreSQL was therefore moved to local port 5433
  via .env rather than changing the container's internal port.

- Git Bash CRLF issue:
  Sourcing .env from Git Bash retained CRLF characters in values and caused
  authentication problems. The application itself reads environment variables
  directly, so this affected only the shell-based manual workflow.

- risk_id collision:
  Initial risk IDs used generation position only. Loading a second seed exposed
  cross-seed collisions and silent row loss through ON CONFLICT DO NOTHING.
  The identifier was changed to include the seed and the loader was changed to
  detect conflicting existing records explicitly.

Phase 2 — Arm A Baseline

- Arm A implementation:
  Four thin, genuinely unmediated recovery agents implemented (zero import
  of sampark.registry/allocator/budget/policy, mechanically enforced by
  tests/agents/test_hidden_response_isolation.py). Arm A runs end to end
  and emits a metrics file per seed.

- Exactly-once and fatigue semantics:
  Added after a dedicated read-only architecture review. An exactly-once
  action/outcome invariant test proves each eligible RiskItem produces at
  most one ContactAction and each ContactAction produces exactly one
  ContactOutcome. A dedicated cross-agent fatigue test proves fatigue
  accumulation reflects TRUE aggregate contacts across all four agents for
  a shared customer, not per-agent counts.

  Phase 3 — Agent Registry

Registration concurrency:
PostgresAgentRepository registration currently uses a check-then-insert
flow. Concurrent first registrations for the same agent_id can race on the
unique constraint, producing a database uniqueness error rather than the
idempotent/conflict-specific repository error.

Accepted for Phase 3 because registration concurrency is not the phase's
correctness target. SERIALIZABLE transaction semantics and the mandatory
concurrency test are intentionally reserved for Phase 4 grant issuance,
where two agents can race for the last contact slot.

Phase 4 — Mediation Core

- Evidence gate:
  Five-seed evidence run (7, 42, 101, 2024, 31337) executed against the
  official Postgres-only evidence CLI, headline configuration plus all
  four required ablations. Gate condition (mean B ₹/contact > mean A
  ₹/contact) PASSES.

- Result:
  mean A ₹/contact: 89387.38 paise. mean B ₹/contact: 156957.37 paise.
  Uplift range across the five seeds: 1.7114x-1.8822x (stdev 0.0633).
  Arm B enforced-compliance violations: zero (quiet-hour, both contact
  caps, conflicting-action, interlock-dispute) across every seed and
  every ablation.

- Protected constants SHA:
  aa87123aafdc9d812f5a01c04766c60b9198a2ce. Verified identical across all
  five seeds and all four ablations by sim/gate.py's own consistency
  check.

- Heuristic-only:
  The entire measured uplift comes from the calibrated heuristic scorer
  (sampark/allocator/scoring.py, sampark/allocator/calibrated.py) and the
  budgeted-greedy allocator. No learned model is on the decision path.
  Phase 6, if it proceeds, is where a model would be introduced, reported
  as an ablation beside this heuristic result, not as a silent
  replacement of it.

- Total ₹ recovered vs. ₹/contact:
  Arm B recovers 8,089,920,774 paise total, against Arm A's
  8,938,738,057 paise — Arm B recovers about 0.905x of Arm A's total ₹,
  because it contacts far fewer people (51,542 vs 100,000) while winning
  decisively on ₹/contact. This does NOT invalidate the Phase 4 gate: the
  locked exit criterion is mean ₹/contact, not total ₹ recovered, and
  Design Lock §18.6 named this exact tradeoff before the run so that
  reporting it here is not read as spin after the fact.

Phase 5 — Audit & Explainability

- U-1 audit schema:
  seq column (BIGSERIAL, persistence ordering only — deliberately excluded
  from the hash preimage), a unique index on seq, a unique index on
  prev_hash (the structural fork guard), and append-only UPDATE/DELETE/
  TRUNCATE triggers. Owner-applied to the live database and verified live.
  Folded into the canonical sampark/schema.sql in this closure pass;
  verified by applying schema.sql alone to a fresh scratch database and
  confirming its audit_events structure (columns, indexes, triggers)
  matches the live database exactly.

- Canonical serialization / hash chain:
  One canonical byte representation (sort_keys=True,
  separators=(",",":"), ensure_ascii=True), reusing the precedent already
  established by sampark/contracts/grant_request.py. SHA-256 hash is
  always recomputed, never stored on the event.

- Append-only enforcement:
  Enforced by the database (BEFORE triggers), not application convention.
  Verified directly: UPDATE/DELETE/TRUNCATE on audit_events each raise.

- U-2 (real Phase 4 -> audit wiring):
  sampark/mediation/service.py::mediate_window and sim/arm_b.py call a
  real AuditSink when given one (audit_sink=None by default everywhere,
  including the official evidence CLI, so behavior is byte-identical when
  omitted). Demonstrated against real Postgres, real GrantIssuer, real
  MediationLedger, real mediate_window — not mocks.

- U-3 (ScoreBreakdown threading):
  AllocationOutcome.score is threaded from the allocator's own
  already-computed value for the GRANTED and competitive-loss paths, never
  recomputed. Proven by an object-identity assertion, not merely equality.

- Explainability / export:
  explain_request()/explain_contested_window() reconstruct a decision from
  the audit log alone (no ledger, no database, no policy evaluator),
  raising an explicit error rather than inventing a missing fact.
  export.py streams canonical JSONL.

- Deterministic audit stream:
  Two independent real Arm B runs of the identical seed and fixture, each
  into its own isolated schema, produce byte-identical canonical event
  streams including prev_hash at every position.

- Real PostgreSQL integration / live verification:
  All of the above demonstrated against genuine PostgreSQL, not mocks.
  python -m sampark.audit.verify reports VALID: True against the live
  store (560 events at time of writing; genesis and linkage both correct).

- U-8, registration half:
  agent.registered is now emitted from the real registry setup in
  sim/arm_b.py (both backends), audit_sink=None preserving prior behavior
  exactly when omitted. This work also found and fixed a latent bug:
  event_for_agent_registered's payload originally included the free-text
  Agent.publisher field, which violates the canonical payload's
  ASCII-identifier rule the moment a real multi-word publisher string is
  used (the only prior exercises of this function used single-word test
  publishers and never caught it). Fixed by dropping publisher from the
  payload rather than weakening the validation rule.

- Rogue-agent strike mechanism, unresolved (agent.struck / agent.revoked):
  sampark.registry.strikes.record_scope_denial has no production call
  site anywhere in this codebase. Spec §12.3's two-stage rogue-agent demo
  strikes on stage-two HARD-POLICY denials (rate ceiling / quiet hours,
  after scope has already passed) — a trigger mechanism that does not
  exist in this codebase today, and is a different mechanism from
  record_scope_denial, which strikes on SCOPE denials (stage one). Wiring
  record_scope_denial into sampark/mediation/service.py::mediate_window
  would therefore produce a demo that does not match what the
  specification describes, not a shortcut to the same result. Left
  deliberately unwired rather than inventing a hard-policy strike trigger.
  This is Phase 8 (demo surface / chaos panel) territory: the hard-policy
  strike mechanism does not exist yet, and building it is a design
  decision, not an audit-wiring task. agent.struck/agent.revoked remain
  library-only in sampark/audit/emit.py (implemented and unit-tested)
  until that mechanism exists and an owner decision authorizes wiring it.

- CI/Postgres (U-7):
  CI had no PostgreSQL service, so 64 postgres-marked tests silently
  skipped in CI, including tests/test_concurrent_grant_issuance.py (the
  project's most important test per CLAUDE.md §12) and Phase 5's own T-18/
  T-26 exit-criterion tests. Fixed by adding a postgres:16 service (image
  matching docker-compose.yml) plus a schema-apply step to
  .github/workflows/ci.yml.

  Phase 6 — Intelligence layer
Exit criterion (spec §18.1): "models beat the heuristic — or are honestly reported as not doing so, with the ablation committed." Result: the honest branch, not the models-win branch.

phase6_heuristic (the new model-agnostic scorer seam, run as the frozen HeuristicScorer) reproduces the Phase 4 headline exactly — bit-for-bit on every reported metric — across all five precommitted seeds (7, 42, 101, 2024, 31337). This is the regression proof that the new seam didn't silently change behavior.

The uplift T-learner and fatigue-hazard model are both implemented but report available=False on this dataset, for concrete data-availability reasons, identically across all five seeds:

uplift: no treated/control split exists — every eligible risk item is contacted exactly once; a T-learner needs real variation, which requires a holdout arm that doesn't exist until Phase 7.
fatigue hazard: neither ContactOutcome nor ContactState carries any real opt-out signal yet (optouts_by_channel is always {}, unwritten by any generator code).
phase6_model therefore falls back to the same heuristic and also reproduces the headline exactly. No unsupported ML improvement is claimed.

Offline optimality-gap measurement (sim/optimality_gap.py, exact per-window multiple-choice-knapsack DP, tested against brute force): mean gap ratio ≈0.9996 (worst-case ≈0.9994) at headline merchant-margin capacity; ≈0.9994 (worst-case ≈0.9985) at half capacity as a stress test. The shipped greedy allocator is empirically within ~0.05–0.15% of the measured per-window optimum — this is a lower bound, since the DP doesn't search incentive downgrades itself.

What broke, and what we did about it: mid-evidence-run, the host's C: drive filled to 100%, crashing Docker Desktop and killing the live Postgres connection an evidence run was using. sim/arm_b.py's per-run cleanup (_cleanup_postgres_run, a finally block) needs a live connection to reset its own transactional rows, so it never ran — leaving 399 orphaned grant_requests/grants/etc. rows. The next rerun's registry-cleanup step (which only ever cleared agents/capability_scopes) then failed on an FK constraint because of those orphans. Found by reading the FK error and tracing it to the dead-connection cleanup gap; fixed by extending run_phase6_evidence.sh's cleanup to also clear the same tables _cleanup_postgres_run already documents as safe to reset. Two further self-inflicted false starts on the same rerun (forgot to activate .venv; a fresh shell missing .env) were fixed the same session.

Full regression suite: 588 passed, 3 skipped (pre-existing, unrelated to Phase 6).

  Phase 7 — Attribution & policy compiler
Exit criterion (spec §18.1): "compiled rules pass their own generated tests before activating." Demonstrated: tests/policy/compiler/test_generate.py exercises both a correctly compiled rule passing its generated test and a deliberately wrong one failing it.

Holdout: sim/holdout.py assigns customers to holdout by SHA-256 hash-rank within amount-at-risk quintile strata — no RNG, no allocator visibility, so an allocator-declined item can never become a control. At seed 42: 4,913 real customers, 490 held out at f=0.10, nested inside 980 held out at f=0.20 (the f=0.10 set is a strict subset), standardized mean difference in amount-at-risk -0.0074 (near-perfect balance).

Natural recovery: sim/environment.py's world="v2" (default remains "v1", byte-identical to every pre-Phase-7 caller) draws natural recovery from an independent RNG namespace, sampled AFTER the contact stream, over only the uncontacted complement — it cannot influence any allocation decision, and cannot perturb audit-event generation or prev_hash (proven structurally this closure session: none of the 11 pre-Phase-7 sampark.audit.emit.event_for_* functions accept a world parameter or reference any world-v2-only type — tests/audit/test_world_v2_does_not_affect_audit_generation.py). sim/natural.py's per-root-cause multiplier table (every value in [0.05, 0.40], unknown strictly interior) was committed before any world-v2 evidence ran.

Decision-17 precommitment: the Arm A-H natural-recovery prediction for seed 42, f=0.10 (30,658,277 paise over 1,962 uncontacted items) was computed and committed before the corresponding run executed, because Arm A's uncontacted set is exactly the held-out customers and is therefore known in advance. Realized: 31,947,441 paise — 4.2% relative error, consistent with sampling noise around the predicted expectation.

Holdout estimator validation: the production-realistic holdout estimate (5.10%, n=1,962, 95% Wilson CI [4.21%, 6.16%]) was checked against Arm H's full-population ground-truth counterfactual (5.29%, n=20,000) — the ground truth falls inside the holdout's confidence interval. Arm H exists only for this validation; it is structurally excluded from the attribution ledger (build_baseline_estimator only ever receives Arm A-H's natural_outcomes, never Arm H's — enforced by AST tests that fail if either attribution module ever imports sim.arm_h).

Attribution: 18,038 real credits computed from Arm A-H data (seed 42, f=0.10). credited_recovery_paise = observed_recovered_paise - expected_natural_paise holds exactly for every credit, enforced by a database CHECK constraint (sampark/attribution/schema_proposal.sql, proposal only — sampark/schema.sql untouched, owner-owned per CLAUDE.md §3). 13,852 credits are negative (an unrecovered contact against a positive baseline) and are retained, not clamped, contributing a real -207,579,256 paise tail to the total. No merchant-budget scaling is applied under holdout — the frozen budget from Phase 4 is used as-is.

Uplift: the T-learner is honestly unavailable — across all 5 precommitted seeds and both committed holdout fractions (f=0.10, f=0.20), one or more (source, root_cause) buckets fall below the 200-observation-per-arm floor in the holdout control arm. No treated/control split is fabricated to work around this, and no model improvement is claimed.

Fatigue hazard: available — the first Phase 6/7 model to clear its adequacy gate. Every (source, root_cause) bucket resolves through a hierarchical, shrunk fallback (bucket -> source -> global, shrinkage toward the parent level); no bucket is ever priced at the old anti-conservative silent zero (sampark.models.scorer.ModelBackedScorer's dict lookups raise KeyError instead of defaulting to 0.0 for a genuinely missing key — tests/models/test_scorer_no_silent_zero.py).

Policy compiler: English-authored IR -> deterministic grammar/bounds/fact-availability/conflict validation -> rule-function generation -> back-rendering, tested against a 13-case golden corpus (9 canonical + 4 paraphrase), 13/13 reaching their expected verdict. Facts the system does not have (90-day chargeback history, RTO flag) correctly compile to FACT_UNAVAILABLE, never silently admitted or denied. sampark.policy.compiled.composed_hard_rules() is proven byte-identical to the frozen 11 HARD_RULES when the activation set is empty (tests/policy/test_activation_empty_in_protected_evidence.py) — the state every Phase 4/6 evidence run sees — and is deliberately NOT wired into sampark/mediation/hard_filter.py (Phase-4-protected) since policies/activated.yaml has no real compiled rule to exercise it with yet. The live English->IR LLM call was not exercised: ANTHROPIC_API_KEY is absent from this environment (verified directly, not assumed), and sampark/policy/compiler/llm.py fails loudly rather than fabricating a response (CLAUDE.md §8); this is not required by the exit criterion above, which concerns the deterministic IR->rule->test pipeline, proven independently against hand-authored IRs.

Phase 7 ablations: sim/arm_b_cli.py gained phase7_heuristic, phase7_model, and phase7_model_uplift. Each was run once against real PostgreSQL at seed 42 and reproduces the frozen Phase 4 headline exactly (10,299 contacts, 2,691 recoveries, 1,593,664,601 paise recovered, 154,739.74 paise/contact, 18,363,386 paise incentive spend) — phase7_heuristic because it constructs the same HeuristicScorer as headline; phase7_model and phase7_model_uplift because the committed Phase 7 model artifact's all-or-nothing availability gate (both uplift and fatigue hazard required) falls back to HeuristicScorer, since uplift is unavailable on this dataset. The full 5-seed x 3-ablation matrix (15 runs) was deliberately not executed: one real run measured at 48 minutes wall-clock makes the full matrix roughly 12 hours, disproportionate for confirming a code-path identity property (which scorer object gets used) that does not vary by seed, and nothing in spec §8.9/§18.1 requires the full matrix for closure.

Phase 4 protection: git diff aa87123aafdc9d812f5a01c04766c60b9198a2ce HEAD -- sampark/allocator/constants.py sampark/allocator/calibrated.py sampark/budget/issuance.py sampark/policy/ is empty throughout Phase 7. results/gate_headline.json is unchanged: mean A 89,387.38 paise/contact, mean B 156,957.37 paise/contact, uplift ratio range [1.7114, 1.8822], gate PASS.

Full regression suite: 779 passed, 3 skipped (skips are the pre-existing, unrelated redis skips in tests/budget/test_precheck.py), run fresh after every source change in this closure session.

Audit: python -m sampark.audit.verify reports VALID: True, 560 events, genesis_ok: True, linkage_ok: True — unchanged by any Phase 7 or Phase 7-closure activity (Arm B CLI evidence runs pass no audit_sink; Postgres tests use isolated schemas that are torn down afterward).

What broke, and what we did about it, this session: while reconnecting to Postgres for a residue check, a grep intended to redact DATABASE_URL-style connection strings did not match the separate POSTGRES_PASSWORD=... line, and that local test-database password (never a production credential; .env is gitignored; nothing was committed) was echoed once to tool output. Corrected immediately by reconnecting via named environment variables without printing them. Separately, and not yet an incident: host C: free space fell from roughly 6.2GB to 5.4GB (99% used) over this session's evidence collection — the same low-disk condition that crashed Docker Desktop during the Phase 6 session. Nothing crashed this time, but the margin is thin; freeing disk space is recommended before any further large evidence run.

  Phase 8 — Demo surface
Exit criterion (spec §18.1): "someone who hasn't heard the pitch can watch it and tell you what got denied and why." The watchable artifact exists and every mechanism behind it is demonstrated by executable evidence. The cold-viewer judgement itself is the one part pytest cannot make, and it stays an owner validation item — see the last paragraph of this entry.

FastAPI + SSE + a one-screen vanilla-JS trace over the finished Phases 0–7. The UI reads audit_events and nothing else: ui/sse.py contains exactly one SQL statement naming exactly one table, and the frontend keeps three stores that are never merged — auditState (system truth, written only by ingestAuditEvents, reachable only from the SSE handler and its /api/events gap repair), controlState (run status and chaos arming, rendered in its own marked region), and presentation state. tests/test_ui_renders_only_audit_events.py enforces that four ways rather than asserting it: statically on the backend query and imports, statically on the frontend's single store-writer and its call sites, by proving the banned tokens (emit_demo_event, websockets) are used nowhere with comments and string literals stripped first (ui/sse.py correctly quotes the rule verbatim, and a raw text scan would have flagged the citation as a violation), and adversarially against a live run where every served event is matched to a real row and a fabricated fact pushed at the API is refused without touching the chain. One row is deliberately not audit-derived and is labelled as such on screen: the Arm A reference numbers from results/gate_headline.json. Arm A has no audit log, so it cannot be audit-derived; spec §12.2 wants the side-by-side, so it is shown and marked rather than blended in.

Three live failures, all in one hands-off replay with no chaos input. Provider timeout: the Phase 2 mock channel adapter always succeeded, so no failure path existed at all; sampark/demo/provider.py wraps it unchanged and adds a grant_id-keyed idempotency store plus three failure modes, and the exhausted case calls the existing rollback_grant — margin released in both pools, contact slot's released_at set, margin_spent_paise untouched. Rogue agent: stage one is a voice channel it never declared and 4000 bps against its declared 200, both denied by the Registry on signature-verified scope with the allocator never invoked; stage two is six correctly-scoped requests inside one simulated minute against its declared max_requests_per_hour of 3, so requests 4–6 are denied on the rate ceiling, accumulate three strikes, and the key is revoked, after which it cannot produce a verifiable request at all. Model kill: sampark/demo/scorer_kill.py wraps the Phase 6 Scorer protocol, a kill makes score() raise, the runner emits model.degraded, swaps in the frozen Phase 4 heuristic and re-runs the window. Measured on a real run: 113 audit events, 1 grant.rolled_back after 2 real provider retries, 9 grant.confirmed, scope.channel_not_allowed and scope.incentive_ceiling_exceeded once each, 3 rate-ceiling denials with strike_count going 1→2→3, 1 agent.revoked, 4 subsequent scope.agent_revoked denials, and 2 model.degraded events. The four honest agents finish ACTIVE with strike_count 0.

CapabilityScope.max_requests_per_hour had been declared, persisted and CHECK-constrained since Phase 3 and read by no evaluation code anywhere; sampark/demo/enforcement.py is that missing enforcement. It sits after evaluate_scope and before candidate construction — not inside evaluate_scope, which would collapse the two stages spec §12.3 calls "the entire thesis in ninety seconds"; not as a 12th HARD_RULES entry, which is protected and would change the committed fact_unavailable counts; and not inside mediate_window, which is the Phase 4 decision path. record_scope_denial remains unwired, so the revocation on camera is unambiguously caused by stage two.

Approved deviation 1 — only agent.rate_ceiling_exceeded causes a strike. Spec §12.3's literal list is "budgets, rate ceiling and quiet hours deny. Strikes accumulate." Taking that literally would revoke all four honest agents inside a single run, because losing a budget contest is the normal outcome for a well-behaved agent and happens in the thousands in every committed Arm B run. The denials all still happen and are all shown; only the strike is narrowed. An agent is struck for misusing the protocol — asking too often — never for losing a fair contest. tests/demo/test_rate_ceiling_and_strikes.py::test_losing_a_fair_contest_can_never_strike pins the closed set.

Approved deviation 2 — chaos control 7 drives dispute_open, not rto_flag. The rto_flag interlock row is declared with a condition that returns None unconditionally: it never reads the ledger, so no data change can make it deny. Making it deny would require editing sampark/policy/hard/interlocks.py and sampark/policy/types.py, both protected, and would flip fact_unavailable.rto_flag from recorded to resolved across every future run, changing the committed Phase 4/6/7 counts. dispute_open is a real, working DENY row of the same interlock matrix, reading RiskItem.root_cause, so the interlock matrix is still what gets demonstrated. The substitution is carried in the control's own spec_note, so it reaches the screen as well as this file.

Provider rollback and retry — a finding, not a design choice. "Rollback, then retry the same request" is not implementable against this codebase: sampark/budget/issuance.py step (1) returns any existing grant for a request_id regardless of state, grant_id is uuid5 of the request_id so one request owns one grant permanently, and ROLLED_BACK is terminal in both sampark/mediation/lifecycle.py and sampark/budget/postgres_ledger.py. A re-issue would mean editing the human-owned SERIALIZABLE transaction. Re-reading spec §6.2 showed it never asked for one: "slot is NOT silently consumed; no double-send on retry" is two separate promises, and they are kept separately — the rollback returns the margin and the contact slot, and a grant_id-keyed idempotency store in the fake provider prevents the second send. The ACCEPT_THEN_TIMEOUT mode covers the case that actually matters, where the provider delivered and the caller then timed out: the retry returns the stored receipt and contacts nobody twice. No terminal ROLLED_BACK grant is ever re-issued or re-executed, and nothing in this phase claims otherwise.

Model availability is not reinterpreted. build_scorer() still returns HeuristicScorer on this dataset because the uplift T-learner has no untreated control population — the committed Phase 6 finding, unchanged. Rather than fake a live model in order to kill it, the demo logs both real degradation reasons and treats them identically: model.artifact_unavailable, emitted at run start and true on every run, and model.killed_by_operator, injected by the chaos control. Both converge on the same deterministic fallback to the frozen Phase 4 heuristic. That equivalence is the better demonstration anyway — the system handles "never had a model" and "the model died mid-run" the same way, which is the point of spec §12.3's third failure.

Seven chaos controls, recovered verbatim from spec §12.4 and each mapped to a real backend mechanism whose effect reaches the chain: kill uplift model, revoke agent key, set clock to 21:40, force provider timeout, flood rogue agent to 6 req/min, mark customer opted-out mid-run, and the interlock control above. Arming a control is never itself audited — the log is the decision record, not a UI activity feed — and a control that cannot apply returns 409, changes nothing and writes nothing rather than faking an effect. Control 3 is not a clock mock: nothing in this codebase reads a wall clock on the decision path, and sampark.policy.hard.quiet_hours.evaluate is a pure function of the instant it is handed, so setting the instant is the mechanism.

Isolation is the property the whole phase rests on. sampark/audit/chain.py keeps one hash chain per PostgreSQL schema, so a demo append into public.audit_events would extend the real 560-event chain irreversibly, the table being append-only by trigger. Each run therefore gets its own sampark_demo_<unix_ts>_<hex> schema built by applying sampark/schema.sql verbatim under a search_path that deliberately omits public, so the demo cannot even read the shared 120,000-row risk_items table, let alone write the chain. Cleanup is DROP SCHEMA CASCADE, which is DDL and so is not intercepted by the append-only triggers. Four cleanup layers: reset drops, a new run drops the prior, shutdown drops, and startup sweeps schemas older than six hours — the only layer that recovers from a hard crash, which is exactly what Phase 6 produced.

Determinism: 113 events and head hash 333be7b8129a988ae3822079ad5279902093435cc2023ebe45994a3e0382b318, reproduced identically across three independent execution paths — the headless sampark.demo.cli, the FastAPI TestClient, and a live uvicorn server. Both tiers hold, logical projection and full canonical bytes including signatures, because sim/arm_b.py's deterministic keypair helper was reused rather than reimplemented. seq is the SSE transport cursor only and is never treated as logical identity. Wall-clock pacing is presentation and cannot change a decision; the compression ratio is computed from the scenario's actual span and displayed (1 sim-hour ≈ 0.67s at seed 42), because printing spec §12.1's illustrative 0.4s when the real figure differs would itself be the unlabelled time manipulation that paragraph forbids.

One new event type only: model.degraded, because spec §12.3 requires the allocator to log a degradation event and no existing type carried that fact. Everything else reuses the vocabulary — the rate-ceiling denial is a decision.denied with a new reason-code string, since event_for_decision copies reason codes verbatim and validates nothing against a closed set, and agent.struck/agent.revoked had been implemented and unit-tested since Phase 5 with no caller, so this phase is the wiring rather than a new mechanism. TYPE_ORDER places model.degraded at 0 rather than below holdout.assigned, because tests/audit/test_emit_phase7.py asserts holdout.assigned is the unique minimum and a Phase 8 convenience does not get to weaken a Phase 7 invariant; 0 still sorts it strictly before the decisions it explains.

What broke, and what we did about it. A post-run residue check found a row in public.budget_windows dated 2025-09-10 — a demo window date — beside the one inert 2099-01-01 fixture artifact that has been there since Phase 6. Traced to reset ordering: DemoSession.reset() dropped the demo schema while the runner thread was still going, and drop_demo_schema then reset that shared connection's search_path to public, so the surviving daemon thread's next unqualified INSERT landed in the real database. Fixed in three layers — a cooperative DemoRunner.request_stop() checked at window boundaries, teardown that stops and joins the thread before dropping, and drop_demo_schema now leaving search_path empty instead of public so anything that gets past the first two fails loudly with "relation does not exist" rather than writing silently. tests/demo/test_reset_never_leaks_into_public.py pins all three layers. The stray row was removed; public.audit_events was never affected, 560 events before and after every run.

Second incident, self-inflicted and operational rather than a code defect: the first full regression run was killed at roughly 80% on a mistaken diagnosis of a hang. Get-Process.CPU reported 0 seconds after 3h26m elapsed, which was taken as evidence of a stall — but that counter reads 0 in this environment even for a process that is demonstrably working, and the Postgres evidence gathered at the same time (a single idle session on the contact-caps query, zero ungranted locks) was consistent with normal execution and was over-ruled by the bogus number. The suite was in fact executing tests/sim_arm_b_holdout, which this file already documents as containing a real Postgres Arm B-H run; re-run in isolation it completed cleanly in 1:39:43. Killing it mid-fixture skipped that fixture's cleanup, which left opt-out state behind and produced a LedgerConflictError on the next attempt — a problem created by the intervention, not found by it. State self-healed on the following run. The lesson recorded for later phases: on this host, judge a long-running suite by log progress and database activity, not by a CPU counter that cannot be trusted.

Separately observed and pre-existing, not caused by Phase 8: running tests/audit/** deletes the four residual public.agents and public.capability_scopes rows, because those fixtures use search_path TO <schema>, public and agents is not duplicated in their isolated schema, so their teardown reaches the real table; the same mechanism removed the 2099-01-01 budget_windows fixture row. Both were already documented as inert residue nothing depends on. No Phase 8 code writes to public at all — every public.-qualified reference under sampark/demo/ and ui/ is a read-only SELECT or a docstring.

Phase 4 protection: git diff aa87123aafdc9d812f5a01c04766c60b9198a2ce HEAD over sampark/allocator/constants.py, sampark/allocator/calibrated.py, sampark/budget/issuance.py, sampark/policy/types.py, sampark/policy/hard and sampark/policy/soft is empty. The only diffs anywhere under sampark/policy/ are the Phase 7 compiler and compiled packages, which are sanctioned additions rather than modifications to protected files. results/gate_headline.json is unchanged and python -m sim.gate still reports mean A 89,387.38 paise/contact, mean B 156,957.37 paise/contact, uplift range [1.7114, 1.8822], gate PASS. python -m sampark.audit.verify reports VALID: True, 560 events, genesis_ok: True, linkage_ok: True.

Full regression: 919 passed, 3 skipped, run in two segments because one directory dominates the wall clock — 901 passed and 3 skipped in 5m53s for everything except tests/sim_arm_b_holdout, and 18 passed in 1:39:43 for that directory alone. The skips are the pre-existing redis skips in tests/budget/test_precheck.py. The baseline before this phase was 782 collected, so Phase 8 adds 140 tests with no regressions.

Not done, deliberately: no LLM-rendered explanation endpoint (spec §8.10). ANTHROPIC_API_KEY is present in .env but empty — verified directly this session, which is a slightly different finding from the Phase 7 entry above, which recorded it as absent — so no live call was made and none is claimed. The deterministic format_explanation ships instead, and the explain endpoint returns the raw events the sentence was derived from so it can be checked against the record rather than trusted. No authentication on the demo endpoint; it binds to 127.0.0.1 and its safety rests on the structural schema isolation above, not on access control. No recovery-outcome modelling inside the demo — Phase 8 is a decision-trace demo, not an evidence run, so grants settle at their reserved ceiling and the Arm A/B economics remain sim/'s job. No new results/*.json evidence matrix was produced or needed.

Cold-viewer criterion, owner validation still outstanding. The repository-level validation that could honestly be done was done: a harness applying ui/static/app.js's own classification logic to a live run confirmed that all seven comprehension questions are answerable from what the UI renders — what happened, what was denied, why, what rolled back and recovered, what became of the rogue agent, what happened when the model was killed, and that every displayed fact carries its event_id, prev_hash and recomputed hash and chains. That validation exposed one genuine clarity defect and it was fixed additively: "compliance held" was not visible anywhere, even though spec §12.3 calls the recovery-drops-compliance-does-not distinction the whole design philosophy, so three audit-derived compliance tiles were added (quiet-hour violations, contact-cap breaches, scope violations by honest agents), computed purely from fields already on the streamed events and rendered red if ever non-zero. All three read 0 on a real run. What remains is the part no harness can supply: showing it to a person who has not heard the pitch and checking they can say what got denied and why.

  Phase 9 — Evidence run, sensitivity analysis and final documentation
Exit criterion (spec §18.1): "Every cell filled including unfavourable ones; a stranger can run it from the README alone." The first clause is met by executable evidence and the second by a rewritten README front matter carrying a quickstart that reproduces the headline from committed files in seconds. The Phase 8 cold-viewer judgement remains the one criterion no automation can supply and is still open; see the last paragraph.

The sweep spec §11 asks for turned out far cheaper than budgeted, because of a property of the runner nobody had written down. Under world v1 no realized outcome ever feeds back into a decision: all four agents build their complete action lists before the window loop starts, carried_forward is a function of the DECISION rather than the outcome, environment.observe is called only after the grant is already issued, and nothing anywhere reads outcome.recovered. Scaling BETA_FATIGUE is therefore exactly equivalent to scaling every customer's fatigue_hazard, and it touches no RNG stream — the same draws happen in the same order, only the threshold they are compared against moves. So varying either coefficient changes which contacts succeed and never which contacts happen, and every admission, ranking, grant, deferral, denial and prev_hash is invariant across the whole grid. That makes the sweep a pure re-observation needing no allocator re-run, no SERIALIZABLE issuance and no PostgreSQL at all: 88.9 minutes instead of the roughly twelve hours a Postgres-backed grid would have cost on a host with 12.8GB free, which is the same low-disk condition that crashed Docker in Phase 6. The property is asserted directly in tests/sim_sensitivity/test_decision_invariance.py, with a negative control proving the parameter is genuinely wired to something rather than passing vacuously.

Precommitment came first, deliberately and as its own commit. results/phase9_precommitment.json — the two dimensions, their grids, the anchor, the primary metric, six predictions and five falsifiers — was committed as eabdbd1 before sim/sensitivity.py existed and before any result was observed, mirroring the Decision-17 mechanism from Phase 7. A prediction committed after the results are known is not a prediction, so the two commits must not be squashed: the separation is the evidence. tests/sim_sensitivity/test_precommitment_binding.py asserts the code's grid still equals that committed file, so the grid cannot drift once results are in.

The sweep ran the full precommitted grid at the owner's explicit instruction: 50 of 50 points, BETA_FATIGUE over seven values and BETA_INCENTIVE over three, each across the five precommitted seeds, no failures, no retries, no tracebacks, and 10 of 10 anchor checks reproducing the committed Phase 4 evidence on eight fields apiece. All six predictions passed. Mean uplift rises monotonically from 1.5513x at BETA_FATIGUE 0.0 to 1.9282x at 2.0.

The result worth arguing about is the one that goes against the pitch. At BETA_FATIGUE 0.0 the cross-agent fatigue externality is switched off completely, and Arm B still beats Arm A by 1.5513x — roughly 73% of the advantage measured at the frozen value. Spec §8.6 calls that fatigue term "the whole thesis expressed as arithmetic." The measurement does not support it as the primary driver: selection, meaning ranking by expected net and declining low-value contacts, is doing most of the work, and fatigue supplies the remainder and grows in importance as it worsens. This is consistent with the committed fifo_under_cap ablation, where caps and hard policy without expected-value ranking buy only 1.06 to 1.14x. The prediction was right in direction and passed its precommitted band; it was wrong about magnitude, and saying so costs nothing and buys credibility. It is stated in README.md, ARCHITECTURE.md and DISCLAIMER.md rather than left in a JSON file for a reviewer to find.

The honest losing condition turned out to sit on a different axis than expected. There is no crossing point on rupees-per-contact anywhere in the tested range — mediation never stops winning on efficiency. It loses on total revenue at every one of the 50 points, with B over A running from 0.7996 to 0.9938. So "where SAMPARK stops winning," which spec §11 asks to be published, is answered on the revenue axis rather than the efficiency one. The trade improves monotonically as fatigue worsens: at BETA_FATIGUE 2.0 Arm B recovers 99.38% of Arm A's revenue on roughly half the contacts, which is the strongest form of the argument the project can honestly make — mediation is most valuable exactly where customer attention is most fragile.

The A/B/H table is built by re-reading committed evidence and re-runs no experiment; regenerating Phase 4/6/7 results would have consumed hours to produce identical bytes. It refuses cross-family deltas structurally, because Arm A under world v1 has no natural-recovery accounting and is not commensurable with Arm A-H under world v2. Phase 9 extended the Phase 7 holdout-versus-Arm-H check from one cell to all ten seed-by-fraction cells, and ground truth falls inside the holdout's 95% Wilson interval in 10 of 10. The Wilson implementation reproduces the committed Phase 7 interval bit-for-bit, which is what makes the extension trustworthy. One number in that table is the most misreadable in the repository and is labelled accordingly: Arm B-H's natural-recovery figure covers 10,733 uncontacted items at seed 42, which is the 1,962-item randomized holdout plus 8,771 allocator-declined items, and the declined ones were selected on low expected value by the allocator itself, so that pool's rate is biased low by the allocator's own skill and is not a baseline.

What broke, and what we did about it. The Wilson interval returned a lower bound of -2.8e-17 at zero successes — a negative probability, produced by floating-point cancellation when the two terms coincide at the boundary. Its own invariant test caught it before any result was published, which is the argument for writing tests that assert a property rather than the implementation's own output. Clamping to [0,1] corrects float error without moving any value strictly inside the interval, and the bit-for-bit reproduction of the committed Phase 7 interval still passes. Second, and self-inflicted: the first full regression run was launched without sourcing .env, because shell state does not persist between tool invocations, so every postgres-marked test skipped and the ui fixtures erred — 867 passed, 109 skipped, 42 errors. The run was discarded as invalid rather than reported, and re-run with the environment loaded. This is the same class as the Phase 6 .venv mistake, and the same lesson: on this host the environment must be established inside the same invocation as the command that needs it. Nothing was committed from either failure.

Scope reductions: none. The owner authorized full scope and the complete precommitted 50-point grid was executed.

Not done, deliberately, each recorded rather than quietly skipped. Contact-cap sensitivity was not tested, and it is the most economically interesting knob in the system: CONTACT_CAP_24H and CONTACT_CAP_7D are module-scope imports inside sampark/policy/hard/contact_cap.py and sampark/budget/issuance.py, both protected, with no override parameter anywhere, so sweeping them would require editing a protected file. Naming that exclusion is more honest than widening the protection to get a nicer chart. build_scorer()'s all-or-nothing model gate was left untouched even though the fatigue-hazard model is available on this dataset and the uplift model is not, because loosening an availability gate after observing which half passed is result-driven tuning; the consequence is that the measured model contribution stays exactly 0.00% and the row stays in the table. The live English-to-IR LLM step remains unexercised: ANTHROPIC_API_KEY is present in .env but empty, sampark/policy/compiler/llm.py fails loudly rather than fabricating a response, and the committed compiler fidelity number measures the deterministic parse-and-validate pipeline against hand-authored golden IRs, not a model. p99 grant decision latency is reported as NOT MEASURED because no latency instrumentation exists anywhere; an in-memory number would exclude the SERIALIZABLE round-trip that dominates real decision cost, and presenting it as production latency would be worse than a blank cell.

Phase 4 protection: git diff aa87123aafdc9d812f5a01c04766c60b9198a2ce HEAD over sampark/allocator/constants.py, sampark/allocator/calibrated.py, sampark/budget/issuance.py, sampark/policy/types.py, sampark/policy/hard and sampark/policy/soft is empty. Phase 9 went further and modified nothing under sampark/ at all — its entire analytical layer is sim/sensitivity.py, sim/abh_table.py and tests, with the only behavioural change being keyword-only beta_fatigue and beta_incentive parameters on three sim/ functions, every one defaulting to the frozen module constant so that every pre-Phase-9 call site is byte-identical. That is asserted as a test rather than checked by hand. results/gate_headline.json is unchanged and python -m sim.gate still reports mean A 89,387.38 paise/contact, mean B 156,957.37 paise/contact, uplift range [1.7114, 1.8822], gate PASS. python -m sampark.audit.verify reports VALID: True, 560 events, genesis_ok: True, linkage_ok: True, head hash bf4ad0d0...b18244 — unchanged before and after a three-and-a-half-hour suite and the entire sweep.

Full regression: 1015 passed, 3 skipped, exit 0, in 3:25:34 with the environment correctly loaded. The skips are the pre-existing redis skips in tests/budget/test_precheck.py. The baseline before this phase was 919 passed, so Phase 9 adds 96 tests with no regressions. During the roughly fifty-minute silent stretch in tests/sim_arm_b_holdout the run was judged alive by catching a live UPDATE on customer_margin_windows in pg_stat_activity and watching the grants table climb through 1,146, 4,926 and 7,233 before its cleanup returned it to zero — log progress and database activity, exactly as the Phase 8 entry above concluded after a healthy suite was killed on a bogus CPU reading.

Owner decisions that remain OPEN and are not closed by this entry. The Phase 8 cold-viewer criterion still requires showing the running demo to a person who has not heard the pitch; no such session has taken place and none is claimed. Whether to ship a commented policies/activated.yaml is undecided — Phase 9 added policies/README.md, which maps the eleven existing hard rules to their modules, citations and tests and authored no rule, and left activated.yaml absent, which is the state the protected evidence requires. CI runtime is unaddressed: the workflow now genuinely runs the multi-hour postgres-marked tests on every push, and no marker split was introduced because doing so without an owner decision risks reading as hiding a failure. Whether to supply an API key for a live compiler run is undecided. The recommendation on build_scorer() is to leave the gate as it is.
