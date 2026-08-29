# CLAUDE.md — engineering instructions for SAMPARK

Persistent instruction set for every Claude Code session in this repository.
Read this file at the start of every phase, before touching anything.

The authoritative design document is `SAMPARK-razorpay-buildathon-spec.md`.
This file governs *how* to work. The spec governs *what* to build. Where this
file and the spec disagree, stop and ask — do not silently pick one.

---

## 1. Project identity

SAMPARK (संपर्क — "contact") is a **mediation layer for revenue-recovery
agents**. It is a control plane, not a recovery agent.

The core thesis, which every design choice must serve:

> **Authorization decides whether an agent *may* act.**
> **Authorization alone must not decide which of several equally-authorized agents *should* act.**

Permission systems answer one request at a time and are stateless with respect
to other agents — that is what makes them fast and composable, and it is exactly
what makes them blind here. Four agents contacting the same customer can each
hold valid consent, an approved scope, an in-policy discount and a registered
template. A perfect authorization layer permits all four. The harm is emergent.

SAMPARK therefore combines nine things, and the whole argument depends on them
being one system rather than nine features:

1. cryptographic agent identity (Ed25519, detached signatures)
2. capability scopes (declared, signed, enforced before the allocator runs)
3. a unified at-risk ledger (one human is one row, regardless of how many
   products noticed them)
4. customer/contact budgets (attention as scarce, depletable inventory)
5. merchant-margin budgets (aggregate incentive authority across all agents)
6. hard policy constraints (regulatory filters, never traded off)
7. interlocks (mutually exclusive states across agents)
8. allocation (rank, allocate, defer, downgrade — not permit/deny)
9. auditability (append-only, hash-chained, agent-signed)

**Track 03 framing.** The submission is judged on problem taste, build quality,
AI judgment, and failure recovery. Three of those four are about execution and
honesty. The repo is the interview.

---

## 2. Architecture constraints

Use the specified stack. Do not substitute.

| Layer | Locked choice |
|---|---|
| Language | **Python 3.11** (never 3.12/3.14 — enforced by `tests/test_environment.py`) |
| API | **FastAPI** |
| Contracts | **Pydantic** |
| Ledger | **PostgreSQL 16**, `SERIALIZABLE` on grant issuance |
| Budgets/counters | **Redis** (rolling windows, TTL'd reservations, locks) |
| Agent identity | **Ed25519** detached signatures; scopes as signed JSON claims |
| Payments | **Razorpay Python SDK** + **Razorpay MCP Server** (`rzp_test_` only) |
| Models | **scikit-learn / LightGBM** where the spec specifies (uplift T-learner, fatigue hazard, isotonic calibration) |
| Infra | **Docker Compose** |
| Tests | **pytest** |
| CI | **GitHub Actions** |
| Tracing | **OpenTelemetry** |
| Logging | **structlog** (JSON) |
| Demo surface | **FastAPI + SSE + minimal UI** (vanilla JS; zero framework debt) |

**Ask before introducing any of these:** a major framework, another database, a
message queue or broker, an orchestration platform (Celery/Airflow/Temporal/
Kubernetes), an authentication system (OAuth/OIDC/Auth0/Keycloak), a frontend
framework (React/Vue/Svelte/Next), or an LLM framework (LangChain/LlamaIndex/
CrewAI/AutoGen).

Deliberate non-choices already made in the spec — do not "improve" them without
asking:

- **Ed25519 + a Postgres registry, not a full OIDC/OAuth deployment.** Scoped
  down on purpose (§15.9).
- **Budgeted greedy allocator, not CP-SAT.** A heuristic with a published
  optimality gap beats a solver nobody can defend under questioning. Measure the
  gap offline and report it; do not swap in a solver.
- **A YAML lookup table for root-cause classification, not a model.** This was
  an LLM call and it was cut. Cutting it is the argument.

---

## 3. Human-owned artifacts

These are **owned by the project owner**. Do not generate them, and do not
change them silently:

1. the PostgreSQL schema
2. Pydantic / domain contracts
3. policy rules in `policies/` (each needs a citable regulation behind it)
4. `DECISIONS.md`
5. the `SERIALIZABLE` grant-issuance transaction design
6. the concurrency test design
7. major architectural decisions

Claude **may** implement code behind an approved contract.

Claude **must not** invent a domain model and then proceed as if it were
approved. If a contract is missing, say so and stop. A plausible-looking
invented schema is worse than no schema, because it gets built on.

The test for whether something is human-owned: *would a panel interrogate this
artifact and expect the owner to defend every field?* If yes, it is human-owned.

---

## 4. Development discipline

Every phase, in order:

1. Understand the phase goal.
2. Identify the phase **exit criterion**.
3. Establish or confirm the contracts.
4. Write tests before implementation where practical.
5. Implement the minimum required functionality.
6. Run the tests. Actually run them.
7. Inspect the diff.
8. Review for architectural drift.
9. Only then commit — and only when explicitly instructed (§9).

**A phase is not complete because code exists.** A phase is complete when its
stated exit criterion has been *demonstrated*. "Implemented but unverified" is a
distinct status and must be reported as such.

---

## 5. Phase boundaries

Treat each phase as a **separate Claude Code context**. One context sprawling
across several phases produces confident, subtly inconsistent code that nobody
can explain later.

At the start of every phase:

- read this file
- read the relevant specification sections
- inspect the current repository state (do not assume it matches memory)
- identify and state the phase exit criterion

Do not accumulate unrelated work across phases in one context. If a task belongs
to a later phase, name it and leave it.

**Phase caps from the spec, which exist because these phases expand:**
Phase 3 (Agent Registry) is capped at one day. Phase 8 (demo surface) at two.
Phase 4 is the **hard gate** — if mediated allocation is not beating unmediated
on heuristics alone, the thesis is wrong and the §20 fallback applies.

---

## 6. Code generation rules

**Prefer:** small modules · explicit interfaces · deterministic behavior · typed
contracts · dependency injection where it genuinely helps · testable pure
functions · simple implementations · explainable algorithms.

**Avoid:** speculative abstractions · premature generalization · unnecessary
dependencies · magic configuration · hidden side effects · large generated files
· any code the project owner cannot explain.

The last one is the real filter. **If a generated file is longer than can be
read carefully, the task was too big — split it.** A commit that cannot be
explained under questioning is worse than a missing feature.

Two structural rules the spec makes load-bearing:

- **`policy/hard/` and `policy/soft/` stay separate packages.** Hard constraints
  are filters that eliminate candidates *before* scoring; soft factors are terms
  in the objective. No expected-value calculation may ever buy its way past a
  hard constraint. This separation *is* the compliance argument, so it must be
  visible in the code structure, not just honored at runtime.
- **The trace-integrity rule (§12.1, non-negotiable).** The UI renders the audit
  log and **nothing else**. No `emit_demo_event()`, no parallel telemetry socket,
  no component reporting its own progress to the frontend. If a stage does not
  write a durable, hash-chained audit event, it does not appear on screen. An
  instrumented visualization is a second code path, which lets the demo look
  correct while the system is broken.

Determinism is a requirement, not a preference: same seed, same trace, every
run. The simulator and the replay must be reproducible across runs.

---

## 7. LLM boundary

**LLMs must not make critical deterministic decisions.** A model that can
hallucinate must never sit on the path to a money action.

Never use an LLM for:

- signature verification
- capability authorization / scope checks
- compliance filtering
- budget arithmetic
- contact counting
- margin calculations
- allocation decisions
- attribution mathematics
- root-cause classification (deterministic YAML lookup; a model here was
  explicitly cut and the cut is part of the argument)

**The only two approved LLM jobs:**

1. **Policy compilation** — English → executable rule objects **plus a generated
   pytest case per rule**. The tests are committed, and rules activate only if
   their tests pass. The output is a *checked artifact*, not an unverifiable
   prompt.
2. **Audit-log explanation** — decision log → human sentence. Read-only,
   generated **from the log**, never from the model's memory, and structurally
   incapable of influencing an outcome.

If an implementation seems to want a third LLM use: **stop and ask.** Do not add
it and mention it afterwards.

---

## 8. Security

Treat secrets as secrets.

Never:

- print or echo API keys
- commit `.env` (it is gitignored; `.env.example` is the tracked template and
  holds names with empty values only)
- hard-code credentials
- expose or commit private keys (Ed25519 agent private keys never enter the repo)
- log authorization headers, signatures-with-keys, or raw customer PII
- **fabricate a successful external API call**

Razorpay integration uses **test credentials only** (`rzp_test_`). Never live
keys, never a live payment path.

Channel adapters for WhatsApp/SMS/voice are **mocked on purpose** — you cannot
lawfully contact real numbers with synthetic consent. Log the exact payload that
*would* have been sent and be loud in the README about why it is mocked. Do not
quietly wire a real provider.

---

## 9. Git

**Never commit unless explicitly instructed.**

Before any commit:

- show `git status`
- inspect the diff in full
- run the relevant tests
- identify which changes were generated vs. hand-written
- identify any architectural change hiding in the diff

Never rewrite history, force-push, or run destructive operations
(`reset --hard`, `checkout --`, `clean -fd`) unless explicitly instructed.

Commit in **real increments** — one per meaningful unit of work, not one per
session. Fifteen days of genuine increments is a signal; three giant commits is
a different signal.

Repo-specific note: `core.ignorecase=true` and `core.autocrlf=true` here.
Case-only renames need `git mv -f`, and unstaging one can silently discard the
rename record.

---

## 10. MCP

MCP tools are **external development and integration capabilities**. They are
not where business logic lives.

- Do not move critical business logic into an MCP tool.
- Do not install or configure new MCP servers unless explicitly requested or
  clearly necessary for the current phase.

The Razorpay MCP Server is in the stack as a deliberate fluency signal for
payment operations — not as a substitute for the mediation logic, which stays in
this codebase where it can be tested and audited.

---

## 11. Multi-agent development

When Claude Code subagents are used, use **specialized roles**, not uncontrolled
parallel implementation. **The main context remains the integrator.**

Subagents may: inspect · implement within an approved contract · write tests ·
review security · review database behavior · review audit integrity.

Subagents **must not** independently change architecture.

Do not run multiple agents on the same files simultaneously unless the task has
been explicitly partitioned.

---

## 12. Testing

Tests must cover: normal behavior · invalid input · boundary conditions ·
failure behavior · concurrency where relevant · security invariants where
relevant.

**Tests should prove requirements, not increase coverage.** A test that asserts
a requirement the panel would ask about is worth more than ten that exercise
getters.

Tests the spec names explicitly:

- **`test_concurrent_grant_issuance.py`** — the Phase 4 `SERIALIZABLE` grant
  issuance concurrency test. **This is the most important test in the project.**
  Two agents racing for the last contact slot is the central correctness
  problem, and it must be a real database transaction, not application-level
  logic. Both the transaction and this test are human-owned (§3).
- **`test_scope_enforcement.py`** — an out-of-scope request must be rejected on
  signature-verified scope alone, with **no allocator involvement**.
- **`test_ui_renders_only_audit_events.py`** — enforces the trace-integrity rule
  rather than merely asserting it.

Never claim a test passed unless it was actually executed. If tests could not be
run, say so and say why.

---

## 13. Decision log

**Do not write `DECISIONS.md`.** It is maintained by the project owner, by hand.
An AI-written build log reads exactly like an AI-written build log, and it
defeats the purpose of the artifact — which is the *failure recovery* judging
axis, phrased in the past tense: what broke, and what you did about it.

When something happens that should be recorded, report:

- **what** decision was made
- **why** it matters
- **what alternatives were rejected**

Then stop. The owner writes the entry.

Worth flagging for the log: anything that broke and how it was found; where
generated code was wrong and the test that caught it; any deviation from the
spec; any place a tool was used and what it got wrong.

---

## 14. Communication

When reporting work:

- **distinguish implemented behavior from planned behavior** — never describe
  intended behavior in the past tense
- identify assumptions
- identify deviations from the specification
- identify unresolved risks
- **never claim a test passed unless it was actually executed**
- **never claim an external service worked unless it was actually verified**
- report unfavorable results plainly — a measured result that undercuts the
  thesis is data, not a failure to hide

When uncertain about an architectural decision: **stop and ask.** Do not invent
one and present it as settled.

---

## 15. Current phase

**Phases 0–6 implemented. Phase 6 (Intelligence layer) is CLOSED as of this
entry. Do not begin Phase 7 work until the owner reviews this closure.**

Phases 0 through 5 have each demonstrated their spec §18.1 exit criterion
(payment link + CI; 20k reproducible risk items; Arm A end-to-end metrics;
scope-only rejection with no allocator involvement; the Phase 4 hard gate
PASS; and Phase 5's chain/explainability verified live against real
PostgreSQL).

**Phase 6 exit criterion (spec §18.1): "Models beat the heuristic — or are
honestly reported as not doing so, with the ablation committed." Result:
the honest-non-improvement branch — demonstrated, not the models-win branch.**

**Phase 6 evidence (recorded — do not alter without an explicit, recorded
reason):**
- `sampark/allocator/scorer.py` — model-agnostic scorer seam. `constants_commit_sha`
  in every Phase 6 result file is `78f5850c24969bfbbf0afde2b88fc9a8e3a4dcfc` (HEAD at
  run time); `sampark/allocator/constants.py` is byte-identical between the protected
  Phase 4 commit `aa87123` and HEAD (`git diff aa87123 HEAD -- sampark/allocator/constants.py`
  is empty), so the comparison below is against unchanged constants.
- `phase6_heuristic` ablation reproduces the Phase 4 headline **exactly** (bit-for-bit
  on `total_contacts`, `total_recoveries`, `recovered_amount_paise`,
  `recovered_amount_per_contact_paise`, `incentive_spend_paise`, `decisions_logged`)
  across all five precommitted seeds 7, 42, 101, 2024, 31337 — this is the regression
  proof for the new scorer seam.
- `sampark/models/uplift.py` (T-learner) and `fatigue_hazard.py`: implemented, but both
  report `available=False` on this dataset, identically (byte-identical reason string)
  across all five seeds:
  - uplift: no untreated/control population exists for any risk source — every eligible
    RiskItem is contacted by its matching Phase 2 agent exactly once (max uncontacted
    fraction observed: 0.0000); a T-learner needs real treated/control variation, which
    requires a holdout arm that does not exist until Phase 7 (spec §8.9).
  - fatigue hazard: `agents.types.ContactOutcome` has no opt-out-related field, and
    `sampark.contracts.ContactState.optouts_by_channel` exists on the contract but
    `sim/ledger.py` constructs every `ContactState` with `optouts_by_channel={}` and no
    generator code ever writes to it — no real opt-out signal exists yet.
  - `phase6_model` therefore deterministically falls back to `HeuristicScorer` and also
    reproduces the Phase 4 headline exactly, for the same honest reason, on all five seeds.
  - No unsupported ML improvement is claimed anywhere in this evidence.
- `sim/optimality_gap.py` — exact per-window multiple-choice-knapsack DP (no CP-SAT;
  CLAUDE.md §2), tested against brute force (`tests/sim_optimality_gap/`, 8/8 passing).
  Measured (seed 42, top-5 windows by admitted ceiling): mean gap ratio ≈0.9996 /
  worst-case ≈0.9994 at headline merchant-margin capacity
  (`results/phase6_optimality_gap_headline_seed42.json`); mean ≈0.9994 / worst-case
  ≈0.9985 at half that capacity, an artificial stress test since headline capacity
  rarely binds (`results/phase6_optimality_gap_merchant_margin_half_seed42.json`).
  I.e. the shipped greedy allocator is empirically within ~0.05–0.15% of the measured
  per-window optimum on this data — a lower bound on the true achievable optimum, since
  the DP does not itself explore incentive downgrades (module docstring for the exact
  caveats: per-window not whole-horizon, no downgrade search inside the DP).
- Full regression suite: 588 passed, 3 skipped (skips are `tests/budget/test_precheck.py`
  — redis not pip-installed as a project dependency, pre-existing and unrelated to
  Phase 6). `python -m sampark.audit.verify` re-run after all Phase 6 evidence
  collection: `VALID: True`, 560 events, `genesis_ok: True`, `linkage_ok: True` — the
  audit chain was not disturbed by Phase 6 work.
- Nothing from this phase is git-committed (§9); `git status` still shows the same
  modified/untracked fileset the Phase 6 work produced.

**Phase 6 failure-recovery incident (for the owner's `DECISIONS.md`, not written here
per §13):** mid-evidence-run, the host C: drive filled to 100% free space, which crashed
Docker Desktop and severed the live Postgres connection the run was using.
`sim/arm_b.py`'s `_run_arm_b_postgres` resets its own transactional rows
(`grant_requests`, `grants`, `contact_slot_claims`, `customer_margin_windows`,
`budget_windows`) in a `finally` block on every run, but that block needs a live
connection — it could not run when the connection was already dead, leaving 399
orphaned rows. The next rerun's registry cleanup (`run_phase6_evidence.sh`, which only
cleared `agents`/`capability_scopes`) then failed on an FK constraint
(`grant_requests_agent_id_fkey`) because of those orphans. Detected by inspecting the
FK error and cross-referencing it against `_cleanup_postgres_run`'s own documented-safe
table list; fixed by extending the script's `cleanup_registry` to also clear those same
tables/columns, matching the scope that function already documents as safe (never
customers, risk_items, or agent identity). Two further false starts on the same rerun,
both self-inflicted: forgetting to activate `.venv` (system Python 3.14 resolved instead
of the project's 3.11 — the exact failure mode `tests/test_environment.py` documents),
and a fresh background shell not having `.env`'s Postgres vars loaded.

**Phase 6 residue check (post-evidence-run, before this closure entry):** all
Phase-6-relevant transactional tables (`grant_requests`, `grants`, `contact_slot_claims`,
`customer_margin_windows`, `agents`, `capability_scopes`) at 0 rows; one unrelated
`budget_windows` row (`window_id 2099-01-01`, `merchant-sim`, zero reserved/spent) found —
an inert test-fixture artifact from the general test suite, not from any Phase 6 evidence
run (Phase 6 runs use Sept 2025 windows), left untouched since there is no established
cleanup procedure that targets it and deleting it was not required to correct anything.
No new Postgres schemas found (`\dn` shows only `public`). `customers`/`risk_items`
identity tables unchanged in row count by this work.

**Phase 5 core (audit chain, canonicalization, append-only enforcement,
emitter, explainability, export, U-1 live-applied, U-2 real integration,
U-3 score threading, T-26 determinism) is implemented and independently
verified against real PostgreSQL** — `python -m sampark.audit.verify`
reports `VALID: True` against the live store.

**Still open, pre-existing and independent of Phase 6 (carried forward, in order of
what blocks the most — Phase 6 proceeded without these being closed; a Phase 7 session
should still weigh them):**

1. `sampark/schema.sql` (human-owned — CLAUDE.md §3 item 1) does not yet
   contain U-1's DDL (`seq`, `UNIQUE(prev_hash)`, the three append-only
   triggers). U-1 has been owner-applied to the live database and
   verified there; a *fresh checkout* cannot run Phase 5 until this file
   is updated by the owner. `sampark/audit/schema_proposal.sql` holds the
   exact DDL, unmodified, as the durable record of what was applied.
2. CI has no PostgreSQL service (U-7, approved, not yet applied — owner
   action per Design Lock §17.3, `.github/workflows/**` is human-owned).
   Without it, 64 tests silently skip in CI, including
   `tests/test_concurrent_grant_issuance.py` (§12's most important test)
   and Phase 5's own T-18/T-26 exit-criterion tests. The exact proposed
   diff is at `CI_POSTGRES_SERVICE_PROPOSAL.md` (repo root); the workflow
   file itself has not been touched.
3. U-8 (registry writes -> audit events, approved) is now wired for
   `agent.registered` (`sim/arm_b.py`'s registry setup ->
   `sampark.audit.sink.AuditSink.record_agent_registered`, `audit_sink=None`
   by default, byte-identical when omitted). `agent.struck`/`agent.revoked`
   remain library-only (`sampark/audit/emit.py`'s emitter functions exist
   and are tested) because **no code path anywhere calls
   `sampark.registry.strikes.record_scope_denial` in production** — spec
   §12.3's two-stage rogue-agent demo strikes on stage-two *hard-policy*
   denials (rate ceiling / quiet hours), a mechanism that does not exist
   in the codebase yet and is Phase 8 demo-surface territory, not a
   Phase 0–5 gap. Do not wire a scope-denial-triggered strike into
   `sampark/mediation/service.py` without an explicit owner decision —
   the real spec-described trigger is a different mechanism.
4. §18.1–§18.6 (Design Lock) and U-1…U-8 owner confirmations are not yet
   recorded in `DECISIONS.md` — Claude must not write that file (§13);
   the owner does.
5. `results/*.json` (the entire Phase 4 evidence record) is gitignored —
   durable only in this working tree. Whether to commit it conflicts with
   the Phase 2 "generator committed, output not" convention; needs an
   owner ruling before Phase 6 writes into the same directory.

Update this section at each phase boundary.
