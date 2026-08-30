# `policies/` — where the rules actually live

Spec §19's repository map lists `policies/` as the home of the regulatory rules,
"each rule: source citation + test — hand-written". This file exists because a
reviewer following that map would otherwise find an empty directory and draw the
wrong conclusion.

**The rules are real, cited and tested. They live in `sampark/policy/hard/` as
executable Python, not as data files here.** That was a deliberate choice: a rule
that is a Python function is type-checked, unit-tested, and composed in a frozen,
ordered tuple that a test asserts the order of — none of which a YAML file gets.

This directory holds the **policy compiler's** artifacts instead (§2 below), and
it is currently empty of them for a reason that is itself load-bearing (§3).

---

## 1. The eleven hard rules and their citations

Evaluation order is frozen in `sampark/policy/hard/__init__.py::HARD_RULES` and
pinned by `tests/policy/hard/test_hard_filter_ordering.py`. Regulation is always
evaluated strictly before merchant preference.

| # | Rule | Module | Citation |
|---|---|---|---|
| 1 | `opt_out` | `sampark/policy/hard/opt_out.py` | TCCCPR 2018 — opt-out honouring |
| 2 | `consent_scope` | `sampark/policy/hard/consent_scope.py` | DPDP Act 2023 purpose limitation + TCCCPR 7-day transactional-consent validity |
| 3 | `dlt_template` | `sampark/policy/hard/dlt_template.py` | TCCCPR 2018 — DLT template registration |
| 4 | `interlock.dispute_open` | `sampark/policy/hard/interlocks.py` | spec §8.8 row 4 (chargeback in last 90 days) — **proxy**, see the module |
| 5 | `interlock.rto_flag` | `sampark/policy/hard/interlocks.py` | spec §8.8 row 1 (RTO Shield flagged → block cart recovery/upsell) |
| 6 | `interlock.refund_in_flight` | `sampark/policy/hard/interlocks.py` | spec §8.8 row 2 (refund issued/in-flight → block dispute contest/retry) |
| 7 | `interlock.fraud_review` | `sampark/policy/hard/interlocks.py` | spec §8.8 row 3 (fraud review → block promotional contact/incentives) |
| 8 | `interlock.mandate_cancellation` | `sampark/policy/hard/interlocks.py` | spec §8.8 row 5 (cancellation requested → block mandate retry) |
| 9 | `interlock.active_grant_in_window` | `sampark/policy/hard/interlocks.py` | spec §8.8 row 6 (grant active → block every other agent) |
| 10 | `quiet_hours` | `sampark/policy/hard/quiet_hours.py` | TCCCPR 2018 — 21:00–09:00 IST blackout |
| 11 | `contact_cap` | `sampark/policy/hard/contact_cap.py` | rolling 24h/7d contact caps |

Tests live under `tests/policy/hard/`.

**Two honesty notes carried here rather than left in the code:**

- **`interlock.rto_flag` is declared but not enforced.** Its condition returns
  `None` unconditionally — it never reads the ledger and can only ever report
  `FACT_UNAVAILABLE`. Making it deny would require editing protected Phase 4
  files and would change committed evidence counts. Five of the six interlock
  rows can actually deny.
- **`FACT_UNAVAILABLE` never short-circuits.** A candidate can be
  hard-ADMISSIBLE and still carry recorded gaps; those reason codes are attached
  to the decision and published per seed. They are *recorded*, not *resolved*.

> These rules are an **interpretation** of public regulatory text by a student,
> not legal advice and not audited against anything. The word "compliant" is
> never used unqualified in this repository. See [`../DISCLAIMER.md`](../DISCLAIMER.md) §10.

---

## 2. What this directory is for

The policy compiler (`sampark/policy/compiler/`) writes here:

```
policies/source/     English rule text (input)
policies/ir/         the LLM-produced PolicyIR, as JSON (the ONLY LLM step)
policies/rendered/   the generated Python rule
policies/compiled/   the generated pytest case for that rule
policies/activated.yaml   flat list of rule_ids an owner has approved for runtime
```

The pipeline is: **English → IR (LLM) → deterministic validate → deterministic
compile → generated pytest → activation only if that test passes and an owner
lists the rule in `activated.yaml`.** The LLM proposes; deterministic code
disposes. The output is a checked artifact, not an unverifiable prompt.

---

## 3. Why it is empty — and why that matters

**`policies/activated.yaml` does not exist, which means zero compiled rules are
active. That is the correct and required state.**

`sampark/policy/compiled/__init__.py` returns `()` when the file is absent, and
composes any activated rules strictly *after* the eleven hand-written rules
above, never before.

This is load-bearing for the committed evidence: **a compiled rule that denied
candidates the frozen eleven would have admitted would change Arm B's allocation
and invalidate every committed result file.** It is enforced as a regression
test, not as a convention:

- `tests/policy/test_activation_empty_in_protected_evidence.py`
- `tests/sim_sensitivity/test_phase4_protection.py::test_activated_policy_file_stays_empty_for_protected_evidence`

**The live English→IR LLM step has never been exercised** — `ANTHROPIC_API_KEY`
is present but empty, and `sampark/policy/compiler/llm.py` fails loudly rather
than fabricating a response. The committed fidelity measurement
(`results/phase7_compiler_fidelity.json`: 9/9 canonical, 4/4 paraphrase) exercises
the **deterministic** parse-and-validate pipeline against hand-authored golden
IRs in `tests/policy/compiler/golden/corpus.py`, including a deliberately wrong
rule that fails its own generated test.

So there are no committed IR artifacts here because no rule has been compiled
from English by a model, and none has been activated. Populating this directory
is an **owner decision**, not something to do to make the tree look fuller.
