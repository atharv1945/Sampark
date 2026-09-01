"""T-21 — no secrets in any payload, over the FULL event vocabulary
(Phase 5A §10).

This is the same enforced regex sampark.audit.canonical already applies
to every payload string (§4.3 rule 3 / §10 — "the same enforced
constraint") — this file exercises it against one instance of every
event type this module emits, plus an explicit deny-list scan for
secret-shaped KEYS, so a future payload field named e.g. "api_token"
would be caught even if its (as-yet-unwritten) VALUE happened to be
ASCII-safe.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid

from sampark.allocator.candidate import build_candidate
from sampark.allocator.outcomes import AllocationOutcome, OutcomeKind
from sampark.audit import emit
from sampark.audit.canonical import canonical_bytes
from sampark.contracts import Agent, AgentState, DecisionOutcome, Grant, GrantDecision, GrantRequest, GrantState, RiskItem

ISSUED_AT = dt.datetime(2025, 9, 10, 6, 0, tzinfo=dt.timezone.utc)

_SECRET_KEY_RE = re.compile(r"(?i)(secret|password|token|private|authorization|api_key|apikey)")

# Names an .env.example holder — never a value, never printed, matching
# CLAUDE.md §8. This is a name-shape check only.
_ENV_VAR_NAME_FRAGMENTS = ("RAZORPAY_KEY", "ANTHROPIC_API_KEY", "POSTGRES_PASSWORD", "REDIS")


def _all_sample_events() -> list:
    request = GrantRequest(
        request_id=uuid.uuid4(), agent_id="cart_recovery_agent", customer_id="cust-1", risk_id="risk-1",
        intent="cart_recovery", requested_channel="whatsapp", requested_max_incentive_bps=500,
        issued_at=ISSUED_AT, signature="c2lnbmF0dXJlLWJ5dGVz",  # base64-looking, deliberately
    )
    item = RiskItem(risk_id="risk-1", source="abandoned_checkout", amount_paise=500_000,
                     root_cause="price_hesitation", detected_at=ISSUED_AT)
    candidate = build_candidate(request, item, "cust-1", dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc))
    grant = Grant(
        grant_id=uuid.uuid4(), channel="whatsapp", incentive_ceiling_paise=25_000,
        send_after=dt.datetime(2025, 9, 10, 12, 0, tzinfo=dt.timezone.utc),
        expires_at=dt.datetime(2025, 9, 10, 14, 0, tzinfo=dt.timezone.utc), state=GrantState.RESERVED,
    )
    granted_outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.GRANTED, reason_code=None, next_eligible_at=None,
        grant=grant, fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
        effective_incentive_bps=500,
    )
    denied_outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DENIED, reason_code="policy.opt_out_active",
        next_eligible_at=None, grant=None, fact_unavailable_reason_codes=("fact_unavailable.rto_flag",),
        score=None, rescheduled_candidate=None,
    )
    deferred_outcome = AllocationOutcome(
        candidate=candidate, outcome_kind=OutcomeKind.DEFERRED, reason_code="budget.contact_cap_24h",
        next_eligible_at=dt.datetime(2025, 9, 11, 3, 30, tzinfo=dt.timezone.utc), grant=None,
        fact_unavailable_reason_codes=(), score=None, rescheduled_candidate=None,
    )
    scope_decision = GrantDecision(
        decision_id=uuid.uuid4(), request_id=request.request_id, outcome=DecisionOutcome.DENIED,
        reason_code="scope.channel_not_allowed", human_readable=None, next_eligible_at=None, grant=None,
    )
    agent = Agent(agent_id="agent-1", public_key="cHVibGljLWtleQ==", publisher="Acme", state=AgentState.ACTIVE, strike_count=0)

    return [
        emit.event_for_request_received(request),
        emit.event_for_denied_on_scope(scope_decision, request, ISSUED_AT),
        emit.event_for_decision(denied_outcome, ISSUED_AT),
        emit.event_for_decision(deferred_outcome, ISSUED_AT),
        emit.event_for_grant_reserved(granted_outcome, uuid.uuid4(), uuid.uuid4()),
        emit.event_for_grant_executing(grant, request, grant.send_after),
        emit.event_for_grant_confirmed(grant, request, grant.send_after, actual_spend_paise=20_000),
        emit.event_for_grant_rolled_back(grant, request, grant.send_after),
        emit.event_for_grant_expired(grant.grant_id, request.request_id, grant.expires_at),
        emit.event_for_agent_registered(agent, ISSUED_AT),
        emit.event_for_agent_struck(agent.model_copy(update={"strike_count": 1}), "scope.channel_not_allowed", ISSUED_AT, request),
        emit.event_for_agent_revoked(agent.model_copy(update={"strike_count": 3, "state": AgentState.REVOKED}), ISSUED_AT),
        # Razorpay product integration. Included here so the privacy rule is
        # exercised over the FULL vocabulary rather than the pre-integration
        # one — this is the only event type built from an external payload
        # that really does carry a customer's phone and email, so it is the
        # one with the most to leak. It carries neither: identity reaches it
        # only as the hash-derived customer_id.
        emit.event_for_payment_risk_detected(_razorpay_opportunity()),
    ]


def _razorpay_opportunity():
    from sampark.integrations.normalize import normalize_payment
    from sampark.integrations.provenance import McpCallReceipt, Provenance

    return normalize_payment(
        {
            "id": "pay_PRIV00000001", "entity": "payment", "amount": 100_000, "currency": "INR",
            "status": "failed", "order_id": "order_PRIV01", "method": "card",
            "email": "priya.sharma@example.com", "contact": "+91 98765 43210",
            "error_code": "GATEWAY_ERROR", "error_reason": "issuer_down",
            "error_source": "bank", "error_step": "payment_authorization",
            "created_at": 1788000000,
        },
        Provenance.from_mcp(
            McpCallReceipt("fetch_payment", "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"),
            observed_at=ISSUED_AT, reference="pay_PRIV00000001",
        ),
        payment_link_id="plink_PRIV1",
    )


def test_no_raw_contact_detail_from_a_razorpay_payment_reaches_a_payload():
    """T-21 extended to the one event type built from an EXTERNAL payload.
    The source payment carries a real email and phone; the audit event must
    carry neither, in any form."""
    event = emit.event_for_payment_risk_detected(_razorpay_opportunity())
    blob = repr(event.payload)
    for secret in ("priya.sharma@example.com", "9876543210", "98765 43210", "+91"):
        assert secret not in blob, f"raw contact detail leaked into the payload: {secret!r}"
    assert event.payload["customer_id"].startswith("cust_")


def _walk_payload_strings(value, path=""):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_payload_strings(v, f"{path}.{k}")
            yield f"{path}.<key>", k
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            yield from _walk_payload_strings(item, f"{path}[{i}]")


def test_no_secret_shaped_keys_in_any_payload():
    for event in _all_sample_events():
        for path, key in ((p, p.rsplit(".", 1)[-1]) for p, _ in _walk_payload_strings(event.payload)):
            assert not _SECRET_KEY_RE.search(key), f"{event.event_type}: suspicious key {key!r} at {path}"


def test_no_env_var_name_fragments_appear_in_any_payload_value():
    for event in _all_sample_events():
        for path, value in _walk_payload_strings(event.payload):
            for fragment in _ENV_VAR_NAME_FRAGMENTS:
                assert fragment not in value, f"{event.event_type}: {path} contains env-var-name fragment {fragment!r}"


def test_every_payload_string_is_a_controlled_ascii_identifier():
    # This IS the enforced privacy/determinism rule (Phase 5A §4.3 rule
    # 3 / §10) — canonical_bytes() raises on anything else. Exercised
    # here over the full event vocabulary at once, not one event at a
    # time as test_canonical.py does.
    for event in _all_sample_events():
        canonical_bytes(event)  # must not raise


def test_agent_signature_and_public_key_are_not_subject_to_the_payload_identifier_rule():
    # agent_signature is base64 (may contain '+', '/', '=') and is a
    # TOP-LEVEL AuditEvent field, not a payload value — the identifier
    # regex must never be applied to it (Phase 5A §4.3's own carve-out).
    events = _all_sample_events()
    signed = [e for e in events if e.agent_signature is not None]
    assert signed, "fixture must include at least one signed event"
    for event in signed:
        canonical_bytes(event)  # must not raise despite a base64-shaped signature
