"""Structural boundaries — Design Lock §1, §5.1, §8, §10.3, §16.

These enforce architecture, not behaviour: they parse source with
`ast` (same technique as tests/test_scope_enforcement.py's allocator
check) so the boundary holds even after all the modules on both sides
exist, not merely because one happens to be absent today.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil

import sampark.allocator
import sampark.budget
import sampark.policy.hard
import sampark.policy.soft


def _imported_module_names(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _all_submodules(package):
    modules = [package]
    if hasattr(package, "__path__"):
        for info in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
            modules.append(importlib.import_module(info.name))
    return modules


def test_policy_hard_never_imports_policy_soft_or_scoring():
    for module in _all_submodules(sampark.policy.hard):
        imported = _imported_module_names(module)
        assert not any("policy.soft" in name for name in imported), (
            f"{module.__name__} must never import sampark.policy.soft; found {imported}"
        )
        assert not any("allocator.scoring" in name for name in imported), (
            f"{module.__name__} must never import sampark.allocator.scoring; found {imported}"
        )


def _imported_names_with_aliases(module) -> list[tuple[str, str | None]]:
    """Like _imported_module_names, but also captures the alias each
    name was imported under (`import sampark.policy as hard_policy` and
    `from sampark.policy import hard as hard_policy` must both be
    caught, not just the literal spelling `sampark.policy.hard`)."""
    tree = ast.parse(inspect.getsource(module))
    names: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((alias.name, alias.asname))
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                # a `from sampark.policy import hard` (with or without an
                # alias) resolves to the submodule sampark.policy.hard —
                # normalize to that dotted form so it is caught below
                # exactly like `import sampark.policy.hard`.
                full = f"{node.module}.{alias.name}"
                names.append((full, alias.asname))
    return names


def test_allocator_never_imports_policy_hard():
    """Checked by (module, name) pair AND by alias — `from sampark.policy
    import hard as hard_policy` resolves to the module
    `sampark.policy.hard` regardless of what local name it is bound to,
    so aliasing must not be able to hide this import from the check."""
    forbidden = ("sampark.policy.hard",)
    for module in _all_submodules(sampark.allocator):
        imported = _imported_names_with_aliases(module)
        for full_name, alias in imported:
            assert not any(full_name == f or full_name.startswith(f + ".") for f in forbidden), (
                f"{module.__name__} must never import sampark.policy.hard, even under an alias; "
                f"found {full_name!r} as {alias!r}"
            )


def test_allocator_greedy_has_no_reference_to_hard_policy_names_at_runtime():
    """Belt-and-braces over the AST check: inspect the ACTUAL imported
    module object's namespace at runtime for anything hard-policy-shaped
    (Verdict, HardVerdict, PolicyContext, evaluate_all, or the
    sampark.policy.hard module object itself bound under any name)."""
    import sampark.allocator.greedy as greedy
    import sampark.policy.hard as policy_hard_module
    from sampark.policy.types import HardVerdict, PolicyContext, Verdict

    # Identity comparison, not `in`/hashing: some module-namespace values
    # (e.g. a ModuleSpec) are unhashable and would break a set/list `in`
    # check via __eq__ before we ever get to the objects we actually care
    # about.
    banned_values = (policy_hard_module, HardVerdict, PolicyContext, Verdict, policy_hard_module.evaluate_all)

    for name, value in vars(greedy).items():
        assert not any(value is banned for banned in banned_values), (
            f"sampark.allocator.greedy.{name} is bound to a hard-policy object ({value!r}) — "
            "the allocator must have zero runtime access to hard-policy verdicts or evaluators"
        )


def test_greedy_allocate_window_receives_only_a_pre_filtered_candidate_tuple():
    """The allocator cannot see a candidate hard-policy has rejected.

    Constructs ONE hard-INADMISSIBLE candidate (quiet hours) and ONE
    hard-ADMISSIBLE candidate for the SAME customer, runs
    `sampark.mediation.hard_filter.filter_candidates` to get the real
    survivor tuple, and proves that when ONLY that survivor tuple is
    handed to `allocate_window`, the rejected candidate's risk_id never
    appears anywhere in the allocator's output — the allocator has no
    way to reach it, because it was never given it."""
    import datetime as dt
    from uuid import uuid4

    from sampark.allocator.candidate import build_candidate
    from sampark.allocator.constants import AGING_BONUS_PAISE
    from sampark.allocator.greedy import allocate_window
    from sampark.budget.store import InMemoryGrantIssuer, InMemoryMediationLedger
    from sampark.contracts import GrantRequest, RiskItem
    from sampark.mediation.hard_filter import filter_candidates

    detected_at = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)
    decision_at = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)

    def _candidate(risk_id, send_after, bps=0):
        item = RiskItem(
            risk_id=risk_id, source="abandoned_checkout", amount_paise=1_000_000,
            root_cause="price_hesitation", detected_at=detected_at,
        )
        request = GrantRequest(
            request_id=uuid4(), agent_id="cart_recovery_agent", customer_id="cust-1", risk_id=risk_id,
            intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=bps,
            issued_at=detected_at, signature="sig",
        )
        return build_candidate(request, item, "cust-1", send_after)

    rejected = _candidate("risk-rejected-quiet-hours", dt.datetime(2025, 9, 10, 22, 0, tzinfo=dt.timezone.utc))
    admitted = _candidate("risk-admitted", decision_at + dt.timedelta(hours=3))

    ledger = InMemoryMediationLedger(
        {"cust-1": (rejected.risk_item, admitted.risk_item)}, merchant_budget_paise_per_window=1_000_000_000
    )
    survivors, immediate_outcomes, fact_unavailable_by_risk_id = filter_candidates(
        (rejected, admitted), ledger, decision_at
    )

    # The hard filter genuinely rejected one and admitted the other.
    assert {c.risk_item.risk_id for c in survivors} == {"risk-admitted"}
    assert {o.candidate.risk_item.risk_id for o in immediate_outcomes} == {"risk-rejected-quiet-hours"}

    issuer = InMemoryGrantIssuer()
    outcomes = allocate_window(
        survivors, ledger, issuer, decision_at, AGING_BONUS_PAISE, fact_unavailable_by_risk_id=fact_unavailable_by_risk_id
    )

    seen_risk_ids = {o.candidate.risk_item.risk_id for o in outcomes}
    assert "risk-rejected-quiet-hours" not in seen_risk_ids, (
        "the allocator produced an outcome for a candidate it was never given"
    )
    assert seen_risk_ids == {"risk-admitted"}


def test_greedy_allocate_window_signature_has_no_policy_parameter():
    """No parameter by which the allocator could re-run or override a
    hard denial — it takes a flat Candidate tuple, not a request stream
    it could re-evaluate for scope/policy itself."""
    from sampark.allocator.greedy import allocate_window

    params = list(inspect.signature(allocate_window).parameters)
    assert params[0] == "candidates"
    assert not any("verdict" in p.lower() for p in params)
    # "policy" IS allowed to appear now (fact_unavailable_by_risk_id is
    # plain data, not a policy object) — the real guarantee is the two
    # tests above: no hard-policy TYPE or MODULE reference exists in
    # sampark.allocator.greedy, at either AST or runtime-object level.


def test_budget_package_only_imports_redis_in_precheck():
    for module in _all_submodules(sampark.budget):
        if module.__name__ == "sampark.budget.precheck":
            continue
        imported = _imported_module_names(module)
        assert "redis" not in imported, f"{module.__name__} must not import redis; found {imported}"


_BANNED_BARE_NAMES = {"uuid4"}  # a bare `uuid4()` call (vs `uuid.uuid4()`, also caught below)
_BANNED_QUALIFIED_PAIRS = {
    ("time", "time"),  # time.time()
    ("datetime", "now"),  # datetime.now() / datetime.datetime.now()
    ("date", "today"),  # date.today()
    ("uuid", "uuid4"),  # uuid.uuid4()
    ("random", "random"),
    ("np", "random"),
    ("numpy", "random"),
}


def _find_banned_calls(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _BANNED_BARE_NAMES:
            hits.append(func.id)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in _BANNED_QUALIFIED_PAIRS:
                hits.append(f"{func.value.id}.{func.attr}")
    return hits


def test_no_wall_clock_or_random_calls_on_the_decision_path():
    import sampark.allocator.candidate
    import sampark.allocator.greedy
    import sampark.allocator.scoring
    import sampark.budget.contact
    import sampark.budget.margin
    import sampark.budget.store
    import sampark.budget.windows
    import sampark.mediation.lifecycle
    import sampark.mediation.service

    decision_path_modules = [
        sampark.allocator.candidate,
        sampark.allocator.greedy,
        sampark.allocator.scoring,
        sampark.budget.contact,
        sampark.budget.margin,
        sampark.budget.store,
        sampark.budget.windows,
        sampark.mediation.lifecycle,
        sampark.mediation.service,
        *_all_submodules(sampark.policy.hard),
        *_all_submodules(sampark.policy.soft),
    ]
    for module in decision_path_modules:
        hits = _find_banned_calls(module)
        assert not hits, f"{module.__name__} calls banned wall-clock/random function(s): {hits}"


def test_decision_id_is_uuid5_not_uuid4():
    import sampark.mediation.service

    source = inspect.getsource(sampark.mediation.service.decision_id_for)
    assert "uuid5" in source
    assert "uuid4" not in source
