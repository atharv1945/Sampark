"""The precommitment must stay binding — Phase 9A.

`results/phase9_precommitment.json` was committed (982c53e; eabdbd1 before the
co-author-trailer rewrite, same tree) BEFORE
`sim/sensitivity.py` existed and before any result was observed. Its value is
entirely in that ordering, and that value evaporates if the grid in the code
can quietly drift away from the grid in the committed file once results are in.

These tests make the drift impossible without a visibly failing test.
"""

from __future__ import annotations

import json
from pathlib import Path

from sim.environment import BETA_FATIGUE, BETA_INCENTIVE
from sim.sensitivity import (
    ANCHORS,
    BETA_FATIGUE_VALUES,
    BETA_INCENTIVE_VALUES,
    DIMENSION_BETA_FATIGUE,
    DIMENSION_BETA_INCENTIVE,
    DIMENSIONS,
    FINAL_SEEDS,
)

_PRECOMMITMENT = Path(__file__).resolve().parents[2] / "results" / "phase9_precommitment.json"


def _precommitment() -> dict:
    return json.loads(_PRECOMMITMENT.read_text(encoding="utf-8"))


def test_precommitment_file_exists():
    assert _PRECOMMITMENT.exists(), "the Phase 9 precommitment must remain committed"


def test_grids_match_the_precommitted_file():
    by_id = {d["id"]: d for d in _precommitment()["dimensions"]}
    assert list(BETA_FATIGUE_VALUES) == by_id[DIMENSION_BETA_FATIGUE]["values"]
    assert list(BETA_INCENTIVE_VALUES) == by_id[DIMENSION_BETA_INCENTIVE]["values"]


def test_anchors_match_both_the_precommitment_and_the_frozen_constants():
    by_id = {d["id"]: d for d in _precommitment()["dimensions"]}
    assert ANCHORS[DIMENSION_BETA_FATIGUE] == by_id[DIMENSION_BETA_FATIGUE]["frozen_value"] == BETA_FATIGUE
    assert ANCHORS[DIMENSION_BETA_INCENTIVE] == by_id[DIMENSION_BETA_INCENTIVE]["frozen_value"] == BETA_INCENTIVE


def test_every_grid_contains_its_own_anchor():
    """A grid that excluded its frozen value would have no point at which the
    sweep could be checked against committed Phase 4 evidence."""
    for dimension, values in DIMENSIONS.items():
        assert ANCHORS[dimension] in values, dimension


def test_seeds_match_the_precommitment_and_the_gate():
    from sim.gate import FINAL_SEEDS as GATE_SEEDS

    assert list(FINAL_SEEDS) == _precommitment()["seeds"]
    assert FINAL_SEEDS == GATE_SEEDS


def test_precommitment_records_six_predictions_with_falsifiers():
    p = _precommitment()
    assert [x["id"] for x in p["predictions"]] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    assert p["falsifiers"], "a prediction with no stated falsifier is not falsifiable"


def test_contact_cap_exclusion_is_recorded():
    """The most economically interesting knob is excluded by Phase 4
    protection. That exclusion must stay on the record rather than being
    quietly worked around later."""
    excluded = _precommitment()["excluded_dimensions"]
    assert any("CONTACT_CAP" in e["parameter"] for e in excluded)
    assert any("protected" in e["excluded_because"].lower() for e in excluded)
