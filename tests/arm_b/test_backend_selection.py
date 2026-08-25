"""Backend selection — Phase 4C-2 Blocker 1.

1. The official Arm B runner (sim/arm_b_cli.py) selects the Postgres
   backend, always.
2. The official evidence path CANNOT silently fall back to InMemory —
   there is no flag for it, and a Postgres failure aborts loudly.
"""

from __future__ import annotations

import argparse
import inspect
import sys

import pytest

import sim.arm_b_cli as arm_b_cli
from sim.arm_b import BACKEND_MEMORY, BACKEND_POSTGRES, run_arm_b


def test_run_arm_b_default_backend_is_memory_for_fast_unit_tests():
    """Every existing call site (`run_arm_b(seed)`) must keep working
    unchanged — the DEFAULT stays "memory" so unit tests are unaffected
    by Blocker 1; only the OFFICIAL CLI explicitly requests postgres."""
    sig = inspect.signature(run_arm_b)
    assert sig.parameters["backend"].default == BACKEND_MEMORY


def test_run_arm_b_rejects_unknown_backend():
    with pytest.raises(ValueError):
        run_arm_b(42, backend="sqlite")


def test_official_cli_has_no_backend_selection_flag():
    """The escape hatch that doesn't exist cannot be silently taken —
    there must be no --backend/--in-memory/--no-postgres flag on the
    official CLI's parser at all."""
    source = inspect.getsource(arm_b_cli)
    assert "--backend" not in source
    assert "--in-memory" not in source
    assert "--memory" not in source


def test_official_cli_always_passes_backend_postgres(monkeypatch):
    """Patch run_arm_b itself to capture the kwargs the CLI actually
    calls it with, without needing a real Postgres connection."""
    captured = {}

    def fake_run_arm_b(seed, **kwargs):
        captured.update(kwargs)
        raise SystemExit(99)  # short-circuit before any real work

    monkeypatch.setattr(arm_b_cli, "run_arm_b", fake_run_arm_b)
    monkeypatch.setattr(sys, "argv", ["arm_b_cli.py", "--seed", "42"])

    with pytest.raises(SystemExit):
        arm_b_cli.main()

    assert captured.get("backend") == BACKEND_POSTGRES


def test_official_cli_fails_loudly_and_does_not_fall_back_when_postgres_unavailable(monkeypatch):
    """Simulate a Postgres connection failure inside run_arm_b and
    assert the CLI exits non-zero rather than silently retrying with
    the in-memory backend."""

    def failing_run_arm_b(seed, **kwargs):
        assert kwargs.get("backend") == BACKEND_POSTGRES
        raise ConnectionError("simulated: Postgres unreachable")

    monkeypatch.setattr(arm_b_cli, "run_arm_b", failing_run_arm_b)
    monkeypatch.setattr(sys, "argv", ["arm_b_cli.py", "--seed", "42"])

    with pytest.raises(SystemExit) as exc_info:
        arm_b_cli.main()
    assert exc_info.value.code != 0


def test_official_cli_asserts_result_backend_is_postgres(monkeypatch):
    """Even if run_arm_b somehow returned a memory-backed result, the
    CLI's own invariant check must catch it rather than silently
    reporting memory-backend numbers as the official evidence."""
    from sim.arm_b import ArmBResult

    def sneaky_run_arm_b(seed, **kwargs):
        return ArmBResult(outcomes=(), decisions=(), backend=BACKEND_MEMORY)

    monkeypatch.setattr(arm_b_cli, "run_arm_b", sneaky_run_arm_b)
    monkeypatch.setattr(sys, "argv", ["arm_b_cli.py", "--seed", "42"])

    with pytest.raises(AssertionError):
        arm_b_cli.main()


@pytest.mark.parametrize(
    "ablation,expected",
    [
        ("headline", {}),
        ("aging_zero", {"aging_bonus_paise": 0}),
        ("fifo_under_cap", {"fifo_mode": True}),
    ],
)
def test_ablation_params_are_deterministic_and_minimal(ablation, expected):
    """Each ablation changes EXACTLY the one parameter it names — no
    incidental extra overrides."""
    assert arm_b_cli._ablation_params(ablation) == expected


def test_merchant_margin_half_ablation_is_computed_not_typed_in():
    from sampark.allocator.constants import MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW

    params = arm_b_cli._ablation_params("merchant_margin_half")
    assert params == {"merchant_budget_paise_per_window": MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW // 2}


def test_every_ablation_has_a_result_metadata_note():
    """W8: every ablation, including every FUTURE one added to the
    closed ABLATIONS set, must have a note — a missing entry raises
    KeyError at CLI run time rather than silently omitting the field."""
    assert set(arm_b_cli._ABLATION_NOTES) == set(arm_b_cli.ABLATIONS)


def test_fifo_under_cap_note_documents_the_admission_bypass():
    """The one ablation whose admission semantics genuinely differ from
    every other condition must say so in its own result metadata, not
    just in a docstring nobody reading the JSON will see."""
    note = arm_b_cli._ABLATION_NOTES[arm_b_cli.FIFO_UNDER_CAP]
    assert "bypass" in note.lower()
    assert "expected_net" in note
