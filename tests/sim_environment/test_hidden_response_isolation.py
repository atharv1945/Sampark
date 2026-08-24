"""Structural enforcement of Phase 2 locked decision 4/5: agents must
never be able to reach HiddenResponseProfile, Population, or the
Environment. Mirrors the existing
test_no_stdlib_random_module_is_used_by_the_generator pattern in
tests/sim_generator/test_generator_reproducibility.py — proving the
isolation by inspection, not merely asserting it in a docstring."""

from __future__ import annotations

import ast
import dataclasses
import inspect

import agents.base as base_module
import agents.cart_recovery as cart_recovery_module
import agents.channel as channel_module
import agents.mandate_recovery as mandate_recovery_module
import agents.payment_retry as payment_retry_module
import agents.receivables as receivables_module
import agents.types as types_module

_AGENT_MODULES = (
    base_module,
    channel_module,
    payment_retry_module,
    cart_recovery_module,
    mandate_recovery_module,
    receivables_module,
)

_FORBIDDEN_NAMES = {"HiddenResponseProfile", "Population", "Environment"}


def test_ledger_view_has_no_hidden_response_or_shared_contact_state_field() -> None:
    field_names = {f.name for f in dataclasses.fields(types_module.LedgerView)}
    assert "hidden_response" not in field_names
    assert "contact_states" not in field_names


def test_agent_modules_never_bind_hidden_response_types() -> None:
    for module in _AGENT_MODULES:
        leaked = _FORBIDDEN_NAMES & set(module.__dict__.keys())
        assert not leaked, f"{module.__name__} has direct access to {leaked}"


def _imported_module_names(module) -> set[str]:
    """Actual `import X` / `from X import ...` targets — parsed via ast
    rather than a raw substring search, so a docstring that merely
    *mentions* sim.environment.Environment (as this test file's own
    module-under-test, agents/base.py, does) can't produce a false
    positive."""
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_agent_modules_never_import_sim_environment_or_sim_population() -> None:
    for module in _AGENT_MODULES:
        imported = _imported_module_names(module)
        assert "sim.environment" not in imported, f"{module.__name__} imports sim.environment"
        assert "sim.population" not in imported, f"{module.__name__} imports sim.population"


def test_recovery_agent_select_actions_signature_takes_only_a_ledger_view() -> None:
    import agents.base as base

    signature = inspect.signature(base.RecoveryAgent.select_actions)
    params = list(signature.parameters)
    assert params == ["self", "view"]
