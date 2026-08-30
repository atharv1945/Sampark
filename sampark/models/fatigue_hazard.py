"""Fatigue-hazard model — Phase 6 structural gate + Phase 7 real fit,
spec §18.1 / §8.6.

Spec §8.6 defines fatigue as a learned quantity:

    fatigue_cost = Delta P(opt_out | contact_history + this_contact) x customer_forward_value

That requires opt-out EVENTS as training labels. At Phase 6 close this
repository's generator produced no such labels at all:

  - `agents.types.ContactOutcome` (Arm A's complete outcome schema) had
    no opt-out-related field of any kind.
  - `sim/ledger.py` constructs every customer's `ContactState` with
    `optouts_by_channel={}`, always empty; nothing in `sim/population.py`
    or `sim/generator.py` ever populated it.

**Phase 7 (spec §8.9) changes the first fact, deliberately.**
`agents.types.ContactOutcome` now carries `opt_out` / `opt_out_channel`
(defaulted `False`/`None` — every pre-Phase-7 caller is unaffected), drawn
in `sim/environment.py::Environment.observe` under `world="v2"` only. This
means `detect_opt_out_labels()`'s STRUCTURAL check below now reports
`has_opt_out_labels=True` — it is a schema-capability check, never a
per-dataset volume check, exactly as its own docstring always said.

**What did NOT change:** `sim/arm_a.py::run_arm_a` (Phase 6's `load_training_rows`,
still called by `train_fatigue_hazard_model` below with no `fraction`
argument) builds its `Environment` at the default `world="v1"` — it NEVER
draws an opt-out label, for any seed, ever. So `train_fatigue_hazard_model(seed)`
(Phase 6's original, unchanged entry point) still correctly reports
`available=False`, but now for a DIFFERENT, more precise reason: the
labels are structurally possible but this specific (world-v1) dataset has
ZERO positive labels, not (as Phase 6 originally worded it) "no seed's log
can ever pass the check." `sampark/models/artifact_data.py` (the committed
Phase 6 artifact) is regenerated to reflect the updated, accurate reason
text — `FATIGUE_HAZARD_AVAILABLE` itself stays `False`, so no ablation's
numeric result changes.

`train_fatigue_hazard_model_holdout(seed, fraction)` is the real Phase 7
entry point: it reads `sim.arm_a_holdout.run_arm_a_holdout`'s (world="v2")
real opt-out labels, and fits a HIERARCHICAL, SHRUNK hazard estimate —
never the anti-conservative `hazard.get(key, 0.0)` `sampark/models/scorer.py`
used to fall back to for an unseen bucket (Phase 7 design lock, Part 7.2):
an unseen bucket priced at zero fatigue cost would be the MOST attractive
candidate to contact, which is backwards. `(source, root_cause, n)` falls
back to `(source, n)`, then to `(n)` — each level shrunk toward its
parent's rate (`_SHRINKAGE_ALPHA` pseudo-count) — with the `(n)` level
made TOTAL over the only two contact indices the allocator can ever query
under the frozen caps (`CONTACT_CAP_24H=1`, `CONTACT_CAP_7D=2` ->
`n in {0, 1}`), so `available=True` never ships a scorer that could still
hit an undefined bucket at runtime.
"""

from __future__ import annotations

import collections
import dataclasses
from dataclasses import dataclass
from typing import Sequence

from agents.types import ContactOutcome
from sampark.contracts import ContactState
from sampark.models.training_data import TrainingRow, TreatmentArm, load_training_rows_with_holdout


@dataclass(frozen=True)
class OptOutLabelReport:
    contact_outcome_fields: tuple[str, ...]
    contact_state_optout_field_present: bool
    has_opt_out_labels: bool
    reason: str


def detect_opt_out_labels() -> OptOutLabelReport:
    """Structural check, not a data scan: does `ContactOutcome` carry an
    opt-out-related field AT ALL, checked against the live dataclass
    rather than trusted from a comment. As of Phase 7 this is `True` —
    `opt_out` / `opt_out_channel` exist. Whether a SPECIFIC dataset
    contains any positive label is a separate, data-dependent question,
    answered by `detect_opt_out_label_volume` below, never by this
    function (kept deliberately dataset-agnostic, matching its original
    Phase 6 contract)."""
    outcome_fields = tuple(f.name for f in dataclasses.fields(ContactOutcome))
    has_outcome_field = any("opt_out" in name or "optout" in name for name in outcome_fields)

    contact_state_fields = tuple(ContactState.model_fields.keys())
    has_contact_state_field = any(
        "opt_out" in name or "optout" in name for name in contact_state_fields
    )

    if has_outcome_field:
        reason = (
            f"agents.types.ContactOutcome carries opt-out field(s) (fields: {outcome_fields!r}); "
            "labels are structurally available (Phase 7, spec §8.9). Whether a specific "
            "seed/world/fraction actually contains positive labels is a separate, "
            "data-dependent question — see detect_opt_out_label_volume."
        )
    else:
        reason = (
            "agents.types.ContactOutcome carries no opt-out-related field "
            f"(fields: {outcome_fields!r}); "
            + (
                "sampark.contracts.ContactState.optouts_by_channel exists on the contract "
                "but sim/ledger.py constructs every ContactState with optouts_by_channel={} "
                "and no generator code ever writes to it, so it carries no real signal either."
                if has_contact_state_field
                else "sampark.contracts.ContactState also carries no such field."
            )
        )

    return OptOutLabelReport(
        contact_outcome_fields=outcome_fields,
        contact_state_optout_field_present=has_contact_state_field,
        has_opt_out_labels=has_outcome_field,
        reason=reason,
    )


@dataclass(frozen=True)
class FatigueHazardModel:
    """A fitted binary hazard classifier: P(opt_out | features) per
    (source, root_cause, contact_index-bucket). Lookup-table shaped for
    the same auditability reason `UpliftModel` is. UNCHANGED since Phase
    6 — `fit_fatigue_hazard_model` below still fits a plain, non-hierarchical
    empirical rate per bucket. Phase 7's hierarchical/shrunk resolution
    (see `fit_fatigue_hazard_model_holdout`) produces a `FatigueHazardModel`
    too, but with its `hazard_by_bucket` ALREADY fully resolved through
    the fallback chain — `predict_hazard` itself never changes."""

    hazard_by_bucket: dict[tuple[str, str, int], float]

    def predict_hazard(self, source: str, root_cause: str, contact_index: int) -> float:
        key = (source, root_cause, contact_index)
        if key not in self.hazard_by_bucket:
            raise KeyError(f"no fitted hazard for bucket {key!r}")
        return self.hazard_by_bucket[key]


def fit_fatigue_hazard_model(
    rows: Sequence[TrainingRow],
    opted_out_labels: Sequence[bool],
) -> FatigueHazardModel:
    """Generic fit: `opted_out_labels[i]` is whether `rows[i]`'s contact
    was followed by an opt-out (caller-supplied — this function makes no
    assumption about what produced the label, real or synthetic-for-test).
    Empirical hazard rate per (source, root_cause, contact_index). NO
    fallback, no shrinkage — a bucket with zero observations simply does
    not appear in `hazard_by_bucket`. UNCHANGED since Phase 6."""
    if len(rows) != len(opted_out_labels):
        raise ValueError(
            f"rows and opted_out_labels must be the same length, got {len(rows)} and {len(opted_out_labels)}"
        )

    totals: dict[tuple[str, str, int], list[int]] = {}
    for row, opted_out in zip(rows, opted_out_labels):
        key = (row.source, row.root_cause, row.contact_index)
        bucket = totals.setdefault(key, [0, 0])
        bucket[0] += 1
        bucket[1] += 1 if opted_out else 0

    return FatigueHazardModel(
        hazard_by_bucket={key: recovered / obs for key, (obs, recovered) in totals.items() if obs > 0}
    )


@dataclass(frozen=True)
class FatigueHazardModelResult:
    available: bool
    reason: str | None
    report: OptOutLabelReport
    model: FatigueHazardModel | None = None


def train_fatigue_hazard_model(seed: int) -> FatigueHazardModelResult:
    """The Phase 6 entry point `sampark.models.artifact.build_model_artifact`
    calls when `fraction == 0` (i.e. always, for the committed
    `sampark/models/artifact_data.py`). `seed` selects Arm A's log via
    `sim.arm_a.run_arm_a` — built at `world="v1"` (unconditionally, that
    function's own frozen default), which NEVER draws an opt-out label.
    So even though `detect_opt_out_labels()` now reports the labels are
    STRUCTURALLY available (Phase 7), this specific (world-v1) dataset
    has exactly zero positive labels for any seed — checked directly
    below, never assumed."""
    report = detect_opt_out_labels()
    if not report.has_opt_out_labels:
        return FatigueHazardModelResult(available=False, reason=report.reason, report=report, model=None)

    from sampark.models.training_data import load_training_rows

    rows = load_training_rows(seed)
    n_positive = sum(1 for r in rows if r.opt_out)
    if n_positive == 0:
        reason = (
            f"agents.types.ContactOutcome carries opt_out (structurally available), but "
            f"sim.arm_a.run_arm_a(seed={seed}) builds its Environment at world='v1' "
            "(that function's own frozen default), which never draws an opt-out label — "
            "0 positive labels observed across all "
            f"{len(rows)} Arm A rows. A real opt-out signal requires world='v2' "
            "(spec section 8.9, Phase 7) — see train_fatigue_hazard_model_holdout."
        )
        return FatigueHazardModelResult(available=False, reason=reason, report=report, model=None)

    # Unreachable via this entry point (Arm A is always world="v1"), kept
    # only so a direct call with a hand-built world-v2 row set is still
    # honestly handled rather than silently returning available=False for
    # the wrong reason.
    labels = [r.opt_out for r in rows]
    model = fit_fatigue_hazard_model(rows, labels)
    return FatigueHazardModelResult(available=True, reason=None, report=report, model=model)


# =============================================================================
# Phase 7 — hierarchical, shrunk fit against real world-v2 opt-out labels
# (spec §8.9). A SEPARATE path from train_fatigue_hazard_model above; that
# function and sampark/models/artifact_data.py (the committed Phase 6
# artifact) are unaffected by anything below this line.
# =============================================================================

_MIN_OBS_PER_BUCKET = 200
_MIN_POSITIVES_PER_BUCKET = 20
_SHRINKAGE_ALPHA = 30  # pseudo-count, mirroring sim/calibration.py's own precedent class

# The allocator only ever queries n in {0, 1} under the frozen caps
# (CONTACT_CAP_24H=1, CONTACT_CAP_7D=2 — sampark/allocator/constants.py).
# Making the (n) fallback level TOTAL over exactly this two-element domain
# is what makes availability structural rather than a runtime gamble.
QUERYABLE_CONTACT_INDICES: tuple[int, ...] = (0, 1)

LEVEL_SOURCE_ROOT_CAUSE = "source_root_cause"
LEVEL_SOURCE = "source"
LEVEL_GLOBAL = "global"


@dataclass(frozen=True)
class HierarchicalFatigueHazardResult:
    available: bool
    reason: str | None
    model: FatigueHazardModel | None
    fallback_level_by_bucket: dict[tuple[str, str, int], str]
    n_obs_by_bucket: dict[tuple[str, str, int], int]
    n_positives_by_bucket: dict[tuple[str, str, int], int]


def _counts(rows: Sequence[TrainingRow], key_fn) -> tuple[dict, dict]:
    obs: dict = collections.defaultdict(int)
    pos: dict = collections.defaultdict(int)
    for row in rows:
        key = key_fn(row)
        obs[key] += 1
        pos[key] += 1 if row.opt_out else 0
    return dict(obs), dict(pos)


def fit_fatigue_hazard_model_holdout(rows: Sequence[TrainingRow]) -> HierarchicalFatigueHazardResult:
    """`rows` must be TREATED rows carrying real `opt_out` labels (i.e.
    from `load_training_rows_with_holdout(seed, fraction)` with
    `fraction > 0` and `world="v2"` — the caller's responsibility, not
    re-verified here beyond using only `TreatmentArm.TREATED` rows: a
    HOLDOUT row is never contacted and therefore can never carry a real
    opt-out draw — Phase 7 design lock §2.1's `p_optout = 0` with no
    contact property, enforced upstream by `TrainingRow.__post_init__`.

    Resolves hazard for EVERY `(source, root_cause, n)` in the queryable
    domain (every source x every root_cause actually observed x
    `QUERYABLE_CONTACT_INDICES`), via a hierarchical, shrunk chain:

        (source, root_cause, n)  shrunk toward (source, n)'s rate
              -> if under floor, (source, n)  shrunk toward (n)'s rate
              -> if under floor, (n)  — must itself clear the floor,
                 globally, or the WHOLE model reports unavailable
                 (Phase 6's all-or-nothing artifact discipline,
                 ModelArtifact.is_valid_for_scoring(), preserved here:
                 a model that silently omits a queryable bucket is not
                 shipped at all).

    The result is a REGULAR `FatigueHazardModel` whose `hazard_by_bucket`
    is fully resolved — `predict_hazard` needs no fallback logic of its
    own, and `sampark/models/scorer.py::ModelBackedScorer` no longer
    needs (or has) a `.get(key, 0.0)` default."""
    treated = [r for r in rows if r.treatment_arm is TreatmentArm.TREATED]

    obs_src_rc_n, pos_src_rc_n = _counts(treated, lambda r: (r.source, r.root_cause, r.contact_index))
    obs_src_n, pos_src_n = _counts(treated, lambda r: (r.source, r.contact_index))
    obs_n, pos_n = _counts(treated, lambda r: r.contact_index)

    def _rate(pos: int, obs: int) -> float | None:
        return pos / obs if obs > 0 else None

    def _shrunk(pos: int, obs: int, parent_rate: float | None) -> float | None:
        if parent_rate is None:
            return None
        return (pos + _SHRINKAGE_ALPHA * parent_rate) / (obs + _SHRINKAGE_ALPHA)

    # Global (n)-level rate must itself clear the floor for the model to
    # be usable at all — it is the guaranteed-total terminal level.
    global_ok = all(
        obs_n.get(n, 0) >= _MIN_OBS_PER_BUCKET and pos_n.get(n, 0) >= _MIN_POSITIVES_PER_BUCKET
        for n in QUERYABLE_CONTACT_INDICES
    )
    if not global_ok:
        under = {
            n: (obs_n.get(n, 0), pos_n.get(n, 0))
            for n in QUERYABLE_CONTACT_INDICES
            if obs_n.get(n, 0) < _MIN_OBS_PER_BUCKET or pos_n.get(n, 0) < _MIN_POSITIVES_PER_BUCKET
        }
        return HierarchicalFatigueHazardResult(
            available=False,
            reason=(
                f"global (n)-level opt-out volume falls below the floor "
                f"(n_obs>={_MIN_OBS_PER_BUCKET}, n_positives>={_MIN_POSITIVES_PER_BUCKET}) for at least "
                f"one queryable contact index (n -> (n_obs, n_positives)): {under!r} — "
                "the terminal fallback level would not be total, so no model is shipped"
            ),
            model=None,
            fallback_level_by_bucket={},
            n_obs_by_bucket=obs_src_rc_n,
            n_positives_by_bucket=pos_src_rc_n,
        )

    buckets = sorted({(source, root_cause) for source, root_cause, _n in obs_src_rc_n})
    hazard_by_bucket: dict[tuple[str, str, int], float] = {}
    level_by_bucket: dict[tuple[str, str, int], str] = {}

    for source, root_cause in buckets:
        for n in QUERYABLE_CONTACT_INDICES:
            key3 = (source, root_cause, n)
            key2 = (source, n)

            src_n_rate = _rate(pos_src_n.get(key2, 0), obs_src_n.get(key2, 0))
            n_rate = _rate(pos_n.get(n, 0), obs_n.get(n, 0))  # guaranteed defined — global_ok checked above

            obs3, pos3 = obs_src_rc_n.get(key3, 0), pos_src_rc_n.get(key3, 0)
            obs2, pos2 = obs_src_n.get(key2, 0), pos_src_n.get(key2, 0)

            if obs3 >= _MIN_OBS_PER_BUCKET and pos3 >= _MIN_POSITIVES_PER_BUCKET:
                hazard_by_bucket[key3] = _shrunk(pos3, obs3, src_n_rate) if src_n_rate is not None else _rate(pos3, obs3)
                level_by_bucket[key3] = LEVEL_SOURCE_ROOT_CAUSE
            elif obs2 >= _MIN_OBS_PER_BUCKET and pos2 >= _MIN_POSITIVES_PER_BUCKET:
                hazard_by_bucket[key3] = _shrunk(pos2, obs2, n_rate)
                level_by_bucket[key3] = LEVEL_SOURCE
            else:
                hazard_by_bucket[key3] = n_rate
                level_by_bucket[key3] = LEVEL_GLOBAL

    model = FatigueHazardModel(hazard_by_bucket=hazard_by_bucket)
    return HierarchicalFatigueHazardResult(
        available=True,
        reason=None,
        model=model,
        fallback_level_by_bucket=level_by_bucket,
        n_obs_by_bucket=obs_src_rc_n,
        n_positives_by_bucket=pos_src_rc_n,
    )


def train_fatigue_hazard_model_holdout(seed: int, fraction: float) -> HierarchicalFatigueHazardResult:
    """The Phase 7 entry point `sampark.models.artifact.build_model_artifact`
    calls when `fraction > 0`. Reads real world-v2 opt-out labels from
    `sim.arm_a_holdout.run_arm_a_holdout` (via `load_training_rows_with_holdout`)
    and fits the hierarchical, shrunk model above. `fraction=0.0` produces
    zero opt-out draws (world="v2" is still used by `run_arm_a_holdout`
    unconditionally, but with an empty holdout every candidate is
    contacted exactly as Arm A would — so real labels DO exist even at
    fraction=0.0, unlike the uplift model's control population, which is
    genuinely empty at fraction=0.0)."""
    rows = load_training_rows_with_holdout(seed, fraction)
    return fit_fatigue_hazard_model_holdout(rows)
