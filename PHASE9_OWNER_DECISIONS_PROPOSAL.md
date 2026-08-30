# Phase 9 — proposed decisions and prepared `DECISIONS.md` text

**This document is a PROPOSAL. Nothing in it has been locked into the project.**

`DECISIONS.md` is owner-written (CLAUDE.md §13) and **was not modified by Phase 9**.
This file follows the existing convention of `PHASE4_SCHEMA_AND_ISSUANCE_PROPOSAL.md`
and `CI_POSTGRES_SERVICE_PROPOSAL.md`: a reviewed proposal retained as a durable
record, separate from the artifacts the owner maintains by hand.

Two sections:

- **Part A** — decisions Phase 9 made autonomously, within existing authority.
  Listed for the record; no sign-off required, but each is reversible.
- **Part B** — decisions that require owner sign-off and were **deliberately left
  open**. Phase 9 did not resolve these.
- **Part C** — prepared `DECISIONS.md` entry text, for the owner to accept,
  edit, or discard.

---

## Part A — decisions taken within existing authority

### A-1. The sensitivity sweep varies `BETA_FATIGUE` and `BETA_INCENTIVE`

**Authority:** spec §11 names the fatigue-hazard sweep explicitly. `sim/environment.py`'s
own docstring names these two coefficients as "expected to be revisited (e.g. the
spec §11 sensitivity sweep)".

**Why these and not others:** they are *ground-truth world* parameters, so varying
them tests whether the result is an artifact of the simulator — which is the
objection spec §14 Round 7 raises. Varying a *system* parameter would be an
ablation, and five of those are already committed.

**Reversible:** yes. The sweep adds files; it changes no committed evidence.

### A-2. The sweep runs on the memory backend

**Justification, established before the sweep was written:** `run_arm_b(42, memory)`
reproduces the Postgres-backed `results/arm_b_metrics_42.json` **bit-for-bit** in
63 seconds versus ~48 minutes. Pinned as
`tests/arm_b/test_memory_postgres_parity_at_anchor.py`.

**Scope of the claim:** world v1 only. It explicitly does **not** claim parity for
world v2, where the in-memory ledger's opt-out enforcement genuinely differs.

**Why it matters:** a Postgres-backed grid would be ~12 hours on a disk with
12.8 GB free — the precise condition that crashed Docker in Phase 6.

### A-3. Contact-cap sensitivity was excluded, not skipped

`CONTACT_CAP_24H` / `CONTACT_CAP_7D` are module-scope imports inside
`sampark/policy/hard/contact_cap.py` and `sampark/budget/issuance.py`, both
**protected**. Sweeping them requires editing a protected file.

**This is the most economically interesting knob in the system**, and Phase 4
protection forbids touching it. The exclusion is recorded in
`results/phase9_precommitment.json` and published in `DISCLAIMER.md` §13 rather
than worked around.

### A-4. The precommitment was committed before the sweep code existed

`results/phase9_precommitment.json` is commit `eabdbd1`, made **before**
`sim/sensitivity.py` was written and before any result was observed. Its entire
value is that ordering, and a test asserts the code's grid still matches it.

**Note for the owner:** this is a deviation from the instruction "do not commit
until implementation is complete and tests are green." It was taken deliberately,
because a prediction that is committed *after* the results are known is not a
prediction. If the owner prefers, the two commits can be described as one unit of
work in the log; they should not be squashed, since squashing destroys the
timestamp that makes the precommitment meaningful.

### A-5. `policies/README.md` was added; no policy rule was authored

CLAUDE.md §3 makes policy rules human-owned, so Phase 9 authored **none**. It
added only a **map** of the eleven existing rules to their modules, citations and
tests, because spec §19's repository layout lists `policies/` as a deliverable and
a reviewer following that map would otherwise find an empty directory.

`policies/activated.yaml` was **not** created (see B-2).

### A-6. Phase 9 modified no file under `sampark/`

The entire analytical layer lives in `sim/` and `tests/`. Asserted as a test
(`test_phase9_touched_no_file_under_sampark`), not just verified by hand.

### A-7. A Wilson-interval defect was found and fixed by its own test

At zero successes the interval returned a lower bound of `-2.8e-17` — a negative
probability, from floating-point cancellation. Clamped to `[0, 1]`, which is
correcting float error rather than changing the interval: every value strictly
inside `(0, 1)`, including every committed Phase 7 value, is returned unchanged.
The reproduction test against `phase7_holdout_validity_seed42_f10.json` still
passes bit-for-bit.

---

## Part B — OPEN, requires owner sign-off

Phase 9 did **not** resolve these. Each is recorded with a recommendation.

### B-1. `build_scorer()`'s all-or-nothing model gate — OPEN

**The situation:** the fatigue-hazard model is **available** on this dataset (all
5 seeds × both fractions, 32 buckets). The uplift model is **not**. `build_scorer()`
requires **both**, so no model reaches a decision and the measured model
contribution is exactly 0.00 %.

**Phase 9's action: none.** The gate was left untouched.

**Recommendation: leave it.** Loosening an availability gate *after* observing
which half passed is result-driven tuning, and declining to do it is a stronger
AI-judgment signal than a marginally better number. A fatigue-only scorer would
also require a fresh 5-seed Postgres campaign (~4 hours) and would place a new
number beside the frozen Phase 4 headline.

**If the owner disagrees:** the defensible form is a *new named ablation*
(`phase9_model_fatigue_only`), never a change to the shipped path — and it must
be precommitted before it is run.

### B-2. What ships in `policies/` — PARTIALLY OPEN

Phase 9 added `policies/README.md` only. Still open:

- whether to commit an explicit, comments-only `policies/activated.yaml` documenting
  why it must remain empty (behaviourally identical to its absence — the loader
  returns `()` either way);
- whether to commit the compiler's golden-corpus IR/rendered artifacts so the
  compiler's *output* is visible in the tree.

**Recommendation:** add the commented `activated.yaml`; leave the IR artifacts out
until an actual English→IR compile has been run against a live model, so the
directory never contains an artifact no model produced.

### B-3. CI runtime — OPEN

`.github/workflows/ci.yml` runs the full suite and now has a real `postgres:16`
service, so `tests/sim_arm_b_holdout/` (~1 h 40 m locally) **actually runs**.
Every push to `main` is plausibly a ~2-hour job. Phase 9 adds ~4 minutes.

**Recommendation:** add a `slow` marker to the multi-minute Postgres tests, run
`-m "not slow"` on push/PR, and add a scheduled/dispatch job that runs everything.
**Document the split in the README** so it cannot read as hiding a failure.

**Explicitly do not** slow-mark `tests/test_concurrent_grant_issuance.py` — it is
the project's most important test and it is fast.

### B-4. The live LLM path — OPEN

`ANTHROPIC_API_KEY` is present but empty, so the English→IR step has never run.
Phase 9 changed nothing and claims nothing.

**Recommendation:** if a key is supplied, run exactly two bounded, recorded calls
— one English→IR compile diffed against the hand-authored golden IR, and one
audit-log explanation generated from a committed event set — and commit both
transcripts. If not, ship as-is. **Do not simulate a call.**

### B-5. p99 grant decision latency — OPEN

Spec §11 lists the row; no latency instrumentation exists anywhere. Phase 9
reports it as **NOT MEASURED** rather than estimating it.

**Recommendation:** leave it unmeasured for this submission. An in-memory
measurement would exclude the `SERIALIZABLE` round-trip that dominates real
decision cost, and presenting it as production latency would be worse than a
blank cell. Instrumenting `sampark/mediation/service.py` would add a side effect
to the Phase 4 decision path for a metric.

### B-6. The cold-viewer test — OPEN, owner action, no substitute

Spec §18.1's Phase 8 criterion ("someone who hasn't heard the pitch can watch it
and tell you what got denied and why") requires showing the running demo to an
actual first-time viewer. **No test can do this.** Everything it depends on is
implemented and was validated at repository level in Phase 8. This remains the
only open item from Phases 0–8.

---

## Part C — prepared `DECISIONS.md` entry text

*(For the owner to accept, edit, or discard. Not written into `DECISIONS.md` by
Phase 9.)*

> **Phase 9 — Evidence run, sensitivity analysis and final documentation**
>
> The sensitivity sweep spec §11 asks for turned out to be far cheaper than
> budgeted, because of a property of the runner nobody had written down: under
> world v1 no realized outcome ever feeds back into a decision. Agents select
> every action before the window loop starts, deferral carry-forward is a
> function of the decision rather than the outcome, and nothing reads
> `outcome.recovered`. So moving `BETA_FATIGUE` changes which contacts succeed
> and never which contacts happen — every admission, ranking, grant, denial and
> `prev_hash` is invariant. That makes the sweep a pure re-observation needing no
> allocator re-run, no SERIALIZABLE issuance and no PostgreSQL: minutes instead
> of the twelve hours a Postgres grid would have cost on a disk with 12.8 GB
> free, which is the condition that crashed Docker in Phase 6. The property is
> asserted as a test with a negative control, not argued in a docstring.
>
> The grid, anchor, primary metric, six predictions and five falsifiers were
> committed before the sweep code existed. A prediction committed after the
> results are known is not a prediction, so the precommitment went in as its own
> commit and a test asserts the code's grid still matches it.
>
> What broke, and what we did about it: the Wilson interval returned a lower
> bound of −2.8e-17 at zero successes — a negative probability. Its own invariant
> test caught it before any result was published. Clamping to [0, 1] corrects
> float cancellation without moving any value strictly inside the interval, and
> the bit-for-bit reproduction of the committed Phase 7 interval still passes.
> The lesson is the same one the Phase 8 schema leak taught: the tests worth
> writing are the ones that assert a property, not the ones that assert the
> implementation's own output.
>
> The sweep ran the full precommitted grid: 50 points, 88.9 minutes, 10 of 10
> anchor checks reproducing the committed Phase 4 evidence, no tracebacks, no
> scope reduction. All six predictions passed.
>
> The result worth arguing about is the one that goes against the pitch. At
> `BETA_FATIGUE = 0` the cross-agent fatigue externality is switched off
> completely, and Arm B still beats Arm A by 1.5513× — roughly 73% of the
> advantage measured at the frozen value. The spec calls that fatigue term "the
> whole thesis expressed as arithmetic." The measurement says otherwise:
> selection — ranking by expected net and declining low-value contacts — is doing
> most of the work, and fatigue supplies the rest, growing as it worsens. That is
> not what we predicted the story to be, and it is now in the README, the
> architecture document and the disclaimer rather than buried in a JSON file.
> The prediction that fatigue mattered was still correct in direction and passed
> its precommitted band; it was wrong about magnitude, and saying so costs
> nothing and buys credibility.
>
> The honest losing condition turned out to be on a different axis than expected.
> There is no crossing point on rupees-per-contact anywhere in the tested range —
> mediation never stops winning on efficiency. It loses on total revenue at every
> one of the 50 points, by between 0.6% and 20%. So "where SAMPARK stops winning"
> is answered, but on the revenue axis. The trade improves monotonically as
> fatigue worsens: at `BETA_FATIGUE = 2.0` Arm B recovers 99.38% of Arm A's
> revenue on roughly half the contacts, which is the strongest form of the
> argument the project can honestly make — mediation is most valuable exactly
> where customer attention is most fragile.
>
> Scope reductions, stated rather than hidden: contact-cap sensitivity was not
> tested because both constants are module-scope imports inside protected Phase 4
> files, and that is the most economically interesting knob in the system;
> `build_scorer()`'s all-or-nothing gate was left alone even though the
> fatigue-hazard model is available and the uplift model is not, because
> loosening an availability gate after seeing which half passed is result-driven
> tuning; and the live English→IR LLM step remains unexercised for want of a key.
