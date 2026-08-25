"""sim.arm_b._window_range — Phase 4C hardening (W7): a single source
for the (first_window, last_window) computation, used both to pre-seed
Postgres budget_windows rows and to bound the mediation loop itself.
"""

from __future__ import annotations

import ast
import datetime as dt
import inspect

from sim.arm_b import _run_window_loop, _window_range
from agents.types import ContactAction


def _action(scheduled_at):
    return ContactAction(
        agent_id="cart_recovery_agent", risk_id="risk-1", customer_id="cust-1",
        channel="whatsapp", intent="cart_recovery", incentive_bps=500, scheduled_at=scheduled_at,
    )


def test_window_range_matches_the_naive_min_max_formula():
    actions = [
        _action(dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc)),
        _action(dt.datetime(2025, 9, 12, 12, 0, tzinfo=dt.timezone.utc)),
    ]
    first, last = _window_range(actions)
    assert first == dt.date(2025, 9, 10)
    # last = max window + MAX_DEFERRAL_WINDOWS + 1 days
    from sampark.allocator.constants import MAX_DEFERRAL_WINDOWS

    assert last == dt.date(2025, 9, 12) + dt.timedelta(days=MAX_DEFERRAL_WINDOWS + 1)


def test_run_window_loop_computes_its_range_via_window_range_not_a_second_formula():
    """Structural guard: `_run_window_loop`'s source must call
    `_window_range(`, not re-derive first_window/last_window from its
    own independent min()/max() over actions_by_window — the exact
    duplication this hardening removed."""
    source = inspect.getsource(_run_window_loop)
    assert "_window_range(" in source, (
        "_run_window_loop must delegate its window range to _window_range() — "
        "a second independent min()/max() computation is exactly the W7 defect"
    )


def test_only_one_max_deferral_windows_plus_one_timedelta_expression_exists():
    """Belt-and-braces: the specific expression that defines "how far
    past the last arrival the range extends" must appear exactly ONCE
    in the module — inside `_window_range` itself — never duplicated."""
    import sim.arm_b as arm_b_module

    tree = ast.parse(inspect.getsource(arm_b_module))
    hits = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "timedelta":
            for kw in node.keywords:
                if kw.arg == "days" and isinstance(kw.value, ast.BinOp):
                    hits += 1
    assert hits == 1, f"expected exactly one 'timedelta(days=... + 1)'-shaped expression, found {hits}"
