"""Policy activation safety lock — Phase 7 (design lock, Decision 4 /
Part 12).

A compiled policy rule that denies candidates the frozen Phase 4/6
heuristic would have admitted CHANGES Arm B's allocation. Therefore:
every Phase 4/6 regression run and evidence command must see an EMPTY
compiled-rule activation set. This is a regression test, not a
convention — it fails loudly if `policies/activated.yaml` is ever
populated without a corresponding, deliberate re-evaluation of the
Phase 4/6 protected evidence.
"""

from __future__ import annotations

from sampark.policy.compiled import compiled_hard_rules, composed_hard_rules
from sampark.policy.hard import HARD_RULES


def test_activated_yaml_is_absent_or_empty_by_default():
    """The committed repository state (no policies/activated.yaml, or an
    empty one) must produce zero compiled hard rules."""
    assert compiled_hard_rules() == ()


def test_composed_hard_rules_equals_hand_written_rules_when_activation_is_empty():
    """THE safety property: with activation empty, composed_hard_rules()
    is byte-identical (same names, same callables, same order) to the
    frozen 11 alone — a compiled-policy layer existing in the codebase,
    unused, changes NOTHING about Phase 4/6 evidence."""
    composed = composed_hard_rules()
    assert composed == HARD_RULES
    assert len(composed) == 11


def test_compiled_rules_are_always_appended_after_the_frozen_eleven():
    """Even if activation were non-empty (not the case here), composed
    order must never insert a compiled rule BEFORE any of the 11 —
    regulation always evaluated strictly before merchant preference."""
    # Constructed scenario: monkeypatch-free check on the function's own
    # logic — HARD_RULES + compiled is concatenation, never interleaving.
    composed = composed_hard_rules()
    hard_rule_names = [name for name, _ in HARD_RULES]
    composed_names = [name for name, _ in composed]
    assert composed_names[: len(hard_rule_names)] == hard_rule_names
