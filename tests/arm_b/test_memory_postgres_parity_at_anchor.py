"""Memory/Postgres backend parity at the frozen anchor — Phase 9.

`sim/gate.py` requires `backend == "postgres"` for any row that enters the
official evidence gate, and every committed `arm_b_*_metrics_*.json` was
produced that way. The repository had no test asserting that the memory
backend agrees with it.

Phase 9's sensitivity sweep depends on exactly that agreement: it runs 50 grid
points on the memory backend (roughly a minute each) instead of on Postgres
(roughly 48 minutes each, and the Phase 6 disk-full incident is what a
twelve-hour Postgres grid risks repeating). This test is the licence for that
substitution, and it is stated as a test rather than as an assumption in a
docstring.

Scope, precisely: this asserts parity for the world-v1 Arm B path at seed 42.
It does NOT claim parity for world v2, where the memory ledger's
`optouts_by_channel` is a frozen `{}` stub and opt-out enforcement genuinely
differs — a difference `sim/phase7_evidence.py` already documents in every
Arm B-H result file's `backend_note`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sim.arm_b import BACKEND_MEMORY, BACKEND_POSTGRES, run_arm_b
from sim.metrics import compute_metrics

SEED = 42
_FROZEN = Path(__file__).resolve().parents[2] / "results" / f"arm_b_metrics_{SEED}.json"

PARITY_FIELDS = (
    "total_contacts",
    "total_recoveries",
    "recovered_amount_paise",
    "incentive_spend_paise",
    "recovered_amount_per_contact_paise",
)


@pytest.fixture(scope="module")
def frozen() -> dict:
    return json.loads(_FROZEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def memory_metrics() -> dict:
    return compute_metrics(run_arm_b(SEED, backend=BACKEND_MEMORY).outcomes)


def test_the_frozen_record_really_was_postgres_backed(frozen):
    """Guards the premise. If the committed file were itself memory-backed,
    this test would be comparing memory to memory and proving nothing."""
    assert frozen["backend"] == BACKEND_POSTGRES
    assert frozen["constants_commit_sha"] == "aa87123aafdc9d812f5a01c04766c60b9198a2ce"


@pytest.mark.parametrize("field", PARITY_FIELDS)
def test_memory_backend_reproduces_the_postgres_record(memory_metrics, frozen, field):
    assert memory_metrics[field] == frozen[field]


def test_per_agent_breakdown_also_matches(memory_metrics, frozen):
    """A stronger check than the totals: two different allocations can sum to
    the same total while distributing grants differently across agents."""
    assert memory_metrics["by_agent"] == frozen["by_agent"]
