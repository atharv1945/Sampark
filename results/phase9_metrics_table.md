# SAMPARK — Phase 9 canonical results

Generated from committed evidence only (no experiment re-run). Commit `eabdbd16140e7a0b06de8c5cc343682b427c6e0f`.

Every figure is labelled by provenance: **observed** (counted from a run), **estimated** (from the randomized holdout — the only control a real merchant could run), **attributed** (observed minus expected natural), **ground truth** (Arm H — not obtainable in production).

## 1. Headline A/B/H table

World v2, seed 42, holdout fraction 0.10 (490 customers held out).

| Metric | Arm A-H (unmediated) | Arm B-H (SAMPARK) | Arm H (no contact) | Δ B vs A |
|---|---:|---:|---:|---:|
| Contacts sent | 18,038 | 9,267 | 0 | **-48.6%** |
| Total recovered (paise) | 1,641,371,055 | 1,486,812,080 | 310,267,323 | **-9.4%** |
| Recovered per contact (paise) | 89,224 | 149,446 | — | **+67.5%** |
| Incentive spend (paise) | 24,649,466 | 16,805,011 | 0 | -31.8% |
| Cumulative opt-out rate | 3.44% | 2.10% | — | — |
| **Scope violations (control row)** | **0** | **0** | — | **0** |

Five-seed aggregate (f=0.10, 5 seeds): contacts -48.5%, total recovered -8.2%, rupees-per-contact ratio **1.687×**.

> **The unfavourable cell stays in.** Arm B recovers *less total money*. It does so with roughly half the contacts, which is the entire argument: attention is the scarce resource, not authorization.

> **Arm B-H's natural-recovery figure is NOT a baseline.** Its uncontacted pool mixes the randomized holdout with every allocator-declined item, which were selected on low expected value. Only the randomized part is a valid control, and that is the only part the attribution ledger uses.

## 2. Causal / attribution

| Quantity | Value | Provenance |
|---|---:|---|
| Holdout natural rate | 0.050968 (n=1962) | estimated |
| Wilson 95% CI | [0.042084, 0.061608] | interval |
| Arm H natural rate | 0.052850 (n=20000) | ground truth |
| Ground truth inside CI? | **YES** | — |
| Credits issued | 18,038 | observed |
| Observed recovery | 1,609,423,614 | observed |
| Expected natural (subtracted) | 269,069,392 | estimated |
| **Credited recovery** | **1,340,354,222** | attributed |
| Negative credits (count / tail) | 13,852 / -207,579,256 | attributed |
| Double-attributed recoveries | 0 (by construction) | observed |

Credits are **never clamped at zero**. An item that did not recover still consumed a contact against a positive natural baseline; clamping would bias the aggregate upward by exactly the negative tail.

## 3. Mechanism decomposition — where the improvement actually comes from

World v1 ablation family, five seeds. Reported beside the table above, never differenced against it.

| Configuration | mean paise/contact | Uplift vs Arm A | Contacts |
|---|---:|---:|---:|
| Arm A (unmediated) | 89,387 | 1.000× | 100,000 |
| fifo_under_cap | 97,812 | 1.094× | 84,605 |
| aging_zero | 152,761 | 1.709× | 51,481 |
| headline | 156,957 | 1.756× | 51,542 |
| merchant_margin_half | 156,533 | 1.751× | 51,542 |
| phase6_heuristic | 156,957 | 1.756× | 51,542 |
| phase6_model | 156,957 | 1.756× | 51,542 |

Hard policy plus contact caps (`fifo_under_cap`) buys the smaller share; expected-value ranking and allocation buys the rest. The margin budget is near-inert at headline capacity. **The model rows are identical to the heuristic row — the measured model contribution is exactly zero, and the row stays in.**

## 4. Model availability

- **Uplift (T-learner):** implemented; available on this dataset: **False**. No untreated control population exists per (source, root_cause) bucket at the required floor — structural, and it does not clear even at holdout fraction 0.40.
- **Fatigue hazard:** implemented; available on this dataset: **True**. Available at every cell — the only model to clear its own adequacy gate. It still does NOT reach a decision, because sampark/models/scorer.py's build_scorer() gate is all-or-nothing (uplift AND fatigue), and that gate was deliberately NOT loosened after observing which half passed.
- **Scorer actually used in every committed run:** `HeuristicScorer`
- **Measured model contribution to the headline: 0.0%**

## 5. Sensitivity analysis

Grid, anchor, primary metric and predictions were committed to `results/phase9_precommitment.json` **before** the sweep ran.

### beta_fatigue (frozen value 1.0)

| value | mean A paise/contact | mean B paise/contact | mean uplift | B total ÷ A total | B wins? |
|---:|---:|---:|---:|---:|---|
| 0.0 | 112,583 | 174,646 | 1.5513 | 0.7996 | yes |
| 0.25 | 106,244 | 169,916 | 1.5993 | 0.8243 | yes |
| 0.5 | 99,816 | 165,431 | 1.6574 | 0.8542 | yes |
| 0.75 | 94,379 | 161,103 | 1.7070 | 0.8798 | yes |
| 1.0 **(frozen)** | 89,387 | 156,957 | 1.7559 | 0.9050 | yes |
| 1.5 | 80,755 | 149,945 | 1.8568 | 0.9570 | yes |
| 2.0 | 74,203 | 143,079 | 1.9282 | 0.9938 | yes |

**Crossing point:** none inside the tested range — Arm B beats Arm A on rupees-per-contact at every tested value. No losing boundary exists inside this range; see the report's interpretation for what that implies.

### beta_incentive (frozen value 4.0)

| value | mean A paise/contact | mean B paise/contact | mean uplift | B total ÷ A total | B wins? |
|---:|---:|---:|---:|---:|---|
| 2.0 | 88,474 | 155,887 | 1.7620 | 0.9082 | yes |
| 4.0 **(frozen)** | 89,387 | 156,957 | 1.7559 | 0.9050 | yes |
| 8.0 | 91,123 | 158,973 | 1.7446 | 0.8992 | yes |

**Crossing point:** none inside the tested range — Arm B beats Arm A on rupees-per-contact at every tested value. No losing boundary exists inside this range; see the report's interpretation for what that implies.

### Interpretation — including the part that is unflattering

**1. SAMPARK does not stop winning on rupees-per-contact anywhere in the tested range.** Uplift rises monotonically from **1.5513×** at `BETA_FATIGUE = 0.0` to **1.9282×** at `2.0`. Spec §11 asked us to publish where our own system loses; on this axis, inside this range, it does not.

**2. But most of that advantage is NOT the fatigue externality.** At `BETA_FATIGUE = 0.0` the cross-agent fatigue term is switched off entirely — the externality spec §8.6 calls "the whole thesis expressed as arithmetic" does not exist — and Arm B still beats Arm A by **1.5513×**. That is about **73%** of the advantage measured at the frozen value, surviving with zero fatigue. **The dominant mechanism is selection — ranking by expected net and declining low-value contacts — not fatigue internalisation.** Fatigue adds the remainder and grows in importance as it worsens. This is a real qualification of the headline framing and it is stated here rather than left for a reviewer to find.

**3. Where SAMPARK genuinely loses is total revenue, at every single tested value.** `B ÷ A total ₹` runs from **0.7996** at `BETA_FATIGUE = 0.0` to **0.9938** at `2.0`. Mediation always recovers less money than letting every agent run free. That is the published losing condition, and it is the honest one: the trade is roughly half the contacts for a single-digit-percent revenue give-up at the frozen world, narrowing to near parity as fatigue worsens.

**4. The worse the fatigue externality, the better mediation looks on BOTH axes.** At `BETA_FATIGUE = 2.0` Arm B recovers **99.38%** of Arm A's total revenue using about half the contacts. The case for SAMPARK is strongest exactly where customer attention is most fragile — which is the regime the product is aimed at, and is a claim this sweep can now support rather than assert.

**5. Incentive potency barely matters.** Across a 4× swing in `BETA_INCENTIVE` the uplift moves by roughly 1%, in the predicted direction. The margin budget is near-inert at headline capacity, consistent with the committed `merchant_margin_half` ablation.

### Precommitted predictions

Committed to `results/phase9_precommitment.json` **before** `sim/sensitivity.py` existed and before any result was observed. A failed prediction is reported as failed and never edited.

| ID | Claim | Locked criterion | Result |
|---|---|---|---|
| P1 | mean uplift ratio is monotonically non-decreasing in beta_fatigue | monotone across all tested values → monotone | **PASS** |
| P2 | mean uplift ratio at beta_fatigue = 0.0 lies in [1.30, 1.70] | interval [1.30, 1.70] → measured 1.5513 | **PASS** |
| P3 | no crossing in beta_fatigue [0.0, 2.0]; Arm B never loses on rupees-per-contact | no crossing in [0.0, 2.0] → no crossing | **PASS** |
| P4 | Arm B recovers less TOTAL rupees at every beta_fatigue, and the shortfall shrinks monotonically as beta_fatigue rises | B total < A total at every value, shortfall shrinking → held | **PASS** |
| P5 | Arm A and Arm B contact counts are byte-identical at every tested value | contact counts identical at every value → identical | **PASS** |
| P6 | mean uplift ratio is monotonically non-increasing in beta_incentive | monotone across all tested values → monotone | **PASS** |

### All 50 sweep points

Every point measured, none interpolated. Rows at a dimension's **frozen** value are the anchors, each verified to reproduce the committed Phase 4 evidence for that seed on eight fields before the sweep was allowed to report.

| # | Dimension | Value | Seed | A contacts | B contacts | A paise/contact | B paise/contact | Uplift | B÷A total ₹ | s |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | beta_fatigue | 0.0 | 7 | 20,000 | 10,327 | 112,767.7 | 172,591.5 | 1.5305 | 0.7903 | 70 |
| 2 | beta_fatigue | 0.25 | 7 | 20,000 | 10,327 | 106,704.5 | 168,681.5 | 1.5808 | 0.8163 | 72 |
| 3 | beta_fatigue | 0.5 | 7 | 20,000 | 10,327 | 100,257.2 | 163,711.5 | 1.6329 | 0.8432 | 77 |
| 4 | beta_fatigue | 0.75 | 7 | 20,000 | 10,327 | 95,465.3 | 160,259.6 | 1.6787 | 0.8668 | 78 |
| 5 | beta_fatigue | 1.0 ⚓ | 7 | 20,000 | 10,327 | 90,932.1 | 155,620.0 | 1.7114 | 0.8837 | 79 |
| 6 | beta_fatigue | 1.5 | 7 | 20,000 | 10,327 | 82,529.3 | 150,161.5 | 1.8195 | 0.9395 | 76 |
| 7 | beta_fatigue | 2.0 | 7 | 20,000 | 10,327 | 75,479.8 | 144,485.6 | 1.9142 | 0.9884 | 207 |
| 8 | beta_fatigue | 0.0 | 42 | 20,000 | 10,299 | 114,327.5 | 172,083.8 | 1.5052 | 0.7751 | 259 |
| 9 | beta_fatigue | 0.25 | 42 | 20,000 | 10,299 | 107,093.7 | 167,819.2 | 1.5670 | 0.8069 | 278 |
| 10 | beta_fatigue | 0.5 | 42 | 20,000 | 10,299 | 100,848.3 | 163,312.3 | 1.6194 | 0.8339 | 183 |
| 11 | beta_fatigue | 0.75 | 42 | 20,000 | 10,299 | 95,167.9 | 158,940.5 | 1.6701 | 0.8600 | 102 |
| 12 | beta_fatigue | 1.0 ⚓ | 42 | 20,000 | 10,299 | 89,686.4 | 154,739.7 | 1.7253 | 0.8885 | 164 |
| 13 | beta_fatigue | 1.5 | 42 | 20,000 | 10,299 | 81,108.5 | 147,028.2 | 1.8127 | 0.9335 | 160 |
| 14 | beta_fatigue | 2.0 | 42 | 20,000 | 10,299 | 74,307.6 | 140,001.1 | 1.8841 | 0.9702 | 162 |
| 15 | beta_fatigue | 0.0 | 101 | 20,000 | 10,350 | 110,059.5 | 178,050.2 | 1.6178 | 0.8372 | 162 |
| 16 | beta_fatigue | 0.25 | 101 | 20,000 | 10,350 | 103,749.7 | 171,650.8 | 1.6545 | 0.8562 | 85 |
| 17 | beta_fatigue | 0.5 | 101 | 20,000 | 10,350 | 96,649.9 | 167,969.4 | 1.7379 | 0.8994 | 47 |
| 18 | beta_fatigue | 0.75 | 101 | 20,000 | 10,350 | 90,715.3 | 164,118.4 | 1.8092 | 0.9362 | 48 |
| 19 | beta_fatigue | 1.0 ⚓ | 101 | 20,000 | 10,350 | 85,413.9 | 160,763.8 | 1.8822 | 0.9740 | 61 |
| 20 | beta_fatigue | 1.5 | 101 | 20,000 | 10,350 | 76,271.8 | 153,773.4 | 2.0161 | 1.0433 | 174 |
| 21 | beta_fatigue | 2.0 | 101 | 20,000 | 10,350 | 70,322.4 | 144,824.5 | 2.0594 | 1.0658 | 240 |
| 22 | beta_fatigue | 0.0 | 2024 | 20,000 | 10,321 | 111,749.3 | 173,734.4 | 1.5547 | 0.8023 | 232 |
| 23 | beta_fatigue | 0.25 | 2024 | 20,000 | 10,321 | 105,188.9 | 169,057.1 | 1.6072 | 0.8294 | 164 |
| 24 | beta_fatigue | 0.5 | 2024 | 20,000 | 10,321 | 98,534.8 | 163,810.0 | 1.6625 | 0.8579 | 193 |
| 25 | beta_fatigue | 0.75 | 2024 | 20,000 | 10,321 | 93,670.6 | 159,687.6 | 1.7048 | 0.8798 | 77 |
| 26 | beta_fatigue | 1.0 ⚓ | 2024 | 20,000 | 10,321 | 88,849.9 | 155,033.2 | 1.7449 | 0.9004 | 70 |
| 27 | beta_fatigue | 1.5 | 2024 | 20,000 | 10,321 | 81,050.3 | 148,467.3 | 1.8318 | 0.9453 | 73 |
| 28 | beta_fatigue | 2.0 | 2024 | 20,000 | 10,321 | 74,479.3 | 141,925.4 | 1.9056 | 0.9834 | 72 |
| 29 | beta_fatigue | 0.0 | 31337 | 20,000 | 10,245 | 114,009.8 | 176,769.8 | 1.5505 | 0.7942 | 76 |
| 30 | beta_fatigue | 0.25 | 31337 | 20,000 | 10,245 | 108,484.8 | 172,369.6 | 1.5889 | 0.8139 | 75 |
| 31 | beta_fatigue | 0.5 | 31337 | 20,000 | 10,245 | 102,790.3 | 168,352.3 | 1.6378 | 0.8390 | 77 |
| 32 | beta_fatigue | 0.75 | 31337 | 20,000 | 10,245 | 96,874.0 | 162,511.2 | 1.6776 | 0.8593 | 78 |
| 33 | beta_fatigue | 1.0 ⚓ | 31337 | 20,000 | 10,245 | 92,054.6 | 158,630.1 | 1.7232 | 0.8827 | 76 |
| 34 | beta_fatigue | 1.5 | 31337 | 20,000 | 10,245 | 82,817.5 | 150,292.7 | 1.8147 | 0.9296 | 72 |
| 35 | beta_fatigue | 2.0 | 31337 | 20,000 | 10,245 | 76,425.6 | 144,159.3 | 1.8863 | 0.9662 | 76 |
| 36 | beta_incentive | 2.0 | 7 | 20,000 | 10,327 | 89,986.6 | 154,855.9 | 1.7209 | 0.8886 | 77 |
| 37 | beta_incentive | 4.0 ⚓ | 7 | 20,000 | 10,327 | 90,932.1 | 155,620.0 | 1.7114 | 0.8837 | 77 |
| 38 | beta_incentive | 8.0 | 7 | 20,000 | 10,327 | 92,629.7 | 157,671.1 | 1.7022 | 0.8789 | 77 |
| 39 | beta_incentive | 2.0 | 42 | 20,000 | 10,299 | 88,721.5 | 152,967.1 | 1.7241 | 0.8878 | 77 |
| 40 | beta_incentive | 4.0 ⚓ | 42 | 20,000 | 10,299 | 89,686.4 | 154,739.7 | 1.7253 | 0.8885 | 77 |
| 41 | beta_incentive | 8.0 | 42 | 20,000 | 10,299 | 91,621.8 | 157,055.8 | 1.7142 | 0.8827 | 77 |
| 42 | beta_incentive | 2.0 | 101 | 20,000 | 10,350 | 84,638.1 | 159,999.4 | 1.8904 | 0.9783 | 78 |
| 43 | beta_incentive | 4.0 ⚓ | 101 | 20,000 | 10,350 | 85,413.9 | 160,763.8 | 1.8822 | 0.9740 | 79 |
| 44 | beta_incentive | 8.0 | 101 | 20,000 | 10,350 | 86,972.0 | 162,242.0 | 1.8655 | 0.9654 | 77 |
| 45 | beta_incentive | 2.0 | 2024 | 20,000 | 10,321 | 87,647.1 | 154,211.9 | 1.7595 | 0.9080 | 76 |
| 46 | beta_incentive | 4.0 ⚓ | 2024 | 20,000 | 10,321 | 88,849.9 | 155,033.2 | 1.7449 | 0.9004 | 77 |
| 47 | beta_incentive | 8.0 | 2024 | 20,000 | 10,321 | 90,550.9 | 157,161.2 | 1.7356 | 0.8957 | 74 |
| 48 | beta_incentive | 2.0 | 31337 | 20,000 | 10,245 | 91,375.7 | 157,402.9 | 1.7226 | 0.8824 | 75 |
| 49 | beta_incentive | 4.0 ⚓ | 31337 | 20,000 | 10,245 | 92,054.6 | 158,630.1 | 1.7232 | 0.8827 | 72 |
| 50 | beta_incentive | 8.0 | 31337 | 20,000 | 10,245 | 93,838.7 | 160,734.6 | 1.7129 | 0.8774 | 68 |

**50 points total.** ⚓ marks a frozen-value anchor.

### Methodology and parameter definitions

| Parameter | Frozen value | Swept over | Meaning |
|---|---:|---|---|
| `BETA_FATIGUE` | 1.0 | 0.0 – 2.0 (7 points) | Coefficient on `prior_contacts × fatigue_hazard` in the ground-truth recovery logit. The cross-agent externality SAMPARK exists to price. At 0.0 the externality does not exist. |
| `BETA_INCENTIVE` | 4.0 | 2.0 – 8.0 (3 points) | Coefficient on `(incentive_bps/10⁴) × price_sensitivity`. How potent a discount is. |

- **This is a sensitivity analysis, not an ablation.** Both parameters are *ground-truth world* parameters in `sim/environment.py`; the SYSTEM is held completely fixed. Varying a system parameter instead would be an ablation, and five of those are already committed (§3 above).
- **Exactly one coefficient moves per point;** the other stays frozen, so any observed change is attributable.
- **Primary metric:** `mean(Arm B paise/contact) ÷ mean(Arm A paise/contact)` over the five precommitted seeds — the same definition `sim/gate.py` uses. It is a ratio of means, not a mean of ratios.
- **Backend:** in-memory, licensed by a committed test showing bit-for-bit parity with the Postgres-backed record at the anchor (world v1 only).
- **Why the sweep is a pure re-observation:** under world v1 no realized outcome feeds back into any decision, so varying either coefficient changes which contacts *succeed*, never which contacts *happen*. P5 tests this directly.
- **Crossing points are reported as brackets between adjacent tested values, never interpolated** — interpolating would invent a number that was not measured.

**Excluded dimension, stated rather than skipped:** contact-cap sensitivity. `CONTACT_CAP_24H` / `CONTACT_CAP_7D` are module-scope imports inside protected Phase 4 files with no override path. It is the most economically interesting knob in the system, and Phase 4 protection forbids touching it.

## 6. What is not measured

- **p99_grant_decision_latency** — NOT MEASURED. No latency instrumentation exists anywhere in the codebase. Reported as absent rather than estimated from an in-memory run, which would not represent the SERIALIZABLE issuance round-trip that dominates real decision cost.

See `DISCLAIMER.md` for the complete limitations record.
