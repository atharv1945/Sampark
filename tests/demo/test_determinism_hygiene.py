"""Structural determinism guards for `sampark/demo/**`.

`tests/allocator/test_structural_boundaries.py` already proves the Phase 4
decision path reads no wall clock and draws no randomness, and derives every
id with `uuid5`. Phase 8 adds new code that sits ON that path (the runner
drives it, the rate gate gates it), so the same guarantees have to be
re-established for the new package — otherwise "same seed, same trace, every
run" (spec §12.1) would rest on the end-to-end replay test alone, and would
only fail AFTER someone had already introduced the non-determinism.

Deliberately narrow: only modules that can influence a decision are policed.
`isolation.py` legitimately uses `uuid4` and `time.time` to name a throwaway
schema, and `cli.py`/`session.py` are process plumbing — a schema name is not
a decision and never enters the audit payload.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

DECISION_PATH_MODULES = (
    "sampark/demo/runner.py",
    "sampark/demo/enforcement.py",
    "sampark/demo/scenario.py",
    "sampark/demo/scorer_kill.py",
    "sampark/demo/provider.py",
    "sampark/demo/clock.py",
)

BANNED_CALLS = {
    "uuid4": "uuid4 is banned on the decision path - every id must be re-derivable (uuid5)",
    "now": "datetime.now() reads a wall clock; every instant must be passed in",
    "utcnow": "datetime.utcnow() reads a wall clock",
    "today": "date.today() reads a wall clock",
    "random": "randomness makes the replay non-reproducible",
    "shuffle": "randomness makes the replay non-reproducible",
    "choice": "randomness makes the replay non-reproducible",
}

# `time.monotonic`/`time.sleep` are permitted in runner.py for PRESENTATION
# pacing only: they cannot reach a decision, and `test_pacing_cannot_change_
# what_is_decided` proves it empirically.
ALLOWED_TIME_ATTRS = {"sleep", "monotonic"}


def _tree(path: str) -> ast.AST:
    return ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))


@pytest.mark.parametrize("module", DECISION_PATH_MODULES)
def test_no_wall_clock_or_randomness_on_the_decision_path(module):
    for node in ast.walk(_tree(module)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name in ("sleep", "monotonic"):
            continue  # presentation pacing, see module note above
        assert name not in BANNED_CALLS, module + ": " + BANNED_CALLS.get(name, "")


@pytest.mark.parametrize("module", DECISION_PATH_MODULES)
def test_no_random_module_import_on_the_decision_path(module):
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in ("random", "secrets"), module
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in ("random", "secrets"), module


def test_the_runner_iterates_only_over_ordered_sequences():
    """Unordered `set` iteration is the classic silent source of run-to-run
    divergence. The runner must sort anything it loops over."""
    source = pathlib.Path("sampark/demo/runner.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.For):
            iterated = node.iter
            if isinstance(iterated, ast.Call):
                name = getattr(iterated.func, "attr", None) or getattr(iterated.func, "id", None)
                assert name not in ("set", "frozenset"), "runner.py iterates a set"
            assert not isinstance(iterated, ast.SetComp), "runner.py iterates a set comprehension"


def test_the_demo_package_never_imports_a_protected_phase_4_internal():
    """Phase 8 may CALL the protected modules; it may not reach inside them.
    Anything starting with `_` is an implementation detail whose behaviour is
    not part of the contract Phase 8 is allowed to depend on."""
    for module in DECISION_PATH_MODULES + ("sampark/demo/isolation.py",):
        for node in ast.walk(_tree(module)):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
                ("sampark.allocator", "sampark.policy", "sampark.budget")
            ):
                for alias in node.names:
                    assert not alias.name.startswith("_"), (
                        module + " imports the private name " + alias.name
                        + " from " + node.module
                    )
