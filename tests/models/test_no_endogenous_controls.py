"""The anti-inflation guard — Phase 7 design lock, Decision 15's hard
restriction: a `TrainingRow.treatment_arm == HOLDOUT` may come ONLY from a
risk item belonging to a `sim.holdout.assign()`-selected customer. An
allocator-declined-but-not-held-out item must NEVER become a control —
doing so would let the allocator's own selection skill masquerade as
causal uplift, since declined items are systematically lower-value.

`load_training_rows_with_holdout` only ever constructs `HOLDOUT` rows from
`ArmAHoldoutResult.natural_outcomes`, and for Arm A-H specifically every
natural outcome's customer IS held out (proven in
tests/sim_arm_a_holdout/test_arm_a_holdout.py). This test proves the
INVARIANT directly, at real scale, rather than trusting that proof by
construction: every HOLDOUT row's customer_id is a member of the ACTUAL
holdout set `sim.holdout.assign()` computes independently.
"""

from __future__ import annotations

import ast
import inspect

from sampark.models.training_data import TreatmentArm, load_training_rows_with_holdout
from sim.cli import build_dataset
from sim.holdout import assign, customer_amounts_from_risk_items

SEED = 42


def test_every_holdout_row_customer_is_in_the_real_holdout_set():
    _population, _signals, ledger = build_dataset(SEED)
    amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    real_holdout = assign(SEED, 0.10, amounts)

    rows = load_training_rows_with_holdout(SEED, 0.10)
    holdout_rows = [r for r in rows if r.treatment_arm is TreatmentArm.HOLDOUT]
    assert len(holdout_rows) > 0  # precondition: the mechanism actually produced control rows

    for row in holdout_rows:
        assert row.customer_id in real_holdout


def test_no_treated_row_customer_is_in_the_holdout_set():
    """The complement: a TREATED row's customer must never be held out —
    if it were, that customer's contact would have been filtered, and it
    could never have produced a ContactOutcome in the first place."""
    _population, _signals, ledger = build_dataset(SEED)
    amounts = customer_amounts_from_risk_items(ledger.risk_items, ledger.risk_customer_map)
    real_holdout = assign(SEED, 0.10, amounts)

    rows = load_training_rows_with_holdout(SEED, 0.10)
    treated_rows = [r for r in rows if r.treatment_arm is TreatmentArm.TREATED]
    for row in treated_rows:
        assert row.customer_id not in real_holdout


def test_zero_fraction_produces_zero_holdout_rows():
    rows = load_training_rows_with_holdout(SEED, 0.0)
    assert all(r.treatment_arm is TreatmentArm.TREATED for r in rows)


def test_holdout_row_shape_is_enforced_by_construction():
    """A HOLDOUT TrainingRow with a non-null channel or a nonzero
    incentive is unconstructable — __post_init__ raises rather than
    silently accepting a row that would misrepresent a never-contacted
    item as having contact-specific attributes."""
    import pytest

    from sampark.models.training_data import TrainingRow

    with pytest.raises(ValueError):
        TrainingRow(
            agent_id=None, customer_id="c", risk_id="r", source="s", root_cause="rc",
            channel="whatsapp",  # <- violates the HOLDOUT shape
            incentive_bps=0, amount_paise=100, contact_index=0,
            recovered=False, amount_recovered_paise=0, incentive_paise=0,
            treatment_arm=TreatmentArm.HOLDOUT,
        )


def test_treatment_arm_is_a_closed_two_member_enum():
    """No third member (e.g. an "UNCONTACTED" proxy) can ever be added
    without this test failing — the closed set IS the safety mechanism
    the design lock names."""
    assert {member.name for member in TreatmentArm} == {"TREATED", "HOLDOUT"}


def test_training_data_module_never_reads_allocator_or_policy_internals():
    """AST guard: sampark.models.training_data must never import
    sampark.allocator.greedy, sampark.mediation, or sampark.policy —
    the allocator's OWN admission/denial judgement must be structurally
    unreachable from this module, which is what makes "a HOLDOUT row can
    only come from sim.holdout" a guarantee rather than a convention."""
    import sampark.models.training_data as td

    tree = ast.parse(inspect.getsource(td))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden_substrings = ("sampark.allocator", "sampark.mediation", "sampark.policy")
    for forbidden in forbidden_substrings:
        assert not any(forbidden in name for name in imported), (
            f"sampark.models.training_data must never import {forbidden}; found {imported}"
        )
