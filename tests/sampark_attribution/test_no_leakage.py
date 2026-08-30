"""Structural guards for sampark.attribution — Phase 7 (spec §8.9).

Two independent leakage risks:
  1. The baseline estimator must never read sim.natural's ground-truth
     multiplier table (it estimates the rate empirically from real
     holdout outcomes, or not at all — Phase 7 design lock, leakage
     prevention D.2/D.3).
  2. The baseline estimator must never import sim.arm_h — Arm H is a
     validation-only counterfactual (Phase 7 design lock, Decision 15);
     using it to compute a credit would make the ledger depend on
     information no production system could obtain.
"""

from __future__ import annotations

import ast
import inspect

import sampark.attribution.baseline as baseline_module
import sampark.attribution.credit as credit_module


def _imported_names(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_baseline_never_imports_arm_h():
    imported = _imported_names(baseline_module)
    assert not any("arm_h" in name for name in imported), (
        f"sampark.attribution.baseline must never import sim.arm_h (Decision 15); found {imported}"
    )


def test_baseline_never_accesses_the_ground_truth_multiplier_table():
    """AST-attribute check, not a substring scan on source text — this
    module's own docstring legitimately DISCUSSES NATURAL_MULTIPLIER_BY_ROOT_CAUSE
    by name; only an actual reference is a violation."""
    tree = ast.parse(inspect.getsource(baseline_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "NATURAL_MULTIPLIER_BY_ROOT_CAUSE":
            raise AssertionError("sampark.attribution.baseline references the ground-truth multiplier table directly")


def test_credit_module_never_imports_arm_h():
    imported = _imported_names(credit_module)
    assert not any("arm_h" in name for name in imported)


def test_baseline_and_credit_never_import_environment_or_population():
    """Same ground-truth boundary sampark.models observes."""
    for module in (baseline_module, credit_module):
        imported = _imported_names(module)
        for forbidden in ("sim.environment", "sim.population"):
            assert not any(forbidden in name for name in imported), (
                f"{module.__name__} must never import {forbidden}; found {imported}"
            )
