"""compute_optimality_gap -- end-to-end against the real seed-42 Arm B
run (memory backend, no Postgres needed). Slower than a unit test (one
full Arm B memory run, ~1 minute) but this is the one test that proves
the tool's core invariant on real data, not a hand-built fixture:
achieved (what greedy actually granted) can never exceed optimal (the
DP's best feasible choice over the SAME admitted candidates) -- if it
ever did, either the DP or the accounting feeding it would be wrong.
"""

from __future__ import annotations

from sim.optimality_gap import compute_optimality_gap


def test_achieved_never_exceeds_optimal_on_real_seed_42_data():
    results = compute_optimality_gap(seed=42, top_k_windows=2)
    assert len(results) == 2
    for r in results:
        assert r.achieved_expected_net_paise <= r.optimal_expected_net_paise + 1e-6
        assert 0.0 <= r.gap_ratio <= 1.0 + 1e-9
        assert r.admitted_count > 0


def test_gap_ratio_is_close_to_one_at_headline_margin_budget():
    """Headline (unconstrained-in-practice) merchant budget: even the
    highest-ceiling-utilization windows should show a small gap, since
    the pool rarely actually binds at this capacity (Design Lock's own
    observation that realized spend is a fraction of ceiling exposure).
    A regression here would mean either the DP or the accounting broke,
    not that the allocator suddenly got much worse."""
    results = compute_optimality_gap(seed=42, top_k_windows=3)
    for r in results:
        assert r.gap_ratio > 0.99
