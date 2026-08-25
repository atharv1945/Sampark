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
