"""Shared fixtures for sampark/policy/ tests.

`FakeLedgerView` is a minimal, hand-built MediationLedgerView — used
instead of sampark.budget.store.InMemoryMediationLedger so hard-rule
unit tests are isolated from the reference issuance implementation and
exercise the Protocol contract directly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid4

import pytest

from sampark.allocator.candidate import Candidate
from sampark.contracts import GrantRequest, RiskItem
from sampark.policy.types import PolicyContext

ISSUED_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


@dataclass
class FakeLedgerView:
    optouts: dict[str, dict[str, str]] = field(default_factory=dict)
    consents: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    risk_items_by_customer_map: dict[str, tuple[RiskItem, ...]] = field(default_factory=dict)
    rolling_counts_map: dict[str, tuple[int, int]] = field(default_factory=dict)
    active_claims: set[tuple[str, date]] = field(default_factory=set)
    open_candidates_map: dict[str, tuple[RiskItem, ...]] = field(default_factory=dict)

    def optouts_by_channel(self, customer_id: str):
        return self.optouts.get(customer_id, {})

    def consent_scopes(self, customer_id: str):
        return self.consents.get(customer_id, {})

    def risk_items_for_customer(self, customer_id: str) -> tuple[RiskItem, ...]:
        return self.risk_items_by_customer_map.get(customer_id, ())

    def rolling_contact_counts(self, customer_id: str, decision_at) -> tuple[int, int]:
        return self.rolling_counts_map.get(customer_id, (0, 0))

    def has_active_claim(self, customer_id: str, window_id: date) -> bool:
        return (customer_id, window_id) in self.active_claims

    def open_candidates_for_customer(self, customer_id: str, decision_at, exclude_risk_id: str):
        return self.open_candidates_map.get(customer_id, ())


@pytest.fixture()
def fake_ledger() -> FakeLedgerView:
    return FakeLedgerView()


@pytest.fixture()
def make_risk_item():
    def _make(
        risk_id: str = "risk-1",
        source: str = "abandoned_checkout",
        amount_paise: int = 15_000,
        root_cause: str = "price_hesitation",
        detected_at: dt.datetime = DETECTED_AT,
    ) -> RiskItem:
        return RiskItem(
            risk_id=risk_id, source=source, amount_paise=amount_paise,
            root_cause=root_cause, detected_at=detected_at,
        )

    return _make


@pytest.fixture()
def make_request():
    def _make(
        agent_id: str = "cart_recovery_agent",
        customer_id: str = "cust-1",
        risk_id: str = "risk-1",
        intent: str = "cart_recovery",
        requested_channel: str = "whatsapp",
        requested_max_incentive_bps: int = 500,
        issued_at: dt.datetime = ISSUED_AT,
    ) -> GrantRequest:
        return GrantRequest(
            request_id=uuid4(), agent_id=agent_id, customer_id=customer_id, risk_id=risk_id,
            intent=intent, requested_channel=requested_channel,
            requested_max_incentive_bps=requested_max_incentive_bps, issued_at=issued_at,
            signature="test-signature-not-verified-in-policy-tests",
        )

    return _make


@pytest.fixture()
def make_candidate(make_request, make_risk_item):
    def _make(
        customer_id: str = "cust-1",
        proposed_send_after: dt.datetime = dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        risk_item: RiskItem | None = None,
        request: GrantRequest | None = None,
        windows_deferred: int = 0,
        **request_kwargs,
    ) -> Candidate:
        from sampark.allocator.candidate import build_candidate

        item = risk_item if risk_item is not None else make_risk_item()
        req = request if request is not None else make_request(
            customer_id=customer_id, risk_id=item.risk_id, **request_kwargs
        )
        candidate = build_candidate(req, item, customer_id, proposed_send_after)
        if windows_deferred:
            for _ in range(windows_deferred):
                candidate = candidate.aged()
        return candidate

    return _make


@pytest.fixture()
def policy_context(fake_ledger):
    def _make(decision_at: dt.datetime = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)):
        return PolicyContext(ledger=fake_ledger, decision_at=decision_at)

    return _make
