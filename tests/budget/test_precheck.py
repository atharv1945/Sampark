"""Advisory rolling-counter pre-check — Design Lock §10.3.

Shadow-only: no verdict here can be, or is ever treated as, "definitely
allowed." Fault equivalence is proven at the mediation-decision level
by construction (nothing in sampark.mediation or sampark.allocator
imports this module at all — the precheck is observation-only tooling,
never wired into the decision path), asserted here structurally.
"""

from __future__ import annotations

import ast
import datetime as dt
import inspect

import pytest

from sampark.budget import precheck
from sampark.budget.precheck import NullPrecheck, PrecheckVerdict, record_shadow_observation

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


def test_null_precheck_always_unknown():
    p = NullPrecheck()
    assert p.check("cust-1", DECISION_AT) is PrecheckVerdict.UNKNOWN


def test_precheck_verdict_has_no_definitely_allowed_member():
    members = {v.name for v in PrecheckVerdict}
    assert members == {"LIKELY_CAPPED", "UNKNOWN"}


def test_record_shadow_observation_delegates_to_the_precheck():
    p = NullPrecheck()
    assert record_shadow_observation(p, "cust-1", DECISION_AT) is PrecheckVerdict.UNKNOWN


def _imported_module_names(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_precheck_module_is_the_only_redis_importer_in_sampark_budget():
    import importlib
    import pkgutil

    import sampark.budget

    modules = [sampark.budget]
    for info in pkgutil.iter_modules(sampark.budget.__path__, prefix="sampark.budget."):
        modules.append(importlib.import_module(info.name))

    for module in modules:
        if module.__name__ == "sampark.budget.precheck":
            continue
        assert "redis" not in _imported_module_names(module)


@pytest.mark.skipif(not precheck.REDIS_AVAILABLE, reason="redis is not an installed dependency of this project")
def test_redis_precheck_degrades_to_unknown_on_any_fault():
    class _RaisingClient:
        def get(self, key):
            raise ConnectionError("simulated Redis fault")

    p = precheck.RedisRollingCounterPrecheck(_RaisingClient(), cap_24h=1)
    assert p.check("cust-1", DECISION_AT) is PrecheckVerdict.UNKNOWN


@pytest.mark.skipif(not precheck.REDIS_AVAILABLE, reason="redis is not an installed dependency of this project")
def test_redis_precheck_missing_key_is_unknown_not_capped():
    class _EmptyClient:
        def get(self, key):
            return None

    p = precheck.RedisRollingCounterPrecheck(_EmptyClient(), cap_24h=1)
    assert p.check("cust-1", DECISION_AT) is PrecheckVerdict.UNKNOWN


@pytest.mark.skipif(not precheck.REDIS_AVAILABLE, reason="redis is not an installed dependency of this project")
def test_redis_precheck_reports_likely_capped_when_over_cap():
    class _FullClient:
        def get(self, key):
            return b"1"

    p = precheck.RedisRollingCounterPrecheck(_FullClient(), cap_24h=1)
    assert p.check("cust-1", DECISION_AT) is PrecheckVerdict.LIKELY_CAPPED
