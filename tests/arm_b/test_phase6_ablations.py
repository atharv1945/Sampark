"""Phase 6's two new evidence-CLI ablations: `phase6_heuristic` and
`phase6_model`. No Postgres and no full seed run here — these test the
CLI's own wiring (which scorer each ablation constructs, and that
neither collides with a Phase 4 result filename), not the evidence
itself (that's the real `sim/arm_b_cli.py --ablation phase6_model`
run against seed data, done separately).
"""

from __future__ import annotations

from sampark.allocator.scorer import HeuristicScorer
from sim.arm_b_cli import (
    ABLATIONS,
    HEADLINE,
    PHASE6_HEURISTIC,
    PHASE6_MODEL,
    _ablation_params,
    _result_path,
)


def test_phase6_ablations_are_registered():
    assert PHASE6_HEURISTIC in ABLATIONS
    assert PHASE6_MODEL in ABLATIONS


def test_phase6_heuristic_constructs_a_heuristic_scorer():
    params = _ablation_params(PHASE6_HEURISTIC)
    assert isinstance(params["scorer"], HeuristicScorer)
    # Every OTHER ablation parameter must be absent -- Design Lock §14.4's
    # "identical code" rule, extended: this ablation changes ONLY which
    # scorer is used.
    assert "aging_bonus_paise" not in params
    assert "merchant_budget_paise_per_window" not in params
    assert "fifo_mode" not in params


def test_phase6_model_falls_back_to_heuristic_on_this_dataset():
    """The real, honest result: build_scorer() against the committed
    artifact (both models unavailable) returns HeuristicScorer, so
    phase6_model's scorer is ALSO a HeuristicScorer today."""
    params = _ablation_params(PHASE6_MODEL)
    assert isinstance(params["scorer"], HeuristicScorer)


def test_phase6_ablations_write_to_new_files_not_headline():
    for ablation in (PHASE6_HEURISTIC, PHASE6_MODEL):
        path = _result_path(42, ablation)
        assert path != _result_path(42, HEADLINE)
        assert ablation in path.name


def test_headline_still_returns_no_scorer_key():
    """Headline (every pre-Phase-6 ablation) must not gain a "scorer"
    key -- run_arm_b's own scorer=None default is what preserves
    byte-identical Phase 4 behavior, and that only happens if this
    function never sets the key for non-Phase-6 ablations."""
    assert "scorer" not in _ablation_params(HEADLINE)
