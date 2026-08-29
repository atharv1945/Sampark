"""ModelArtifact — validity gating and the committed-artifact loader."""

from __future__ import annotations

import pytest

from sampark.models.artifact import (
    CommittedArtifactUnavailableError,
    ModelArtifact,
    build_model_artifact,
    load_committed_artifact,
)
from sampark.models.fatigue_hazard import FatigueHazardModel
from sampark.models.uplift import UpliftModel


def test_build_model_artifact_on_seed_42_is_honestly_invalid_for_scoring():
    artifact = build_model_artifact(seed=42)
    assert artifact.uplift_available is False
    assert artifact.fatigue_hazard_available is False
    assert artifact.is_valid_for_scoring() is False


def test_build_model_artifact_is_deterministic():
    a = build_model_artifact(seed=42)
    b = build_model_artifact(seed=42)
    assert a == b


def test_is_valid_for_scoring_requires_both_components():
    fully_available = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(treated_response_by_bucket={}, control_response_by_bucket={}),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(hazard_by_bucket={}),
    )
    assert fully_available.is_valid_for_scoring() is True

    only_uplift = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(treated_response_by_bucket={}, control_response_by_bucket={}),
        fatigue_hazard_available=False, fatigue_hazard_reason="no labels",
        fatigue_hazard_model=None,
    )
    assert only_uplift.is_valid_for_scoring() is False

    claims_available_but_no_model = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None, uplift_model=None,
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(hazard_by_bucket={}),
    )
    assert claims_available_but_no_model.is_valid_for_scoring() is False


def test_load_committed_artifact_reads_the_real_generated_module():
    """Exercises the actual committed sampark/models/artifact_data.py --
    proves the generated file and the loader agree on shape."""
    artifact = load_committed_artifact()
    assert artifact.seed == 42
    assert isinstance(artifact.uplift_available, bool)
    assert isinstance(artifact.fatigue_hazard_available, bool)


def test_load_committed_artifact_raises_on_missing_module(monkeypatch):
    # The standard way to simulate "module cannot be imported" without
    # touching the real file on disk: sys.modules[name] = None makes the
    # import machinery raise ImportError for that exact name.
    import sys

    monkeypatch.delitem(sys.modules, "sampark.models.artifact_data", raising=False)
    monkeypatch.setitem(sys.modules, "sampark.models.artifact_data", None)
    with pytest.raises(CommittedArtifactUnavailableError):
        load_committed_artifact()
