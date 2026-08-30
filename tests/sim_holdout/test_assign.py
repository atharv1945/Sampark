"""sim.holdout — deterministic customer-level holdout assignment, Phase 7."""

from __future__ import annotations

import random

import pytest

from sim.holdout import ASSIGNMENT_VERSION, assign, membership_digest, strata


def _synthetic_amounts(n: int, seed: int = 0) -> dict[str, int]:
    rng = random.Random(seed)
    return {f"customer-{i:05d}": rng.randint(5_000, 10_000_000) for i in range(n)}


def test_zero_fraction_yields_empty_set():
    amounts = _synthetic_amounts(500)
    assert assign(seed=42, fraction=0.0, customer_amounts=amounts) == frozenset()


def test_assignment_is_deterministic_across_calls():
    amounts = _synthetic_amounts(500)
    a = assign(seed=42, fraction=0.1, customer_amounts=amounts)
    b = assign(seed=42, fraction=0.1, customer_amounts=amounts)
    assert a == b


def test_assignment_is_deterministic_regardless_of_dict_insertion_order():
    amounts = _synthetic_amounts(500)
    shuffled = dict(sorted(amounts.items(), key=lambda kv: kv[0], reverse=True))
    a = assign(seed=42, fraction=0.1, customer_amounts=amounts)
    b = assign(seed=42, fraction=0.1, customer_amounts=shuffled)
    assert a == b


def test_different_seeds_produce_different_assignments():
    amounts = _synthetic_amounts(500)
    a = assign(seed=42, fraction=0.1, customer_amounts=amounts)
    b = assign(seed=7, fraction=0.1, customer_amounts=amounts)
    assert a != b


def test_assignment_is_nested_in_fraction():
    """holdout(0.10) must be a subset of holdout(0.20) — required for the
    two-fraction interference sensitivity to be a clean comparison. This is
    the property the design-lock document's original (rejected) hash formula
    (which included fraction_bps in the hash input) would have broken."""
    amounts = _synthetic_amounts(5000)
    small = assign(seed=42, fraction=0.10, customer_amounts=amounts)
    large = assign(seed=42, fraction=0.20, customer_amounts=amounts)
    assert small <= large
    assert len(large) > len(small)


def test_strata_are_exactly_balanced():
    amounts = _synthetic_amounts(5000)
    buckets = strata(amounts)
    assert len(buckets) == 5
    sizes = [len(b) for b in buckets]
    assert sum(sizes) == 5000
    assert max(sizes) - min(sizes) <= 1


def test_strata_are_sorted_by_amount_ascending():
    amounts = _synthetic_amounts(500)
    buckets = strata(amounts)
    for lower_bucket, upper_bucket in zip(buckets, buckets[1:]):
        max_lower = max(amounts[c] for c in lower_bucket)
        min_upper = min(amounts[c] for c in upper_bucket)
        assert max_lower <= min_upper


def test_fraction_out_of_range_raises():
    amounts = _synthetic_amounts(100)
    with pytest.raises(ValueError):
        assign(seed=42, fraction=1.0, customer_amounts=amounts)
    with pytest.raises(ValueError):
        assign(seed=42, fraction=-0.1, customer_amounts=amounts)


def test_stratum_holdout_count_matches_exact_integer_arithmetic():
    """Regression guard against float-precision truncation: a stratum of
    exactly 1000 customers at fraction=0.1 must hold out exactly 100, not 99
    (int(1000 * 0.1) can evaluate to 99 due to float imprecision)."""
    amounts = _synthetic_amounts(5000)
    buckets = strata(amounts)
    stratum_sizes = [len(b) for b in buckets]
    held = assign(seed=42, fraction=0.1, customer_amounts=amounts)
    # exact_expected: sum over strata of (stratum_size * 1000) // 10000
    expected = sum((size * 1000) // 10_000 for size in stratum_sizes)
    assert len(held) == expected


def test_hash_uses_sha256_not_python_hash():
    """Regression pin: the exact published hash formula, so a future edit
    that silently swaps in Python's hash() (unstable across processes) is
    caught. Computed independently of sim.holdout's own _rank_key."""
    import hashlib

    seed, cid = 42, "customer-00001"
    expected = int.from_bytes(
        hashlib.sha256(f"sampark-holdout:v{ASSIGNMENT_VERSION}:{seed}:{cid}".encode("utf-8")).digest()[:8],
        "big",
    )
    from sim.holdout import _rank_key

    assert _rank_key(seed, cid) == expected


def test_membership_digest_is_deterministic_and_order_independent():
    a = frozenset({"customer-00003", "customer-00001", "customer-00002"})
    b = frozenset({"customer-00001", "customer-00002", "customer-00003"})
    assert membership_digest(a) == membership_digest(b)


def test_membership_digest_changes_with_membership():
    a = frozenset({"customer-00001"})
    b = frozenset({"customer-00001", "customer-00002"})
    assert membership_digest(a) != membership_digest(b)


def test_no_rng_import_in_module():
    """Structural guard: sim.holdout must never import random or numpy —
    determinism is by SHA-256 hash, not by any seeded generator."""
    import ast
    import inspect

    import sim.holdout as holdout_module

    tree = ast.parse(inspect.getsource(holdout_module))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
    assert "random" not in imported_names
    assert "numpy" not in imported_names
    assert "numpy.random" not in imported_names
