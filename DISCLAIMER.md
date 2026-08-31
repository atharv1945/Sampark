# SAMPARK — Limitations and disclaimers

**This is a simulation. Every number in this repository is conditional on a
generator we wrote.**

This document exists so a technically sophisticated reviewer can find the limits
of this work faster than they could find them by digging. It is not legal
boilerplate and it is not a hedge. Where a limitation is severe, it is stated
plainly and early.

Throughout, three claim strengths are kept distinct and are never blurred:

| Label | Meaning |
|---|---|
| **DEMONSTRATED** | Shown by executable evidence committed in this repository |
| **ARCHITECTURAL CAPABILITY** | The mechanism exists and is tested, but was not exercised at production scale or with production data |
| **NOT VALIDATED** | Not shown here at all; asserted nowhere |

---

## 1. Synthetic data

**NOT VALIDATED against reality.** No public dataset of real merchant recovery
logs exists. The environment is 5,000 synthetic people and ~20,000 synthetic
risk items over one simulated month, from a committed, seeded generator
(`sim/generator.py`, `sim/population.py`).

The generator is committed, its parameters are published, and it is
parameter-swept (§13). **That makes the results reproducible and falsifiable. It
does not make them evidence about the real world.**

## 2. Simulated response behaviour

The ground-truth response process is hand-specified
(`sim/environment.py`):

```
logit(p) = logit(conversion_propensity)
         + BETA_INCENTIVE · (incentive_bps/10⁴) · price_sensitivity
         − BETA_FATIGUE  · prior_contacts · fatigue_hazard
```

`BETA_INCENTIVE = 4.0` and `BETA_FATIGUE = 1.0` are **not calibrated against
anything.** The module's own docstring says so. They were chosen as a Phase 2
starting point and never re-tuned after seeing a result.

**Real Indian consumer response to Hinglish voice recovery is not something this
project can measure**, and nothing here should be read as measuring it.

## 3. Natural recovery is a PRIOR, not an estimate

`NATURAL_MULTIPLIER_BY_ROOT_CAUSE` (`sim/natural.py`) is an owner-authored prior.
The **ordering** is the defensible claim (transient technical failure > customer-
decision failure > structurally blocked); the exact digits are a judgement.

> **The most likely misreading of this repository.** Phase 7 precommitted a
> closed-form prediction (Σ p_natural × amount = 30,658,277 paise) and observed
> 31,947,441 — a 4.2 % error. **That validates that the simulator is internally
> consistent with its own prior. It is NOT external validation of the natural-
> recovery model.** Anyone reading "natural recovery was validated to within
> 4 %" has been misled.

## 4. Holdout estimation limitations

**DEMONSTRATED:** ground truth falls inside the holdout's 95 % Wilson interval in
**10 of 10** (seed × fraction) cells (`results/phase9_holdout_validity_all.json`).

Limits of that result:

- At f = 0.10 the holdout is ~1,962 items / 490 customers. The interval is
  correspondingly wide: **±1 percentage point on a ~5 % rate.** Small holdouts
  give noisy credit, and the intervals are reported rather than suppressed.
- The baseline is stratified by `(source, root_cause)` with fallbacks. At seed 42
  / f=0.10, 16,532 credits resolved at `(source, root_cause)` and **1,506 fell
  back to `(source)`**. Thin strata are real.
- All ten cells come from **one generator**. Ten cells is not ten independent
  worlds.

## 5. Interference — a real SUTVA violation, measured but not eliminated

The merchant margin budget is deliberately **not** scaled by `(1 − fraction)`
(Phase 7 Decision 3). Holding out 10 % of customers therefore frees contact and
margin capacity for the remaining 90 % — so a held-out customer's absence changes
a treated customer's treatment.

**This is a genuine violation of the stable-unit-treatment-value assumption, and
it is inherent to mediating a SHARED budget.** It cannot be designed away without
destroying the mechanism under study. Phase 7 chose to *measure* it by running
two fractions (0.10 and 0.20) rather than assume it away. It is not solved.

## 6. Arm H — what it is and is not

**Arm H is the counterfactual a real merchant can never run.** Nobody withholds
all recovery activity from an entire customer base for a month.

- Arm H **validates** the deployable estimator, once, in simulation.
- Arm H **never feeds a credit.** Using it would make the attribution ledger
  depend on information no production system could obtain. Enforced by an AST
  test, not by discipline.
- Any real deployment has only the randomized holdout. **The Arm H comparison is
  a property of this simulation and does not transfer.**

## 7. Arm B-H's "natural recovery" figure is NOT a baseline

> **The single most misreadable number in the results tables.**

Arm B-H reports natural recovery over its uncontacted pool — **10,733 items at
seed 42 / f=0.10.** That pool is a **mixture**: the 1,962-item randomized holdout
**plus 8,771 allocator-declined items.**

Declined items were selected *on low expected value by the allocator itself*, so
that pool's per-item rate (9,493 paise) is far below the randomized holdout's
(16,283 paise) **because it is a selected population, not because natural
recovery is lower.** Only the randomized part is a valid control, and
`sampark/attribution/baseline.py` uses only that part. The field carries a
`WARNING` in the generated table for this reason.

## 8. Model limitations

**The uplift model does not work on this data, and no result in this repository
depends on it working.**

- **Uplift (T-learner):** implemented, **unavailable** across all 5 seeds × all 4
  tested holdout fractions (0.10–0.40). Structural: no untreated control
  population exists per bucket at the required floor.
- **Fatigue hazard:** implemented, **available** (32 buckets, all cells) — but it
  does **not** reach a decision, because `build_scorer()`'s gate requires both
  components and that gate was deliberately not loosened after observing which
  half passed.
- **Measured model contribution to the headline: exactly 0.00 %.**
  `gate_phase6_model.json` reproduces `gate_phase6_heuristic.json` bit-for-bit.

**Every headline number in this repository was produced by the deterministic
heuristic scorer.** Any reading of this project as "ML improved recovery" is
wrong, and the ablation rows are committed specifically so that reading is
impossible.

**Cold start / off-policy bias:** the design trains Arm B's models on Arm A's
logs, which are biased by Arm A's own policy. Off-policy correction is
acknowledged and **not implemented**. This is currently moot — no model reaches a
decision — but it is a real limitation of the design, not a solved problem.

## 9. Policy compiler and the LLM path

**The English→IR LLM step has NOT been exercised.** `ANTHROPIC_API_KEY` is
present in `.env` but empty. `sampark/policy/compiler/llm.py` fails loudly rather
than fabricating a response.

- `results/phase7_compiler_fidelity.json` reports 9/9 canonical and 4/4
  paraphrase. **That measures the deterministic parse-and-validate pipeline
  against hand-authored golden IRs**, not the LLM. The file's own `note` says so.
- **`policies/activated.yaml` does not exist** (the loader treats absent and empty
  identically). **No compiled rule has ever affected any
  evidence run**, and a regression test keeps it that way.
- Therefore: the compiler's *safety architecture* (LLM proposes an IR;
  deterministic code validates, compiles, and generates a test; activation
  requires a passing test plus owner review) is **ARCHITECTURAL CAPABILITY**,
  demonstrated end-to-end on hand-authored IRs including a deliberately wrong
  rule that fails its generated test. The **live LLM leg is NOT VALIDATED.**

**No LLM-rendered audit explanation was produced either.** The deterministic
`format_explanation` ships instead, and returns the raw events the sentence was
derived from so it can be checked against the record rather than trusted.

## 10. Regulatory encoding is an interpretation

TCCCPR 2018 (and its 2025 amendment) and DPDP 2023 rules were compiled by a
student reading public text — **not by counsel, and not audited against anything.**

- Rules carry citations and tests. They are **policy-enforced**, which is a claim
  about this codebase.
- **The word "compliant" is never used unqualified anywhere in this repository.**
- **This is not legal advice.** A real deployment requires actual regulatory
  review.
- `interlock.rto_flag` is **declared but not enforced** — its condition returns
  `None` unconditionally and it can only ever report FACT_UNAVAILABLE. The
  interlock matrix has six rows; five of them can deny.

## 11. Mocked channels

**No real WhatsApp, SMS or voice message was ever sent.** You cannot lawfully
cold-contact real numbers on synthetic consent. `agents/channel.py` logs the
payload that would have been sent.

Unmodelled as a result: deliverability, template rejection, carrier behaviour,
delivery latency, partial delivery, delayed or duplicate webhooks.

**Provider failure simulation is a three-mode abstraction** with a
`grant_id`-keyed idempotency store. Real providers fail in ways this does not
model.

## 12. Statistical limitations

- Confidence intervals are **Wilson score intervals at z = 1.96**, closed-form,
  no new dependency. They are appropriate for the small-proportion regime here
  and are reported rather than suppressed.
- **No hypothesis test is claimed anywhere.** No p-value appears in this
  repository. The gate is a comparison of means over five precommitted seeds,
  and it is described as exactly that.
- **Five seeds is five seeds.** `uplift_stdev` at the anchor is 0.0633. Per-seed
  wins are explicitly not required by the gate; the mean comparison is the gate.
- The optimality gap is measured over the **top 5 windows by requested incentive
  ceiling** — targeted at the windows most likely to bind, **not a random
  sample** — and the DP is **per-window, not whole-horizon**, and does not search
  incentive downgrades. It is a **lower bound** on the true achievable optimum.

## 13. Sensitivity analysis — scope and limits

**DEMONSTRATED:** the qualitative result was tested against variation in the two
ground-truth response coefficients across **50 points** (2 dimensions × 5 seeds),
with grid, anchor, primary metric and six predictions committed to git *before*
the sweep ran. All six predictions passed.

**What the sweep says against us, stated here because it qualifies the pitch:**
at `BETA_FATIGUE = 0.0` — the cross-agent fatigue externality switched off
entirely — Arm B still beats Arm A by **1.5513×**, roughly **73 %** of the
advantage measured at the frozen value. **Selection (ranking by expected net and
declining low-value contacts) is the dominant mechanism, not fatigue
internalisation.** Spec §8.6 calls the fatigue term "the whole thesis expressed
as arithmetic"; the measurement does not support that as the primary driver, and
the framing should be read accordingly.

**The genuine losing condition:** Arm B recovers **less total revenue at every
one of the 50 tested points** (`B ÷ A` from 0.7996 to 0.9938). Mediation always
gives up revenue relative to letting every agent run free. No crossing exists on
₹/contact inside the tested range, so "where SAMPARK stops winning" is answered
on the total-revenue axis, not the efficiency axis.

Limits:

- **Two dimensions only** (`BETA_FATIGUE`, `BETA_INCENTIVE`). Both are
  ground-truth world parameters, which is what makes this a sensitivity analysis
  rather than an ablation.
- **Contact-cap sensitivity was NOT tested**, and this is the most economically
  interesting knob in the system. `CONTACT_CAP_24H` / `CONTACT_CAP_7D` are
  module-scope imports inside **protected Phase 4 files** with no override path;
  sweeping them would require editing a protected file. **Naming this exclusion
  is more honest than quietly widening the protection to get a nicer chart.**
- The sweep runs on the **memory backend**. Its licence is a committed test
  showing bit-for-bit parity with the Postgres-backed record at the anchor — for
  **world v1 only**. It does **not** claim parity for world v2, where the memory
  ledger's opt-out enforcement genuinely differs.
- Phase 7's frozen priors (`NATURAL_MULTIPLIER_BY_ROOT_CAUSE`, `OPTOUT_BASE`)
  were **not swept**, deliberately: they were precommitted against the observed
  total-rupee gap, and sweeping them afterwards would be retroactive tuning.

## 14. What the Phase 4 gate does and does NOT prove

The gate is a single boolean:

```
mean(Arm B ₹/contact) > mean(Arm A ₹/contact)   over exactly 5 precommitted seeds
```

**It proves:** that on this generator, with these constants, mediated allocation
achieves higher recovery per contact than unmediated agents. It PASSes at
1.71–1.88×.

**It does NOT prove:**

- that SAMPARK recovers **more money**. It does not. **Arm B recovers ~8–9 % LESS
  total revenue than Arm A** (world v2: 7,633,415,148 vs 8,314,405,039 paise). It
  does so with roughly half the contacts. That cell is in the headline table and
  is not hedged.
- that the effect would appear on real data;
- that the constants are optimal — they were frozen before any Arm B run and
  never tuned after seeing a result, which is a discipline claim, not an
  optimality claim;
- anything about statistical significance. No test was run.

## 15. What ₹/contact does and does NOT prove

`₹/contact` is the ratio of recovered rupees to contacts sent. **It rewards
sending fewer contacts.** A system that sent exactly one contact to the single
highest-value customer would score spectacularly and recover almost nothing.

That is precisely why **total recovered rupees is reported beside it in every
table, including where it is unfavourable.** Neither number alone is the result;
the pair is.

The economic argument this project actually makes is: *customer attention is a
scarce, depletable, shared resource, and a marketplace of independently
authorized agents will over-consume it because no agent internalises the cost.*
₹/contact is the measurable proxy for that. It is a proxy.

## 16. What cannot be inferred about real customers

**Nothing.** There are no real customers in this repository.

"Priya" is a narrative device. Every phone number and email address is
synthetically generated (`sim/population.py`). No real PII was processed, stored,
or logged at any point. The `person_id` never crosses into a ledger row.

## 17. Security and privacy limitations

- **The identity layer is a demonstration, not a deployment.** Ed25519 keypairs,
  declared capability scopes, detached per-request signatures and a local
  revocation list prove the *shape* of scoped agent identity in ~200 lines.
  Production needs IdP federation, key rotation, short-lived credentials and
  delegated consent chains. **This is the one area where a dedicated
  authorization product does strictly more than SAMPARK**, and the README says so.
- **The demo has no authentication.** It binds to 127.0.0.1 and its safety rests
  on structural schema isolation, not access control.
- Razorpay integration is **`rzp_test_` only**. No live key, no live payment path.
- **No real adversary was tested.** The rogue agent misbehaves in *scripted*
  ways. A genuinely adversarial agent probing reason-code side channels or gaming
  the scorer is out of scope.
- **The allocator trusts agent-declared risk amounts.** A self-interested agent
  could win every grant by overstating. The mitigation (source amounts from the
  ledger rather than the request) is **flagged and not implemented.** This is a
  real hole and it is named here rather than discovered by a reviewer.

## 18. Demo-only mechanisms vs production mechanisms

| Mechanism | Status |
|---|---|
| `SERIALIZABLE` issuance, hash-chained audit, hard policy, allocation, budgets | **Real.** The demo uses the same code. |
| Rate-ceiling enforcement (`sampark/demo/enforcement.py`) | **Demo-only placement.** `max_requests_per_hour` had been declared and persisted since Phase 3 and read by no evaluation code; this is that missing enforcement, deliberately placed outside the Phase 4 decision path. |
| Chaos control 7 | Drives `dispute_open`, **not** `rto_flag`, because `rto_flag` can only report FACT_UNAVAILABLE. Surfaced in the UI, not silent. |
| `record_scope_denial` | Deliberately **unwired**, so the on-camera revocation is unambiguously caused by stage two. |
| Time compression | Simulated clock. The ~40 s replay represents a longer simulated horizon. |
| Recovery outcomes in the demo | **Not modelled.** Grants settle at their reserved ceiling; the demo is a decision-trace demo, not an evidence run. Arm A/B economics remain `sim/`'s job. |

## 19. Operational and reproducibility limitations

- Determinism is claimed **within** this stack: Python 3.11 (enforced at
  runtime), PostgreSQL 16, `results/*.json` forced to `text eol=lf`. It is **not**
  claimed across Python versions or platforms.
- Arm B-H's committed five-seed evidence is **memory-backend**; one Postgres run
  validated the mechanism, opt-out enforcement, cleanup and determinism. This is
  labelled in every affected file and in the tables.
- The Phase 7 Arm B ablations are **seed 42 only**, not the full 5×3 matrix. The
  reason (a code-path identity property that does not vary by seed, at ~48
  minutes per run) is recorded in `DECISIONS.md`.
- **p99 grant decision latency is NOT MEASURED.** Spec §11 lists the row; no
  latency instrumentation exists anywhere in the codebase. It is reported as
  absent rather than estimated from an in-memory run, which would not represent
  the `SERIALIZABLE` round-trip that dominates real decision cost.
- **Maintainability caveat: `sampark/demo/runner.py` is 668 lines**, large for a
  single module. It was reviewed for correctness during the Phase 9 closure pass
  — duplicated logic, shared database state, cleanup races, non-determinism and
  untested paths — and no defect was found, so it was deliberately left alone
  rather than refactored for aesthetics late in the project. It remains the
  least approachable file in the repository for a new reader.
- The full test suite genuinely runs the multi-hour PostgreSQL-marked tests, so
  **CI takes roughly three and a half hours per push.** No marker split was
  introduced, because making those tests skip without an owner decision would
  undo the reason the PostgreSQL service was added to CI in the first place.

## 20. Single-merchant scope

Cross-merchant contact fatigue — the same human being recovered by four
*different* Razorpay merchants simultaneously — is the obvious next problem and
is **deliberately out of scope.** Nothing here addresses it.

---

## Summary — the three claim strengths

**DEMONSTRATED by committed, re-runnable evidence:**

- Mediated allocation beats unmediated on ₹/contact at 1.71–1.88× across five
  precommitted seeds, while sending ~half the contacts and recovering ~8–9 %
  **less** total money.
- Compliance violations go to zero in the mediated arm while the scope-violation
  control row stays at 0/0 in both arms.
- Exactly one grant survives 50 concurrent requests for the last slot, with a
  negative control proving the test can fail.
- The audit chain verifies (560 events, `VALID: True`) and the UI structurally
  cannot report success the system did not achieve.
- Three failure modes — provider timeout/rollback, two-stage rogue agent, model
  degradation — occur in one hands-off replay and the chain still verifies.
- The greedy allocator is within ~0.04 % of a measured per-window optimum.
- The qualitative result survives variation in the ground-truth response
  coefficients, against predictions committed to git beforehand.

**ARCHITECTURAL CAPABILITY, not validated at scale:**

- The policy compiler's LLM→IR→validate→generated-test safety chain (the LLM leg
  was never run).
- Scoped cryptographic agent identity (a demonstration, not a deployment).
- The attribution ledger as a settlement substrate (`schema_proposal.sql` only).

**NOT VALIDATED, and claimed nowhere:**

- That any of this improves outcomes for a real merchant or real customer.
- That the regulatory encoding is legally sufficient.
- That the ML models work — **they were unavailable, and the measured
  contribution is zero.**
- That the system is production-ready, secure under adversarial pressure, or
  deployable as-is.
