"""The Razorpay product API, against a live FastAPI app and real PostgreSQL.

The Razorpay gateway is stubbed at the module boundary (`sampark.integrations
.gateway`), because Razorpay is an external service and the suite must be
deterministic and runnable with no credentials. Everything on SAMPARK's side
of that boundary is real: the isolated schema, the mediation path, the
SERIALIZABLE issuance, the audit chain, and the SSE stream.

The webhook tests are NOT stubbed at all — they build a genuine HMAC-SHA256
signature and post a real Razorpay-shaped envelope, because the verification
is the security boundary and a stub there would test nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from sampark.demo import isolation
from sampark.integrations import gateway, webhook
from sampark.integrations.provenance import McpCallReceipt, Provenance
from sim.persistence import PostgresConfig, PostgresConfigError

pytestmark = pytest.mark.postgres

BASE = "/api/integrations/razorpay"
SECRET = "test-webhook-secret-for-the-api-tests"
FAILED_AT = dt.datetime(2026, 9, 1, 6, 5, tzinfo=dt.timezone.utc)  # 11:35 IST


def _mcp_provenance(operation: str, reference: str | None = None) -> Provenance:
    return Provenance.from_mcp(
        McpCallReceipt(operation, "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"),
        observed_at=dt.datetime.now(dt.timezone.utc), reference=reference,
    )


def _payment(payment_id: str, amount: int, contact: str = "+919876511111") -> dict:
    return {
        "id": payment_id, "entity": "payment", "amount": amount, "currency": "INR",
        "status": "failed", "order_id": "order_API01", "method": "card",
        "email": payment_id.lower() + "@example.com", "contact": contact,
        "error_code": "GATEWAY_ERROR", "error_reason": "issuer_down",
        "error_source": "bank", "error_step": "payment_authorization",
        "created_at": int(FAILED_AT.timestamp()),
    }


class FakeRazorpay:
    """A stand-in Razorpay ledger. Records what was asked for so the tests can
    assert the ADAPTER's behaviour rather than Razorpay's."""

    def __init__(self) -> None:
        self.links: dict[str, dict] = {}
        self.failed: dict[str, dict] = {}   # link id -> payment entity
        self.created_amounts: list[int] = []

    def create(self, reference_id=None, amount_paise=None):
        amount = amount_paise if amount_paise is not None else gateway.demo_amount_paise()
        self.created_amounts.append(amount)
        link_id = "plink_FAKE" + str(len(self.links) + 1).zfill(8)
        payload = {
            "id": link_id, "short_url": "https://rzp.io/rzp/fake" + str(len(self.links) + 1),
            "status": "created", "amount": amount, "currency": "INR",
            "reference_id": reference_id or "ref",
        }
        self.links[link_id] = payload
        return gateway.GatewayResult(payload=payload, provenance=_mcp_provenance("create_payment_link", link_id))

    def fetch_link(self, link_id):
        payload = dict(self.links[link_id])
        payment = self.failed.get(link_id)
        payload["payments"] = (
            [{"payment_id": payment["id"], "status": "failed", "method": "card", "created_at": 1}]
            if payment else []
        )
        return gateway.GatewayResult(payload=payload, provenance=_mcp_provenance("fetch_payment_link", link_id))

    def find_failed(self, link_id, reference_id=None):
        payment = self.failed.get(link_id)
        if payment is None:
            return gateway.FailedPaymentLookup(
                payment=None, provenance=None, matcher=None, fallback_reason=None,
                link_status="created", attempts_seen=0,
            )
        return gateway.FailedPaymentLookup(
            payment=payment, provenance=_mcp_provenance("fetch_payment", payment["id"]),
            matcher="payment_link.payments", fallback_reason=None,
            link_status="created", attempts_seen=1,
        )

    def fail(self, link_id: str, amount: int, contact: str = "+919876511111") -> dict:
        payment = _payment("pay_FAKE" + link_id[-4:].zfill(8), amount, contact)
        self.failed[link_id] = payment
        return payment


@pytest.fixture()
def api(monkeypatch):
    try:
        config = PostgresConfig.from_env()
    except PostgresConfigError as exc:
        pytest.skip("Postgres not configured: " + str(exc))
    try:
        probe = psycopg.connect(config.conninfo(), connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip("Postgres not reachable: " + str(exc))
    probe.autocommit = True

    fake = FakeRazorpay()
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(gateway, "create_demo_payment_link", fake.create)
    monkeypatch.setattr(gateway, "fetch_payment_link", fake.fetch_link)
    monkeypatch.setattr(gateway, "find_failed_payment", fake.find_failed)

    from ui.app import create_app

    app = create_app(config=config)
    with TestClient(app) as client:
        yield client, fake, probe
    probe.close()


def start(client) -> dict:
    response = client.post(BASE + "/session")
    assert response.status_code == 200, response.text
    return response.json()


# --- health -----------------------------------------------------------------


def test_health_reports_configuration_and_the_protected_chain(api):
    client, _fake, _conn = api
    body = client.get(BASE + "/health").json()
    assert body["transport"]["environment"] == "test"
    assert body["transport"]["amount_paise"] == gateway.demo_amount_paise()
    assert body["webhook_configured"] is True
    assert isinstance(body["protected_public_audit_event_count"], int)
    assert "mcp_probe" not in body, "an unprobed health call must not imply the server answered"


# --- lifecycle --------------------------------------------------------------


def test_a_session_creates_an_isolated_schema_and_registers_the_agent(api):
    client, _fake, conn = api
    body = start(client)
    assert body["demo_schema"].startswith(isolation.SCHEMA_PREFIX)
    assert body["agent_id"] == "payment_retry_agent"
    assert body["model_degraded"] is True and body["scorer"] == "HeuristicScorer"

    events = client.get(BASE + "/events").json()
    assert [e["event_type"] for e in events][:2] == ["agent.registered", "model.degraded"]


def test_every_endpoint_refuses_before_a_session_exists(api):
    client, _fake, _conn = api
    client.post(BASE + "/reset")
    for method, path in [
        ("post", "/payment-link"), ("get", "/payment-link"), ("post", "/ingest"),
        ("post", "/provider-failure"), ("get", "/events"), ("get", "/verify"),
        ("get", "/explain/request/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12),
    ]:
        response = getattr(client, method)(BASE + path)
        assert response.status_code == 409, path + " -> " + str(response.status_code)


def test_reset_drops_the_schema(api):
    client, _fake, conn = api
    schema = start(client)["demo_schema"]
    assert schema in isolation.list_demo_schemas(conn)
    assert client.post(BASE + "/reset").json()["dropped_schema"] == schema
    assert schema not in isolation.list_demo_schemas(conn)


# --- payment links ----------------------------------------------------------


def test_the_headline_link_is_created_for_1000_inr(api):
    client, fake, _conn = api
    start(client)
    body = client.post(BASE + "/payment-link", json={"role": "headline"}).json()
    assert body["role"] == "headline"
    assert body["amount_paise"] == gateway.demo_amount_paise() == 100_000
    assert body["amount_inr"] == "1,000.00"
    assert body["provenance"]["environment"] == "test"
    assert fake.created_amounts == [100_000]


def test_the_contrast_link_uses_the_contrast_amount_and_an_override(api):
    client, fake, _conn = api
    start(client)
    client.post(BASE + "/payment-link", json={"role": "contrast"})
    assert fake.created_amounts[-1] == gateway.contrast_amount_paise()
    client.post(BASE + "/payment-link", json={"role": "contrast", "amount_inr": 7500})
    assert fake.created_amounts[-1] == 750_000


@pytest.mark.parametrize(
    "body", [{"role": "nonsense"}, {"amount_inr": 0}, {"amount_inr": -5}, {"unexpected": 1}]
)
def test_a_malformed_payment_link_request_is_refused(api, body):
    client, _fake, _conn = api
    start(client)
    assert client.post(BASE + "/payment-link", json=body).status_code in (400, 422)


# --- ingestion --------------------------------------------------------------


def test_ingest_before_anyone_pays_reports_no_failure_rather_than_inventing_one(api):
    client, _fake, _conn = api
    start(client)
    client.post(BASE + "/payment-link", json={"role": "headline"})
    body = client.post(BASE + "/ingest").json()
    assert body["ingested"] is False
    assert "no failed payment attempt" in body["reason"]
    assert body["results"] == []
    assert body["skipped"][0]["attempts_seen"] == 0


def test_a_failed_1000_rupee_payment_is_detected_and_declined(api):
    client, fake, _conn = api
    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "headline"}).json()
    fake.fail(link["payment_link_id"], 100_000)

    body = client.post(BASE + "/ingest").json()
    assert body["ingested"] is True
    result = body["results"][0]
    assert result["role"] == "headline"
    assert result["outcome"] == "DENIED"
    assert result["reason_code"] == "allocation.negative_expected_net"
    assert result["opportunity"]["amount_inr"] == "1,000.00"
    assert result["opportunity"]["provenance"]["transport"] == "mcp"


def test_both_payments_are_decided_and_the_split_is_visible(api):
    client, fake, _conn = api
    start(client)
    headline = client.post(BASE + "/payment-link", json={"role": "headline"}).json()
    contrast = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(headline["payment_link_id"], 100_000, contact="+919876512001")
    fake.fail(contrast["payment_link_id"], gateway.contrast_amount_paise(), contact="+919876512002")

    results = {r["role"]: r for r in client.post(BASE + "/ingest").json()["results"]}
    assert results["headline"]["outcome"] == "DENIED"
    assert results["contrast"]["outcome"] == "GRANTED"
    assert results["contrast"]["delivery"]["delivered"] is True


def test_ingesting_twice_makes_no_second_decision(api):
    client, fake, conn = api
    schema = start(client)["demo_schema"]
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())

    client.post(BASE + "/ingest")
    before = len(client.get(BASE + "/events?limit=5000").json())
    second = client.post(BASE + "/ingest").json()
    assert second["results"][0]["duplicate"] is True
    assert len(client.get(BASE + "/events?limit=5000").json()) == before


# --- the audit stream -------------------------------------------------------


def test_every_served_event_corresponds_to_a_real_audit_row(api):
    """The trace-integrity rule, live: nothing the product API streams was
    synthesised."""
    client, fake, conn = api
    schema = start(client)["demo_schema"]
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())
    client.post(BASE + "/ingest")

    served = client.get(BASE + "/events?limit=5000").json()
    assert served
    with conn.cursor() as cur:
        cur.execute("SET search_path TO " + schema)
        cur.execute("SELECT event_id::text, event_type, prev_hash FROM audit_events")
        real = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
        cur.execute("SET search_path TO public")

    assert len(served) == len(real)
    for event in served:
        assert event["event_id"] in real
        assert real[event["event_id"]] == (event["event_type"], event["prev_hash"])


def test_the_served_hash_is_recomputed_and_actually_chains(api):
    from sampark.audit.canonical import GENESIS_HASH

    client, fake, _conn = api
    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())
    client.post(BASE + "/ingest")

    served = client.get(BASE + "/events?limit=5000").json()
    assert served[0]["prev_hash"] == GENESIS_HASH
    for previous, current in zip(served, served[1:]):
        assert current["prev_hash"] == previous["hash"]


def test_the_sse_stream_delivers_the_same_rows(api):
    client, fake, _conn = api
    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())
    client.post(BASE + "/ingest")

    with client.stream("GET", BASE + "/stream") as response:
        assert response.status_code == 200
        frames = "".join(response.iter_text())
    streamed = [json.loads(line[5:]) for line in frames.splitlines() if line.startswith("data: ")]
    streamed = [e for e in streamed if "event_id" in e]
    assert [e["event_id"] for e in streamed] == [
        e["event_id"] for e in client.get(BASE + "/events?limit=5000").json()
    ]


def test_verify_reports_a_valid_chain(api):
    client, fake, _conn = api
    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())
    client.post(BASE + "/ingest")

    body = client.get(BASE + "/verify").json()
    assert body["valid"] is True
    assert body["genesis_ok"] and body["linkage_ok"]
    assert body["missing_grant_reservations"] == []


def test_explain_reuses_the_phase_5_engine(api):
    client, fake, _conn = api
    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "headline"}).json()
    fake.fail(link["payment_link_id"], 100_000)
    request_id = client.post(BASE + "/ingest").json()["results"][0]["request_id"]

    body = client.get(BASE + "/explain/request/" + request_id).json()
    assert body["outcome"] == "DENIED"
    assert "allocation.negative_expected_net" in body["explanation"]
    assert body["events"], "the raw events the sentence came from must be returned too"

    assert client.get(BASE + "/explain/request/not-a-uuid").status_code == 400
    assert client.get(
        BASE + "/explain/request/11111111-2222-3333-4444-555555555555"
    ).status_code == 404


# --- the webhook (NOT stubbed: this is the security boundary) ---------------


def envelope(payment: dict, event: str = "payment.failed") -> bytes:
    return json.dumps({
        "entity": "event", "account_id": "acc_TEST", "event": event,
        "contains": ["payment"], "created_at": 1788000000,
        "payload": {"payment": {"entity": payment}},
    }).encode("utf-8")


def signed(raw: bytes, secret: str = SECRET, event_id: str = "evt_API01") -> dict:
    return {
        "x-razorpay-signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        "x-razorpay-event-id": event_id,
        "content-type": "application/json",
    }


def test_a_correctly_signed_failed_payment_webhook_is_ingested(api):
    client, _fake, _conn = api
    start(client)
    raw = envelope(_payment("pay_HOOKAPI00001", gateway.contrast_amount_paise()))
    response = client.post(BASE + "/webhook", content=raw, headers=signed(raw))
    assert response.status_code == 202
    body = response.json()
    assert body["ingested"] is True
    assert body["outcome"] == "GRANTED"
    assert body["opportunity"]["provenance"]["transport"] == "webhook"


def test_an_unsigned_webhook_is_refused_and_never_reaches_the_chain(api):
    client, _fake, _conn = api
    start(client)
    before = len(client.get(BASE + "/events?limit=5000").json())
    raw = envelope(_payment("pay_UNSIGNED0001", 400_000))

    assert client.post(BASE + "/webhook", content=raw).status_code == 401
    assert client.post(
        BASE + "/webhook", content=raw, headers={"x-razorpay-signature": "deadbeef"}
    ).status_code == 401
    assert client.post(
        BASE + "/webhook", content=raw, headers=signed(raw, secret="wrong-secret")
    ).status_code == 401

    assert len(client.get(BASE + "/events?limit=5000").json()) == before


def test_a_tampered_body_is_refused(api):
    client, _fake, _conn = api
    start(client)
    raw = envelope(_payment("pay_TAMPER000001", 400_000))
    headers = signed(raw)
    tampered = raw.replace(b'"amount": 400000', b'"amount": 999999')
    assert client.post(BASE + "/webhook", content=tampered, headers=headers).status_code == 401


def test_a_duplicate_webhook_delivery_creates_no_second_recovery_action(api):
    """Razorpay retries a delivery it did not see acknowledged. The retry must
    produce no second decision, no second grant and no second send."""
    client, _fake, _conn = api
    start(client)
    raw = envelope(_payment("pay_DUPHOOK00001", gateway.contrast_amount_paise()))

    first = client.post(BASE + "/webhook", content=raw, headers=signed(raw)).json()
    assert first["ingested"] is True and first.get("duplicate") is not True
    count = len(client.get(BASE + "/events?limit=5000").json())

    second = client.post(BASE + "/webhook", content=raw, headers=signed(raw)).json()
    assert second["duplicate"] is True
    assert len(client.get(BASE + "/events?limit=5000").json()) == count


def test_a_verified_event_this_adapter_ignores_is_accepted_without_ingesting(api):
    client, _fake, _conn = api
    start(client)
    payment = _payment("pay_CAPTURED0001", 400_000)
    payment["status"] = "captured"
    raw = envelope(payment, event="payment.captured")
    body = client.post(BASE + "/webhook", content=raw, headers=signed(raw)).json()
    assert body["accepted"] is True and body["ingested"] is False


def test_a_webhook_with_no_open_session_is_accepted_but_not_ingested(api):
    """202 rather than an error, so Razorpay's retry loop is not driven by a
    demo that simply is not listening yet."""
    client, _fake, _conn = api
    client.post(BASE + "/reset")
    raw = envelope(_payment("pay_NOSESSION001", 400_000))
    response = client.post(BASE + "/webhook", content=raw, headers=signed(raw))
    assert response.status_code == 202
    assert response.json()["ingested"] is False


def test_a_verified_but_malformed_body_is_422_not_401(api):
    """It really came from the secret holder — it is just not an envelope.
    Collapsing the two would misreport a Razorpay format change as an attack."""
    client, _fake, _conn = api
    start(client)
    raw = b'{"event": "payment.failed", "payload": {}}'
    assert client.post(BASE + "/webhook", content=raw, headers=signed(raw)).status_code == 422


# --- failure injection ------------------------------------------------------


def test_arming_the_provider_rolls_the_next_grant_back(api):
    client, fake, _conn = api
    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())

    assert client.post(BASE + "/provider-failure", json={"mode": "hard_down"}).json()["armed"] is True
    result = client.post(BASE + "/ingest").json()["results"][0]
    assert result["outcome"] == "ROLLED_BACK"
    assert result["delivery"]["rolled_back"] is True
    assert "grant.rolled_back" in [e["event_type"] for e in client.get(BASE + "/events").json()]
    assert client.get(BASE + "/verify").json()["valid"] is True


def test_an_unknown_provider_failure_mode_is_refused(api):
    client, _fake, _conn = api
    start(client)
    assert client.post(BASE + "/provider-failure", json={"mode": "explode"}).status_code == 400


# --- isolation --------------------------------------------------------------


def test_the_protected_public_chain_is_unchanged_by_the_whole_api_flow(api):
    client, fake, conn = api
    before = isolation.public_audit_fingerprint(conn)

    start(client)
    link = client.post(BASE + "/payment-link", json={"role": "contrast"}).json()
    fake.fail(link["payment_link_id"], gateway.contrast_amount_paise())
    client.post(BASE + "/ingest")
    raw = envelope(_payment("pay_ISOLATION001", 400_000, contact="+919876519999"))
    client.post(BASE + "/webhook", content=raw, headers=signed(raw))
    client.post(BASE + "/reset")

    assert isolation.public_audit_fingerprint(conn) == before
