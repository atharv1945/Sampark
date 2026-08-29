"""Fatigue-hazard model — Phase 6, spec §18.1 / §8.6.

Spec §8.6 defines fatigue as a learned quantity:

    fatigue_cost = Delta P(opt_out | contact_history + this_contact) x customer_forward_value

That requires opt-out EVENTS as training labels — some contacts that
were followed by the customer opting out, and some that were not. This
repository's generator produces no such labels at all:

  - `agents.types.ContactOutcome` (Arm A's complete outcome schema) has
    no opt-out-related field of any kind — checked structurally below,
    not just asserted.
  - `sim/ledger.py` constructs every customer's `ContactState` with
    `optouts_by_channel={}`, always empty, for every seed. Nothing in
    `sim/population.py` or `sim/generator.py` ever populates it.
  - `sim/mediation_metrics.py` already documents this: its
    `post_optout_contacts` metric is hardcoded to `None`, "not
    measurable — no opt-out data in this dataset."

`detect_opt_out_labels` checks the first two facts directly against the
live dataclass/module rather than trusting the comment in
`sim/mediation_metrics.py` to still be true. `train_fatigue_hazard_model`
refuses to fit anything once the check fails — it does not substitute a
proxy label (e.g. "did this customer receive no further contact",
which conflates opt-out with simply having no more open risk items) to
produce a number, because that number would not be measuring what its
name claims.

`fit_fatigue_hazard_model` is real, tested infrastructure (binary
hazard classification: opted-out-after-this-contact vs not, over the
same feature set the uplift model uses) — see
`tests/models/test_fatigue_hazard.py`, which exercises it against
synthetic labeled data. It is not reachable from
`train_fatigue_hazard_model` on this dataset.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Sequence

from agents.types import ContactOutcome
from sampark.contracts import ContactState
from sampark.models.training_data import TrainingRow


@dataclass(frozen=True)
class OptOutLabelReport:
    contact_outcome_fields: tuple[str, ...]
    contact_state_optout_field_present: bool
    has_opt_out_labels: bool
    reason: str


def detect_opt_out_labels() -> OptOutLabelReport:
    """Structural check, not a data scan: `ContactOutcome` (what Arm A's
    log actually is) is inspected field-by-field for anything opt-out
    related. `ContactState.optouts_by_channel` existing on the CONTRACT
    is not sufficient by itself -- `sim/ledger.py` never populates it,
    so this function also states that gap explicitly rather than being
    satisfied by the field's mere presence in the schema."""
    outcome_fields = tuple(f.name for f in dataclasses.fields(ContactOutcome))
    has_outcome_field = any("opt_out" in name or "optout" in name for name in outcome_fields)

    contact_state_fields = tuple(ContactState.model_fields.keys())
    has_contact_state_field = any(
        "opt_out" in name or "optout" in name for name in contact_state_fields
    )

    has_labels = has_outcome_field  # ContactState's field is never populated (see below) even if present
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
        has_opt_out_labels=has_labels,
        reason=reason,
    )


@dataclass(frozen=True)
class FatigueHazardModel:
    """A fitted binary hazard classifier: P(opt_out | features) per
    (source, root_cause, contact_index-bucket). Lookup-table shaped for
    the same auditability reason `UpliftModel` is."""

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
    Empirical hazard rate per (source, root_cause, contact_index)."""
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
    """The one entry point `sampark.models.artifact.build_model_artifact`
    calls. `seed` is accepted (and unused beyond the report) for
    interface symmetry with `train_uplift_model` -- the check here is
    structural (does the schema/generator carry the label at all), not
    per-seed data-volume dependent, so no seed's log can pass it."""
    report = detect_opt_out_labels()
    if not report.has_opt_out_labels:
        return FatigueHazardModelResult(available=False, reason=report.reason, report=report, model=None)

    # Unreachable today -- see module docstring. Kept as real, tested
    # infrastructure (tests/models/test_fatigue_hazard.py) for the day
    # opt-out events exist in the generator's output.
    raise NotImplementedError(
        "opt-out labels were detected but no real label source is wired up yet -- "
        "this branch requires an explicit design decision about where such labels "
        "would come from before it can be implemented"
    )
