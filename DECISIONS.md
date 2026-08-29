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

