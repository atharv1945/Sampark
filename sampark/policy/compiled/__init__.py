"""Activated compiled rules — Phase 7 (spec §8.4, design lock Decision 4).

Loads ONLY `policies/activated.yaml` (owner-reviewed, committed) —
compiling a rule (`policies/ir/`, `policies/compiled/`) never activates
it by itself; a rule with no `activated.yaml` entry has ZERO runtime
effect. Composed AFTER the 11 hand-written `sampark.policy.hard.HARD_RULES`,
never inserted by declared permanence or evaluated before them — the
frozen ordering test (`tests/policy/hard/test_hard_filter_ordering.py`)
is untouched, and regulation is always evaluated strictly before merchant
preference.

**Critical, and load-bearing for the Phase 4/6 protected evidence**:
`policies/activated.yaml` MUST be empty (or absent) for every Phase 4/6
regression run and evidence command — a compiled rule that denies
candidates the frozen 11 would have admitted changes Arm B's allocation.
`tests/policy/test_activation_empty_in_protected_evidence.py` enforces
this as a regression test, not a convention.

ZERO LLM or network dependency anywhere in this module — see
`tests/policy/compiler/test_llm_boundary.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

_ACTIVATED_PATH = Path(__file__).resolve().parent.parent.parent.parent / "policies" / "activated.yaml"
_IR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "policies" / "ir"


def _load_activated_rule_ids() -> tuple[str, ...]:
    """Reads `policies/activated.yaml` — a flat list of rule_ids, one
    per line, `# `-prefixed comments and blank lines ignored (no YAML
    library dependency for a format this simple — avoids adding a new
    dependency for one flat list). Returns `()` if the file does not
    exist, which is the default, correct state for every Phase 4/6
    evidence run."""
    if not _ACTIVATED_PATH.exists():
        return ()
    lines = _ACTIVATED_PATH.read_text(encoding="utf-8").splitlines()
    ids = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    return tuple(ids)


def _load_compiled_rule(rule_id: str) -> Callable:
    from sampark.policy.compiler.generate import generate_rule_function
    from sampark.policy.compiler.ir import parse_ir
    from sampark.policy.compiler.validate import validate

    raw = json.loads((_IR_DIR / f"{rule_id}.json").read_text(encoding="utf-8"))
    result = validate(parse_ir(raw))
    return generate_rule_function(result)


def compiled_hard_rules() -> tuple[tuple[str, Callable], ...]:
    """`(name, rule)` pairs for every ACTIVATED compiled rule, in
    `activated.yaml` order — the SAME shape `sampark.policy.hard.HARD_RULES`
    uses, so composition is a plain tuple concatenation. Returns `()`
    when `activated.yaml` is absent or empty (every Phase 4/6 evidence
    run, and every fresh checkout before an owner activates anything)."""
    return tuple((f"compiled.{rule_id}", _load_compiled_rule(rule_id)) for rule_id in _load_activated_rule_ids())


def composed_hard_rules() -> tuple[tuple[str, Callable], ...]:
    """`sampark.policy.hard.HARD_RULES` (the 11 hand-written rules,
    UNCHANGED — this function imports but never modifies that tuple)
    followed by every activated compiled rule, in `activated.yaml`
    order. This is the composition point a mediation-pipeline wiring
    would call instead of `sampark.policy.hard.HARD_RULES` directly.

    Deliberately NOT wired into `sampark.mediation.hard_filter.filter_candidates`
    in this Phase 7 session: that file is under the Phase 4 protection
    boundary (Phase 7 design lock, Part 10.1), and with `activated.yaml`
    empty (no live English->IR compile occurred — no `ANTHROPIC_API_KEY`
    configured, see `tests/policy/compiler/test_llm_boundary.py`), there
    is no real activated rule to exercise the wiring against. This
    function is real, tested infrastructure — `tests/policy/test_activation_empty_in_protected_evidence.py`
    proves the safety property (empty activation set -> byte-identical
    output to `sampark.policy.hard.HARD_RULES` alone) that any future
    wiring will depend on."""
    from sampark.policy.hard import HARD_RULES

    return HARD_RULES + compiled_hard_rules()
