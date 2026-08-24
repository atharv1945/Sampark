"""Phase 1 exit criterion volume checks — spec §11, §18.1: ~5,000
customers, 20,000 risk items, four sources.
"""

from __future__ import annotations

import pytest

from sim.cli import build_dataset
from sim.generator import N_SIGNALS, SOURCES
from sim.population import N_PEOPLE

_SEED = 42


@pytest.fixture(scope="module")
def dataset():
    return build_dataset(_SEED)


def test_generates_exactly_5000_people(dataset):
    population, _, _ = dataset
    assert len(population.people) == N_PEOPLE == 5_000


def test_generates_exactly_20000_risk_items(dataset):
    _, _, ledger = dataset
    assert len(ledger.risk_items) == N_SIGNALS == 20_000


def test_resolved_customer_count_is_approximately_5000(dataset):
    """Not every one of the 5,000 people necessarily appears in the 20,000
    sampled-with-replacement signals, so the resolved count is close to,
    not necessarily exactly, 5,000 (spec says "~5,000")."""
    _, _, ledger = dataset
    assert 4_500 <= len(ledger.customers) <= 5_000


def test_all_four_canonical_sources_are_present(dataset):
    _, signals, _ = dataset
    assert {s.source for s in signals} == set(SOURCES)
    assert SOURCES == (
        "failed_payment",
        "abandoned_checkout",
        "mandate_failure",
        "overdue_invoice",
    )


def test_each_source_has_a_substantial_share_of_risk_items(dataset):
    """Sanity check against a degenerate distribution (e.g. one source
    dominating) rather than a literal even split, which is not spec-
    mandated."""
    _, signals, _ = dataset
    counts: dict[str, int] = {}
    for s in signals:
        counts[s.source] = counts.get(s.source, 0) + 1
    for source in SOURCES:
        assert counts[source] > 1_000, f"{source} has implausibly few risk items: {counts[source]}"


def test_risk_customer_map_covers_every_risk_item_exactly_once(dataset):
    _, _, ledger = dataset
    risk_ids = {r.risk_id for r in ledger.risk_items}
    assert set(ledger.risk_customer_map.keys()) == risk_ids
    assert len(ledger.risk_customer_map) == len(ledger.risk_items)
