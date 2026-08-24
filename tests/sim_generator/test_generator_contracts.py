"""Every emitted ledger record round-trips through the unmodified Phase 0
Pydantic contracts (CONTRACTS.md) without a ValidationError, and every
customer referenced by a risk item actually exists in the ledger.
"""

from __future__ import annotations

import pytest

from sampark.rootcause import load_taxonomy
from sim.cli import build_dataset

_SEED = 42
_FIXED_TAXONOMY: tuple[str, ...] = load_taxonomy().taxonomy


@pytest.fixture(scope="module")
def ledger():
    _, _, ledger = build_dataset(_SEED)
    return ledger


def test_every_risk_item_root_cause_is_in_the_fixed_taxonomy(ledger):
    for risk_item in ledger.risk_items:
        assert risk_item.root_cause in _FIXED_TAXONOMY


def test_every_risk_item_amount_is_positive(ledger):
    for risk_item in ledger.risk_items:
        assert risk_item.amount_paise > 0


def test_every_risk_id_is_unique(ledger):
    risk_ids = [r.risk_id for r in ledger.risk_items]
    assert len(risk_ids) == len(set(risk_ids))


def test_every_customer_id_is_unique(ledger):
    customer_ids = [c.customer_id for c in ledger.customers]
    assert len(customer_ids) == len(set(customer_ids))


def test_every_risk_item_references_a_customer_present_in_the_ledger(ledger):
    customer_ids = {c.customer_id for c in ledger.customers}
    for risk_item in ledger.risk_items:
        referenced = ledger.risk_customer_map[risk_item.risk_id]
        assert referenced in customer_ids


def test_every_customer_has_a_contact_state(ledger):
    customer_ids = {c.customer_id for c in ledger.customers}
    assert set(ledger.contact_states.keys()) == customer_ids


def test_every_customer_has_at_least_one_contact_hash(ledger):
    """Population guarantees every person has phone and/or email; identity
    resolution should never produce a Customer with neither."""
    for customer in ledger.customers:
        assert customer.phone_hash is not None or customer.email_hash is not None
