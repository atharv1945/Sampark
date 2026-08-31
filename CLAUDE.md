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

**Phase status, as of the Phase 7 owner-closure session:**

| Phase | Status |
|---|---|
| 0 — Foundations & Contracts | CLOSED |
| 1 — Data Spine | CLOSED |
| 2 — Arm A baseline | CLOSED |
| 3 — Agent Registry | CLOSED |
| 4 — Mediation core (hard gate) | CLOSED |
| 5 — Audit chain / explainability | CLOSED |
| 6 — Intelligence layer | CLOSED (evidence committed `b8f0efc`) |
| 7 — Attribution & policy compiler | **ENGINEERING CLOSED.** Evidence preserved at the documented seed-42 scope. Design decisions recorded in `DECISIONS.md`. See the three-way distinction below — this is not the same claim as "nothing remains." |
| 8 — Demo surface | **CLOSED.** FastAPI + SSE + vanilla-JS one-screen trace, deterministic ~40s replay, all three §12.3 failures, all seven §12.4 chaos controls. Implemented, tested, demonstrated live, decisions recorded in `DECISIONS.md`, and committed. One owner validation item remains open and is named below — it is not an engineering gap. |
| 9 — Sensitivity sweep / final A-B-H table / `ARCHITECTURE.md` / `DISCLAIMER.md` | **ENGINEERING CLOSED.** 50-point precommitted sensitivity sweep (all 6 predictions PASS), canonical A/B/H table, `ARCHITECTURE.md`, `DISCLAIMER.md`, README front matter, `policies/README.md`. Phase 4 protection intact; `sampark/` untouched. One owner validation item (cold-viewer) still open from Phase 8. |

**PHASES 0–9 ENGINEERING CLOSED. ONE OWNER VALIDATION ITEM REMAINS.**

**Commit-hash note.** The Phase 6-8 commits were rewritten by an owner rebase
after the Phase 8 session. Hashes cited in the historical narratives below
(`510e5fa`, `6e45855`, `d35540e`/`3c2ba16`) were accurate when written but are
no longer reachable from `main`; a fresh clone cannot resolve them. Their
current equivalents are `b8f0efc` (Phase 6 evidence), `14905e7` (Phase 7) and
`9849126` (Phase 8). The historical text is left as written rather than
silently rewritten; this note is the mapping. `aa87123` is unaffected and
remains the Phase 4 protected baseline.

The single item still outstanding anywhere in Phases 0-9 is Phase 8's
cold-viewer criterion (spec §18.1: "someone who hasn't heard the pitch can
watch it and tell you what got denied and why"). That is an OWNER VALIDATION
— it requires showing the running demo to a person, which no test can do.
Everything it depends on is implemented and demonstrated; see the Phase 8
block at the end of this section for exactly what was and was not verified.

Phase 7 closure is genuinely three separate claims, kept distinct rather than
collapsed into one "done":

- **TECHNICAL** — Phase 7 is implemented and verified: every component
  (holdout, world-v2 natural recovery, Arm A-H/Arm H/Arm B-H, attribution
  baseline+ledger, uplift/fatigue-hazard holdout paths, policy compiler,
  the three new audit event types wired into real operations) exists, is
  tested, and its behavior has been checked against real PostgreSQL —
  fresh in the owner-closure session, not merely asserted from an earlier
  turn.
- **EVIDENCE** — Phase 7 evidence is preserved at the documented,
  explicitly scoped-down extent: single-seed (42) for the `phase7_*` Arm B
  ablations (not the full 5×3 matrix — see the Phase 7 status block
  below for why), no live LLM compile (no `ANTHROPIC_API_KEY`
  configured), everything else at full precommitted scope (5 seeds ×
  both holdout fractions for the model-availability findings).
- **PROCEDURAL** — Phase 7 design decisions are recorded in
  `DECISIONS.md`'s "Phase 7 — Attribution & policy compiler" entry, and
  the Phase 6 evidence + Phase 7 implementation are committed (see below).
  This closes the two items that were previously blocking Phase 8 for
  procedural, not engineering, reasons.

(Historical note, left as written: at the time of the Phase 7 closure session
Phase 8 had not yet begun. It has since been implemented, tested, demonstrated
and committed — see the Phase 8 block at the end of this section.)

**Correction to this section, made during the Phase 7 session that follows
(re-verified directly against the repository, not assumed from this file):**
open items 1, 2 and 4 below were already closed by owner action before this
session started — U-1's DDL is present in `sampark/schema.sql` (`seq`,
`UNIQUE(prev_hash)`, all three append-only triggers, confirmed by grep), CI's
`postgres:16` service is present in `.github/workflows/ci.yml`, and
`DECISIONS.md` carries Phase 0–6 entries. The line below stating "nothing
from this phase is git-committed" is also stale: Phase 6 is commit `fe45cef`
and the tree was clean at the start of the Phase 7 session. This section had
not been updated to reflect those closures; it is corrected here rather than
silently, per CLAUDE.md's own instruction to document inconsistencies found
between a report and the actual repository state.

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
- Phase 6 is committed as `fe45cef`. `results/*.json` was gitignored at that
  commit despite the exit criterion's "with the ablation committed" —
  closed in the Phase 7 owner-closure session as commit `510e5fa` (item 3
  below).

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

**Still open (re-verified at the start of the Phase 7 session; items 1, 2
and 4 from the original list of five were already CLOSED by owner action
and are removed from the open list below — see the correction note above):**

1. U-8 (registry writes -> audit events, approved) is now wired for
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
   the real spec-described trigger is a different mechanism. Also newly
   surfaced during Phase 7 reconciliation: `CapabilityScope.max_requests_per_hour`
   is declared, persisted, and enforced nowhere — the missing half of the
   same §12.3 stage-two demo. Both belong to Phase 8, not Phase 7.
2. **CLOSED in the Phase 7 owner-closure pass** (commit `510e5fa` for the
   evidence, a `DECISIONS.md` edit for the decisions): §18.1–§18.6 (Design
   Lock) and U-1…U-8 owner confirmations remain unresolved (no source
   document exists to confirm them against — see item 4), but the Phase 7
   design-lock decisions (holdout, natural recovery, Decision-17
   precommitment, attribution, uplift/fatigue-hazard availability, policy
   compiler, Phase 7 ablations, Phase 4 protection, regression, audit) are
   now recorded in `DECISIONS.md`'s "Phase 7 — Attribution & policy
   compiler" entry. Normally Claude must not write that file (§13, the
   owner does) — this was an explicit, one-time exception: the owner's own
   closure prompt stated "DECISIONS.md is now explicitly authorized to be
   updated" and supplied the exact facts the entry must preserve.
3. **CLOSED**: `results/*.json` is committed as `510e5fa`
   ("evidence(phase6): commit frozen intelligence-layer results") — 43
   result/gate files + the `.gitignore`/`.gitattributes` fix
   (`results/*.json text eol=lf`, so `core.autocrlf=true` cannot make the
   same evidence byte-different across platforms). `git ls-tree -r HEAD --
   results/` confirms 43 tracked files. Nothing was regenerated for this
   commit — the exact files the Phase 6 closure report was written
   against.
4. The "Design Lock" and "Phase 5A/5B" documents cited ~50 times across
   this codebase as authoritative (`sampark/schema.sql`,
   `sampark/allocator/*`, `sampark/policy/*`, `sampark/budget/*`,
   `sampark/audit/*`, `sim/*`, `README.md`, `DECISIONS.md`) are not
   present in the repository — only `PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md`
   and `CI_POSTGRES_SERVICE_PROPOSAL.md` survive as derived records. A
   reviewer cannot check any "Design Lock §N" claim against a source
   document. Not a Phase 7 blocker; flagged because Phase 7 adds more such
   citations to it.

---

**Phase 7 status (Attribution & policy compiler): IMPLEMENTED AND TESTED
as of this entry.** Spec §18.1's stated exit criterion — "compiled rules
pass their own generated tests before activating" — is demonstrated:
`tests/policy/compiler/test_generate.py` exercises both a correctly
compiled rule passing its generated test and a deliberately wrong one
failing it. See the session's own final report for the authoritative
implemented/tested/evidenced breakdown per component; this entry is a
summary, not a duplicate.

**What was built:** `sim/holdout.py` (deterministic customer-level
assignment), `sim/environment.py` world v2 (opt-out labels + natural
recovery, `world="v1"` default byte-identical to every pre-Phase-7
caller), `sim/arm_a_holdout.py` / `sim/arm_h.py` / `sim/arm_b.py`'s
`run_arm_b_holdout`, `sampark/models/{uplift,fatigue_hazard,artifact,scorer}.py`'s
holdout-aware paths (never modifying the Phase 6 zero-arg entry points),
`sampark/attribution/` (baseline/credit/store + schema PROPOSAL only —
`sampark/schema.sql` untouched), three new audit event types
(additive), and `sampark/policy/compiler/` + `sampark/policy/compiled/`
(the full deterministic English→IR→rule pipeline).

**Real evidence collected this session** (not merely "tests pass"):
holdout balance SMD ≈ -0.0074 across 4,913 real customers; holdout-vs-
Arm-H estimator validation (5.10% holdout estimate, 95% CI
[4.21%, 6.16%], Arm H ground truth 5.29% — **inside the interval**);
Decision-17 precommitted prediction (30,658,277 paise) vs realized
(31,947,441 paise, 4.2% error); 18,038 real attribution credits with the
arithmetic invariant holding for every one; fatigue-hazard model
**available** (the first Phase 6/7 model to clear its gate) across all
5 seeds × both fractions, hierarchical fallback proven to never return
the old anti-conservative silent zero; uplift **honestly unavailable**
across all 5 seeds × f∈{0.10,0.20,0.30,0.40} (structural — rare
`unknown`-root-cause buckets never clear the floor even at f=0.40); a
real ~108-minute Postgres-backed Arm B-H run (2 full simulated months,
SERIALIZABLE issuance) proving the mechanism, opt-out enforcement,
cleanup, and determinism, after which `python -m sampark.audit.verify`
and `python -m sim.gate` both reproduced their pre-Phase-7 values
exactly.

**Explicitly scoped down, not silently skipped** (see the final report
for the full list): the live English→IR LLM call was not exercised (no
`ANTHROPIC_API_KEY` configured this session — `sampark/policy/compiler/llm.py`
fails loudly rather than fabricating a response, CLAUDE.md §8);
`sampark.policy.compiled.composed_hard_rules()`
is not wired into `sampark/mediation/hard_filter.py` (a Phase-4-protected
file) since `policies/activated.yaml` has no real (LLM-compiled) rule to
exercise it with yet — `tests/policy/test_activation_empty_in_protected_evidence.py`
proves the byte-identical-to-`HARD_RULES` safety property this future
wiring will depend on; a full 5-seed × ablation Postgres-backed Arm B-H
sweep was not completed (~1-2 hours per run measured directly — Arm B-H
ran on the memory backend for all 5 seeds instead, with ONE real
Postgres run as mechanism/determinism proof).

**Phase 7 closure session (this entry, following the entries above —
re-verified directly against the repository, not assumed from memory):**

- `sim/arm_b_cli.py` **was** extended this session with three new
  official-evidence ablations (`phase7_heuristic`, `phase7_model`,
  `phase7_model_uplift`; also added to `sim/gate.py`'s filename map) —
  correcting the "not extended" line above, which was accurate when
  first written and is now stale. Each was run once against real
  PostgreSQL at seed 42 (the precommitted headline seed) and reproduces
  the frozen Phase 4 headline **exactly** (10299 contacts, 2691
  recoveries, 1,593,664,601 paise recovered, 154,739.74 paise/contact,
  18,363,386 paise incentive spend — bit-identical across `phase7_heuristic`,
  `phase7_model`, `phase7_model_uplift`, and the original frozen
  `arm_b_metrics_42.json`), each for a distinct honest reason
  (`phase7_heuristic`: same `HeuristicScorer` explicitly constructed;
  `phase7_model`/`phase7_model_uplift`: the committed Phase 7 model
  artifact's all-or-nothing availability gate falls back to
  `HeuristicScorer` because uplift is structurally unavailable on this
  dataset, same reasoning as `phase6_model`). **Scoped down deliberately**:
  the full 5-seed × 3-ablation matrix (15 runs) was not executed — one
  real run measured at 48 minutes wall-clock makes the full matrix
  ~12 hours, disproportionate given this regression-seam guarantee is a
  code-path property (identical scorer/fallback logic) that does not
  vary by seed, and given disk headroom was tight throughout this
  session (see below). This is a scope decision, not a gap silently
  left open.
- Added `tests/audit/test_world_v2_does_not_affect_audit_generation.py`
  (3 tests) proving world v2 cannot perturb audit-event generation or
  `prev_hash`: structurally, none of the 11 pre-Phase-7
  `sampark.audit.emit.event_for_*` functions accept a `world` parameter
  or reference any world-v2-only type; combined with the existing
  bit-identical-outcome tests (`tests/sim_environment/test_world_v2.py`,
  `tests/sim_arm_b_holdout/test_arm_b_holdout_memory.py`) this is a
  complete, direct proof, obtained without a duplicate Postgres run.
- **Final full regression, run fresh after every change in this
  session** (`python -m pytest -q`): **779 passed, 3 skipped** (skips
  are the pre-existing, unrelated `tests/budget/test_precheck.py` redis
  skips), exit 0, 7492s (2:04:52) wall-clock.
  `python -m pytest tests/test_concurrent_grant_issuance.py -v`: both
  tests pass standalone (the 50-way race and its negative control).
- `python -m sampark.audit.verify` after all of the above: `VALID: True`,
  560 events, `genesis_ok: True`, `linkage_ok: True` — unchanged from
  every earlier check this session; none of this session's Arm B CLI
  runs or the full test suite wrote to the real `public.audit_events`
  (Postgres tests use isolated schemas; `arm_b_cli.py` runs with no
  `audit_sink`).
- Phase 4 headline (`results/gate_headline.json`,
  `constants_commit_sha aa87123aafdc9d812f5a01c04766c60b9198a2ce`)
  re-confirmed unchanged: mean A 89387.38057, mean B 156957.36981923878
  paise/contact, uplift ratio range [1.7114, 1.8822], `gate_passed: true`.
- Final Postgres residue check: `grant_requests`, `grants`,
  `contact_slot_claims`, `customer_margin_windows`, `budget_windows` all
  at 0 rows; `agents`/`capability_scopes` at 4 rows each (the standard
  Arm-B-evidence-runner registrations — the same documented-safe residue
  pattern as the Phase 6 closure, left in place since no cleanup routine
  targets it and nothing depends on it being absent); no orphan schemas.
- **Live risk, not yet an incident**: host C: drive free space dropped
  from 6.2GB to 5.7GB (99% full) over this session's evidence collection
  — the same low-disk condition that crashed Docker Desktop during the
  Phase 6 session. Docker/Postgres remained healthy throughout this
  session, but the margin is thin; freeing disk space before further
  large evidence runs is advisable.
- **Self-inflicted, low-severity, corrected same session**: while
  reconnecting to Postgres for a residue check, an early `grep`
  intended to redact `DATABASE_URL`-style connection strings did not
  match the separate `POSTGRES_PASSWORD=...` line, and that local
  test-database password (never a production credential, `.env` is
  gitignored, nothing was committed) was echoed once to tool output.
  Corrected immediately by reconnecting via named environment variables
  without printing them. Recorded here per CLAUDE.md §8/§14's
  transparency requirement, not because the credential itself was
  sensitive.

Phase 7 is implemented, tested, and evidenced as of this entry, with the
scope reductions listed above stated explicitly rather than hidden. See
the closure session's own final report (delivered to the user, not
duplicated here) for the complete implemented/tested/evidenced
breakdown and the prepared `DECISIONS.md` entry text.

---

**Phase 8 status (Demo surface): CLOSED — implemented, tested, demonstrated,
recorded in `DECISIONS.md`, and committed.** Spec §18.1's exit criterion —
"someone who hasn't heard the pitch can watch it and tell you what got denied
and why" — is the one criterion that cannot be automated. Everything it
depends on is demonstrated by executable evidence below. The strongest
repository-level validation possible was performed (see "Cold-viewer
validation" at the end of this block) and it surfaced one real clarity defect,
which was fixed. **Showing the running demo to an actual first-time viewer
remains an OWNER action and is the only thing still open in Phases 0–8.**

**What was built (all new files, plus three additive audit edits):**
`sampark/demo/` — `isolation.py` (throwaway-schema isolation),
`scenario.py` (deterministic seed-42 subset), `clock.py` (simulated time +
computed compression ratio), `provider.py` (the channel-failure boundary that
did not exist), `enforcement.py` (the stage-two rate ceiling — the missing
`max_requests_per_hour` enforcement), `scorer_kill.py` (runtime-killable
Phase 6 `Scorer` wrapper), `chaos.py` (the seven §12.4 controls),
`runner.py` (the window loop), `cli.py` (headless runner).
`ui/` — `app.py`, `routes.py`, `sse.py`, `session.py`, `models.py`,
`static/{index.html,app.js,styles.css}`.
Additive only, in `sampark/audit/`: `event_types.py` (+`MODEL_DEGRADED`),
`emit.py` (+`event_for_model_degraded`), `sink.py` (+`record_agent_struck`,
`record_agent_revoked`, `record_model_degraded` — wiring emitters that had
existed and been unit-tested since Phase 5 with no caller).

**Protected files: UNTOUCHED, verified byte-identical.**
`git diff aa87123 HEAD -- sampark/allocator/constants.py
sampark/allocator/calibrated.py sampark/budget/issuance.py
sampark/policy/hard/ sampark/policy/soft/ sampark/policy/types.py` is EMPTY.
`sim/**`, `agents/**`, `sampark/registry/**`, `sampark/mediation/**`,
`sampark/models/**`, `sampark/schema.sql` and `results/**` are unmodified.
Phase 4 gate re-run read-only: mean A 89387.38, mean B 156957.37, uplift
[1.7114, 1.8822], `constants_commit_sha aa87123…`, **GATE: PASS**;
`results/gate_headline.json` hash `ed82eeb6…` unchanged.

**Three decisions that diverge from a literal reading of the spec, each
deliberate and each surfaced in the UI and README rather than buried:**

1. **Only `agent.rate_ceiling_exceeded` accumulates a strike.** §12.3's
   literal list is "budgets, rate ceiling and quiet hours deny. Strikes
   accumulate." Budget/allocation denials and quiet-hours deferrals are the
   NORMAL outcome for a well-behaved agent and occur in the thousands in
   every committed Arm B run; striking on them would revoke all four honest
   agents within one run and turn the "scope violations = 0" headline into a
   screen of false accusations. The denials all still happen and are all
   shown; only the STRIKE is narrowed. Asserted by
   `tests/demo/test_rate_ceiling_and_strikes.py::test_losing_a_fair_contest_can_never_strike`.
2. **Chaos control 7 drives `dispute_open`, not `rto_flag`.** The `rto_flag`
   interlock row is declared with a condition that returns `None`
   unconditionally — it never reads the ledger and can only report
   FACT_UNAVAILABLE. Making it deny needs edits to `policy/hard/interlocks.py`
   AND `policy/types.py` (both protected) and would flip
   `fact_unavailable.rto_flag` from *recorded* to *resolved*, changing the
   committed Phase 4/6/7 counts. `dispute_open` is a real, working DENY row
   of the same matrix. Carried in the control's own `spec_note` so it reaches
   the screen.
3. **`record_scope_denial` remains unwired.** Stage-one scope denials do not
   strike, so the revocation on camera is unambiguously caused by stage two —
   the contrast §12.3 calls "the entire thesis in ninety seconds". Pinned by
   `test_record_scope_denial_has_no_production_call_site`.

**The retry semantics are a finding, not a shortcut.** "Rollback then retry
the same request" is not implementable here: `budget/issuance.py` step (1)
returns any existing grant for a `request_id` regardless of state,
`grant_id = uuid5(NS_GRANT, request_id)`, and `ROLLED_BACK` is terminal in
both lifecycle modules — so re-issuing would require editing the human-owned
SERIALIZABLE transaction. Spec §6.2 never asked for it: "slot is NOT silently
consumed; no double-send on retry" is TWO promises, kept by the rollback and
by a `grant_id`-keyed provider idempotency store respectively.

**Model availability is NOT reinterpreted.** `build_scorer()` still returns
`HeuristicScorer` on this dataset (no untreated control population — the
committed Phase 6 finding). Phase 8 records that as a real degradation
(`model.artifact_unavailable`) at run start rather than starting silently
degraded, and the chaos control injects a second, distinct reason
(`model.killed_by_operator`). Both converge on the same deterministic
fallback. **Nothing anywhere claims the uplift model was available.**

**Evidence collected live this session (every number from a real command):**
- Deterministic replay: 113 audit events, head hash
  `333be7b8129a988ae3822079ad5279902093435cc2023ebe45994a3e0382b318`,
  reproduced IDENTICALLY across three independent execution paths — the
  headless `sampark.demo.cli`, the FastAPI `TestClient`, and a live `uvicorn`
  server. Both determinism tiers hold (logical projection AND full canonical
  bytes including signatures).
- One hands-off replay produces all three §12.3 failures: `grant.rolled_back`
  ×1 (with 2 real provider retries), `scope.channel_not_allowed` ×1 +
  `scope.incentive_ceiling_exceeded` ×1 (stage one), `agent.rate_ceiling_exceeded`
  ×3 → `agent.struck` ×3 → `agent.revoked` ×1 → `scope.agent_revoked` ×4
  (stage two), and `model.degraded` ×2 (both reasons).
- The four honest agents finish ACTIVE with `strike_count = 0`.
- Demo chain verifies after every failure mode: `VALID: True`,
  `genesis_ok: True`, `linkage_ok: True`, no missing grant reservations.
- `public.audit_events` unchanged at **560 events**, head
  `bf4ad0d0…b18244`, before and after every run.

**What broke during Phase 8, and the fix (for the owner's `DECISIONS.md`,
not written there by Claude per §13):** a post-run residue check found a row
in `public.budget_windows` dated `2025-09-10` — a DEMO window date — beside
the one documented pre-existing `2099-01-01` fixture artifact. Cause:
`DemoSession.reset()` dropped the demo schema while the runner thread was
still mid-run, and `drop_demo_schema` then reset that shared connection's
`search_path` to `public`; the surviving daemon thread's next unqualified
`seed_budget_window` INSERT resolved against `public`. Fixed in three layers
— a cooperative `DemoRunner.request_stop()` checked at window boundaries;
teardown now stops and joins the thread BEFORE dropping; and
`drop_demo_schema` now leaves `search_path` EMPTY rather than `public`, so
anything escaping the first two layers fails loudly with "relation does not
exist" instead of silently writing to the real database. Pinned by
`tests/demo/test_reset_never_leaks_into_public.py` (4 tests). The stray row
was deleted; `public.audit_events` was never affected.

**Separately observed, PRE-EXISTING, not caused by Phase 8:** running
`tests/audit/**` removes the four residual `public.agents` /
`public.capability_scopes` rows. Those fixtures use `search_path TO <schema>,
public` (documented in `tests/audit/conftest.py`), so `agents` — not
duplicated in their isolated schema — falls through to `public`, and their
teardown deletes the same `agent_id`s the Arm B evidence runner registers.
CLAUDE.md already described those four rows as inert residue that "nothing
depends on being absent". No Phase 8 code writes to `public` at all (verified
by grep: every `public.` reference under `sampark/demo/` and `ui/` is a
read-only SELECT or a docstring). Flagged rather than silently absorbed.

**Deliberately scoped down, stated rather than skipped:** no LLM-rendered
explanation (spec §8.10) — `ANTHROPIC_API_KEY` is present but EMPTY, so the
call cannot be exercised or verified (CLAUDE.md §8/§14); the deterministic
`format_explanation` ships instead, returning the raw events the sentence was
derived from so it can be checked against the record. No authentication (§13
"Out"); the demo binds to 127.0.0.1 and its safety rests on structural schema
isolation, not on access control. No recovery-outcome modelling in the demo —
Phase 8 is a decision-trace demo, not an evidence run, so grants settle at
their reserved ceiling and Arm A/B economics remain `sim/`'s job. No new
`results/*.json` evidence matrix was produced or needed.

**Cold-viewer validation (owner-closure pass).** A harness applied
`ui/static/app.js`'s OWN classification logic to a live run and checked, for
each of the seven comprehension questions a first-time viewer must be able to
answer, whether the information is actually present and visible in what the UI
renders: what happened (7 pipeline stages light), what was denied (38 denial
rows in the loudest region, each with its reason code), why (every denial
carries a machine reason code, and click-through yields the deterministic
sentence "DENIED on scope: scope.channel_not_allowed. The allocator never
ran."), what rolled back and recovered (1 `grant.rolled_back` violet + 9
`grant.confirmed` green), what became of the rogue agent (both stages, 3
strikes counting 1→2→3, revocation, then 4 `scope.agent_revoked` denials),
what happened when the model was killed (both degradation reasons, fallback to
`HeuristicScorer`, 10 grants still issued), and that the display comes from
the audit trace (every row carries `event_id`/`prev_hash`/recomputed `hash`
and chains; `/api/verify` VALID). All seven pass.

That validation found ONE genuine clarity defect and it was fixed additively,
with no backend change: **"compliance held" was not visible anywhere**, even
though spec §12.3 calls the recovery-drops-compliance-does-not distinction
"the whole design philosophy". Three compliance tiles were added — quiet-hour
violations, contact-cap breaches, and scope violations by honest agents —
computed ENTIRELY from fields already present on the streamed audit events
(`grant.reserved.send_after`/`customer_id`/`window_id`,
`request.denied_on_scope.agent_id`), so they remain audit-derived system truth
inside `auditState` and do not weaken the trace-integrity rule. They render
green at zero and red if ever non-zero. All three read 0 on a real run.
`tests/test_ui_renders_only_audit_events.py` and `tests/ui/**` were re-run
after this change: 50 passed.

---

**Phase 9 status (Evidence run, sensitivity analysis, final documentation):
ENGINEERING CLOSED.** Spec §18.1's exit criterion — *"Every cell filled
including unfavourable ones; a stranger can run it from the README alone"* —
is met on the first clause by executable evidence, and on the second by a
rewritten README front matter with a quickstart. The second clause's ultimate
test is a human one and is named as owner-only below.

**What was built (all new, plus keyword-only parameters on three `sim/`
functions — `sampark/` was not touched at all):**
`sim/sensitivity.py` (the spec §11 sweep), `sim/abh_table.py` (the canonical
A/B/H table, Wilson intervals, mechanism decomposition), keyword-only
`beta_fatigue`/`beta_incentive` on `sim/environment.py`'s `p_recover` /
`Environment`, `sim/arm_a.py::run_arm_a` and `sim/arm_b.py::run_arm_b` (every
default resolves to the frozen module constant, so every pre-Phase-9 call site
is byte-identical), plus `tests/sim_sensitivity/`, `tests/sim_abh/` and
`tests/arm_b/test_memory_postgres_parity_at_anchor.py`.

**The precommitment came first, deliberately.** `results/phase9_precommitment.json`
was committed in its own commit BEFORE `sim/sensitivity.py` existed and before
any result was observed, mirroring Phase 7's Decision-17 mechanism. A test
(`tests/sim_sensitivity/test_precommitment_binding.py`) asserts the code's grid
still equals that committed file, so the grid cannot drift once results are in.

**The property the sweep rests on, asserted rather than argued:** under world v1
no realized outcome feeds back into any decision (agents select all actions
before the window loop; `carried_forward` is a function of the decision, not the
outcome; nothing reads `outcome.recovered`). So varying `BETA_FATIGUE` changes
which contacts SUCCEED and never which contacts HAPPEN — every decision and
`prev_hash` is invariant. `tests/sim_sensitivity/test_decision_invariance.py`
proves it directly, with a negative control showing the parameter is genuinely
wired to something. That is why the sweep needed no allocator re-run, no
SERIALIZABLE issuance and no PostgreSQL: 88.9 minutes instead of the ~12 hours a
Postgres grid would have cost on a disk with 12.8 GB free — the same low-disk
condition that crashed Docker in Phase 6.

**Evidence collected (50/50 points, grid verified identical to the
precommitment, 10/10 anchor checks reproducing committed Phase 4 evidence,
0 tracebacks):**
- All six precommitted predictions **PASS**.
- `BETA_FATIGUE` 0.0 → 2.0: mean uplift 1.5513× → 1.9282×, monotone.
- **The finding that qualifies the pitch:** at `BETA_FATIGUE = 0.0` the
  cross-agent fatigue externality is switched off entirely and Arm B still wins
  by 1.5513× — about 73% of the frozen-value advantage. **Selection, not fatigue
  internalisation, is the dominant mechanism.** Spec §8.6 calls the fatigue term
  "the whole thesis expressed as arithmetic"; the measurement does not support
  that as the primary driver. Recorded in README, ARCHITECTURE.md and
  DISCLAIMER.md rather than left for a reviewer to find.
- **The genuine losing condition:** Arm B recovers less TOTAL revenue at every
  one of the 50 points (`B ÷ A` 0.7996 → 0.9938). No crossing exists on
  ₹/contact, so "where SAMPARK stops winning" is answered on the revenue axis.
- A/B/H (world v2, f=0.10, 5 seeds): contacts −48.5%, total revenue −8.2%,
  ₹/contact 1.687×; **holdout ground truth inside the Wilson 95% interval in
  10/10 cells** (Phase 9 extended Phase 7's single-cell check to all ten).

**A defect Phase 9 found in its own new code:** the Wilson interval returned a
lower bound of −2.8e-17 at zero successes — a negative probability, from float
cancellation. Its own invariant test caught it before any result was published.
Clamped to [0,1]; the bit-for-bit reproduction of the committed Phase 7 interval
still passes.

**Scope reductions: none.** The owner authorized full scope and the full
precommitted 50-point grid was executed.

**Deliberately NOT done, each recorded rather than silently skipped:**
contact-cap sensitivity (both constants are module-scope imports inside
PROTECTED Phase 4 files — the most economically interesting knob in the system,
and protection forbids touching it); loosening `build_scorer()`'s all-or-nothing
model gate even though fatigue-hazard is available and uplift is not (loosening
an availability gate after seeing which half passed is result-driven tuning);
the live English→IR LLM call (`ANTHROPIC_API_KEY` present but empty); p99 grant
decision latency (no instrumentation exists anywhere — reported as NOT MEASURED
rather than estimated from an in-memory run).

**`DECISIONS.md` was NOT modified** (CLAUDE.md §13). Phase 9's proposed decisions
and prepared entry text are in `PHASE9_OWNER_DECISIONS_PROPOSAL.md`, following the
existing `*_PROPOSAL.md` convention.

**Open owner items:** the Phase 8 cold-viewer criterion (still the only open item
from Phases 0–8); whether to ship a commented `policies/activated.yaml`; CI
runtime (the full suite now genuinely runs the ~1h40m Postgres holdout tests);
and whether to supply an API key for a live compiler run.

Update this section at each phase boundary.
