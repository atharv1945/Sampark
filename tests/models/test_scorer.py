"""ModelBackedScorer -- deterministic fallback to the Phase 4 heuristic,
per spec section 12.3's graceful-degradation requirement.

`build_scorer()` against the REAL committed artifact today always
returns HeuristicScorer -- both models report unavailable (see
tests/models/test_uplift.py, test_fatigue_hazard.py). These tests also
prove the fallback path itself works for a hypothetically-valid or
hypothetically-corrupt artifact, independent of what the real one says.
"""

from __future__ import annotations

import datetime as dt
import sys
from uuid import uuid4

from sampark.allocator import greedy
from sampark.allocator.candidate import build_candidate
from sampark.allocator.scorer import HeuristicScorer
from sampark.budget.store import InMemoryGrantIssuer, InMemoryMediationLedger
from sampark.contracts import GrantRequest, RiskItem
from sampark.models.artifact import ModelArtifact
from sampark.models.fatigue_hazard import FatigueHazardModel
from sampark.models.scorer import ModelBackedScorer, build_scorer
from sampark.models.uplift import UpliftModel

DECISION_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)
DETECTED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)


def _make_candidate(risk_id: str, customer_id: str, amount_paise: int, bps: int):
    item = RiskItem(
        risk_id=risk_id, source="abandoned_checkout", amount_paise=amount_paise,
        root_cause="price_hesitation", detected_at=DETECTED_AT,
    )
    request = GrantRequest(
        request_id=uuid4(), agent_id="cart_recovery_agent", customer_id=customer_id, risk_id=risk_id,
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=bps,
        issued_at=DETECTED_AT, signature="sig",
    )
    return build_candidate(request, item, customer_id, DECISION_AT + dt.timedelta(hours=3))


def _make_ledger(*candidates):
    by_customer: dict[str, list[RiskItem]] = {}
    for candidate in candidates:
        by_customer.setdefault(candidate.customer_id, []).append(candidate.risk_item)
    return InMemoryMediationLedger(
        {cid: tuple(items) for cid, items in by_customer.items()},
        merchant_budget_paise_per_window=1_000_000_000,
    )


def test_build_scorer_against_the_real_artifact_falls_back_to_heuristic():
    """The honest, current-dataset result: build_scorer() returns
    HeuristicScorer because sampark/models/artifact_data.py reports both
    models unavailable."""
    scorer = build_scorer()
    assert isinstance(scorer, HeuristicScorer)


def test_build_scorer_falls_back_when_artifact_module_is_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "sampark.models.artifact_data", raising=False)
    monkeypatch.setitem(sys.modules, "sampark.models.artifact_data", None)
    scorer = build_scorer()
    assert isinstance(scorer, HeuristicScorer)


def test_model_backed_scorer_used_when_artifact_is_valid(monkeypatch):
    """Constructs a hypothetically-valid artifact directly (bypassing
    training) to prove build_scorer WOULD use the model if one existed
    -- this is what makes "falls back because unavailable" a real
    conditional, not a hardcoded return."""
    valid_artifact = ModelArtifact(
        seed=42, uplift_available=True, uplift_reason=None,
        uplift_model=UpliftModel(
            treated_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.5},
            control_response_by_bucket={("abandoned_checkout", "price_hesitation"): 0.2},
        ),
        fatigue_hazard_available=True, fatigue_hazard_reason=None,
        fatigue_hazard_model=FatigueHazardModel(hazard_by_bucket={("abandoned_checkout", "price_hesitation", 0): 0.1}),
    )
    monkeypatch.setattr("sampark.models.scorer.load_committed_artifact", lambda: valid_artifact)
    scorer = build_scorer()
    assert isinstance(scorer, ModelBackedScorer)
    assert scorer.artifact is valid_artifact


def test_degraded_run_produces_identical_allocation_to_a_direct_heuristic_run():
    """spec section 12.3: 'Recovery drops; compliance does not' -- the
    allocator's admission/ranking/issuance behavior under the fallback
    scorer must be indistinguishable from a run that used
    HeuristicScorer directly (which is exactly what the fallback
    constructs), not merely 'similar'."""
    winner = _make_candidate(risk_id="risk-winner", customer_id="cust-1", amount_paise=1_000_000, bps=500)
    loser = _make_candidate(risk_id="risk-loser", customer_id="cust-1", amount_paise=10_000, bps=500)

    fallback_scorer = build_scorer()  # real artifact -> HeuristicScorer, proven above
    ledger_a = _make_ledger(winner, loser)
    ledger_b = _make_ledger(winner, loser)

    outcomes_fallback = greedy.allocate_window(
        (winner, loser), ledger_a, InMemoryGrantIssuer(), DECISION_AT, 0, scorer=fallback_scorer
    )
    outcomes_direct = greedy.allocate_window(
        (winner, loser), ledger_b, InMemoryGrantIssuer(), DECISION_AT, 0, scorer=HeuristicScorer()
    )

    for a, b in zip(
        sorted(outcomes_fallback, key=lambda o: o.candidate.risk_item.risk_id),
        sorted(outcomes_direct, key=lambda o: o.candidate.risk_item.risk_id),
    ):
        assert a.outcome_kind == b.outcome_kind
        assert a.reason_code == b.reason_code
        assert a.score == b.score
