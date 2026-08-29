"""Structural boundary: sampark.models must never read simulation
ground truth. Same technique as
tests/allocator/test_structural_boundaries.py.

The forbidden names are exactly what sim/environment.py's own docstring
says nothing outside it may see: `Population.hidden_response`,
`sim.environment` internals, and any RNG/wall-clock call that would
make a training result non-reproducible for a fixed seed.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil

import sampark.models

_FORBIDDEN_MODULE_SUBSTRINGS = ("sim.environment", "sim.population")
_FORBIDDEN_ATTRIBUTE_ACCESSES = ("hidden_response",)

_BANNED_QUALIFIED_PAIRS = {
    ("time", "time"),
    ("datetime", "now"),
    ("date", "today"),
    ("uuid", "uuid4"),
    ("random", "random"),
    ("np", "random"),
    ("numpy", "random"),
}


def _all_submodules(package):
    modules = [package]
    if hasattr(package, "__path__"):
        for info in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
            if info.name.endswith("artifact_data"):
                continue  # generated data module, not code -- nothing to inspect
            modules.append(importlib.import_module(info.name))
    return modules


def test_models_package_never_imports_environment_or_population():
    for module in _all_submodules(sampark.models):
        tree = ast.parse(inspect.getsource(module))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        for forbidden in _FORBIDDEN_MODULE_SUBSTRINGS:
            assert not any(forbidden in name for name in imported), (
                f"{module.__name__} must never import {forbidden}; found {imported}"
            )


def test_models_package_never_accesses_hidden_response():
    """AST-based, not a raw substring scan: this module's own docstrings
    (including this test's imports' docstrings) legitimately DISCUSS
    `Population.hidden_response` by name as the thing that must never be
    accessed -- only an actual `ast.Attribute` node with that name is a
    real violation."""
    for module in _all_submodules(sampark.models):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in _FORBIDDEN_ATTRIBUTE_ACCESSES, (
                    f"{module.__name__} accesses .{node.attr}"
                )


def test_models_package_has_no_wall_clock_or_random_calls():
    for module in _all_submodules(sampark.models):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                pair = (func.value.id, func.attr)
                assert pair not in _BANNED_QUALIFIED_PAIRS, (
                    f"{module.__name__} calls banned wall-clock/random function {pair}"
                )


def test_training_data_reads_only_seed_scoped_public_functions():
    """sampark.models.training_data may call sim.arm_a.run_arm_a and
    sim.cli.build_dataset (both already the calibration precedent's own
    sources) and nothing else from sim/."""
    import sampark.models.training_data as td

    tree = ast.parse(inspect.getsource(td))
    imported_from_sim: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("sim"):
            imported_from_sim.append(node.module)

    assert set(imported_from_sim) <= {"sim.arm_a", "sim.cli"}, imported_from_sim
