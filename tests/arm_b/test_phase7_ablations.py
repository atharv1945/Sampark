"""Phase 7's three new evidence-CLI ablations: `phase7_heuristic`,
`phase7_model`, `phase7_model_uplift`. No Postgres and no full seed run
here — these test the CLI's own wiring, mirroring
tests/arm_b/test_phase6_ablations.py's exact pattern for the same
reason: the wiring is cheap and deterministic to verify directly; the
real evidence run is a separate, expensive Postgres operation.

All three run the STANDARD (non-holdout) sim.arm_b.run_arm_b — they
test the SCORER seam against the Phase 7 committed model artifact, the
same way phase6_heuristic/phase6_model test it against the Phase 6
artifact. Introducing an actual holdout into an official-CLI run is a
SEPARATE mechanism (sim.arm_b.run_arm_b_holdout / sim.phase7_evidence),
deliberately not conflated with this CLI's ablation set.
"""

from __future__ import annotations

from sampark.allocator.scorer import HeuristicScorer
from sim.arm_b_cli import (
    ABLATIONS,
    HEADLINE,
    PHASE6_HEURISTIC,
    PHASE6_MODEL,
    PHASE7_HEURISTIC,
    PHASE7_MODEL,
    PHASE7_MODEL_UPLIFT,
    _ablation_params,
    _result_path,
)


def test_phase7_ablations_are_registered():
    assert PHASE7_HEURISTIC in ABLATIONS
    assert PHASE7_MODEL in ABLATIONS
    assert PHASE7_MODEL_UPLIFT in ABLATIONS


def test_phase7_heuristic_constructs_a_heuristic_scorer():
    params = _ablation_params(PHASE7_HEURISTIC)
    assert isinstance(params["scorer"], HeuristicScorer)
    assert "aging_bonus_paise" not in params
    assert "merchant_budget_paise_per_window" not in params
    assert "fifo_mode" not in params


def test_phase7_model_falls_back_to_heuristic_on_this_dataset():
    """The real, honest result on the committed Phase 7 artifact
    (seed 42, f=0.10): uplift is unavailable (bucket-floor gate), so
    the all-or-nothing rule falls back to HeuristicScorer even though
    fatigue-hazard alone is available."""
    params = _ablation_params(PHASE7_MODEL)
    assert isinstance(params["scorer"], HeuristicScorer)


def test_phase7_model_uplift_also_falls_back_on_this_dataset():
    """Same artifact, different p_hat_mode — still unavailable, for the
    identical reason (uplift itself never fit)."""
    params = _ablation_params(PHASE7_MODEL_UPLIFT)
    assert isinstance(params["scorer"], HeuristicScorer)


def test_phase7_ablations_write_to_new_files_never_colliding_with_phase4_or_6():
    phase4_paths = {
        _result_path(42, HEADLINE),
        _result_path(42, PHASE6_HEURISTIC),
        _result_path(42, PHASE6_MODEL),
    }
    for ablation in (PHASE7_HEURISTIC, PHASE7_MODEL, PHASE7_MODEL_UPLIFT):
        path = _result_path(42, ablation)
        assert path not in phase4_paths
        assert ablation in path.name


def test_headline_still_returns_no_scorer_key_after_phase7_additions():
    """The regression guard, re-run for THIS session's addition: headline
    must still gain no new key from adding three more ablation labels."""
    assert "scorer" not in _ablation_params(HEADLINE)


def test_all_nine_ablations_have_a_note():
    from sim.arm_b_cli import _ABLATION_NOTES

    for ablation in ABLATIONS:
        assert ablation in _ABLATION_NOTES
        assert len(_ABLATION_NOTES[ablation]) > 0
