"""The MCP client and the transport-selecting gateway — OFFLINE.

Not one test here touches the network. Razorpay's MCP server and REST API are
external services; a suite that calls them would be non-deterministic, would
consume a merchant's test ledger on every CI run, and would fail closed on a
machine with no credentials. The single live call this integration performs is
the operator-run `scripts/verify_razorpay_product_flow.py`, exactly as Phase 0
kept `scripts/verify_razorpay_payment_link.py` out of the suite.

What IS tested here is everything that can go wrong without a network: the
JSON-RPC framing, both response encodings, every error path, the fallback
rule, and — most importantly — that a fallback never keeps an MCP label.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sampark.integrations import gateway
from sampark.integrations.provenance import Transport
from sampark.integrations.razorpay_mcp import (
    RazorpayMcpClient,
    RazorpayMcpConfig,
    RazorpayMcpConfigError,
    RazorpayMcpError,
    RazorpayMcpUnavailable,
)

CONFIG = RazorpayMcpConfig(url="https://mcp.example.invalid/mcp", token="test-token-never-real")


class FakeTransport:
    """Stands in for `RazorpayMcpClient._post`. Records what was sent so the
    JSON-RPC envelope itself can be asserted."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.sent: list[dict] = []

    def __call__(self, body: dict) -> Any:
        self.sent.append(body)
        if not self.responses:
            return None
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def result(payload: Any, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def initialized() -> dict:
    return result({"serverInfo": {"name": "razorpay-mcp-server", "version": "1.0.0"},
                   "protocolVersion": "2025-06-18", "capabilities": {}})


def tool_text(obj: Any) -> dict:
    return result({"content": [{"type": "text", "text": json.dumps(obj)}]})


def client_with(*responses: Any) -> tuple[RazorpayMcpClient, FakeTransport]:
    client = RazorpayMcpClient(CONFIG)
    fake = FakeTransport(*responses)
    client._post = fake  # type: ignore[method-assign]
    return client, fake


# --- configuration ----------------------------------------------------------


def test_config_requires_a_token_and_never_repr_s_it(monkeypatch):
    monkeypatch.delenv("RAZORPAY_MCP_TOKEN", raising=False)
    with pytest.raises(RazorpayMcpConfigError):
        RazorpayMcpConfig.from_env()

    monkeypatch.setenv("RAZORPAY_MCP_TOKEN", "super-secret-token-value")
    config = RazorpayMcpConfig.from_env()
    assert "super-secret-token-value" not in repr(config)
    assert "<redacted>" in repr(config)
    assert config.url == gateway.DEFAULT_MCP_URL if False else config.host == "mcp.razorpay.com"


def test_config_honours_an_override_url(monkeypatch):
    monkeypatch.setenv("RAZORPAY_MCP_TOKEN", "t")
    monkeypatch.setenv("RAZORPAY_MCP_URL", "https://mcp.example.test/mcp")
    assert RazorpayMcpConfig.from_env().host == "mcp.example.test"


# --- the JSON-RPC envelope --------------------------------------------------


def test_initialize_then_notify_then_call_is_the_wire_sequence():
    client, fake = client_with(initialized(), None, tool_text({"id": "plink_1"}))
    payload, receipt = client.call_tool("create_payment_link", {"amount": 100_000})

    methods = [message["method"] for message in fake.sent]
    assert methods == ["initialize", "notifications/initialized", "tools/call"]
    assert "id" not in fake.sent[1], "a notification must carry no JSON-RPC id"
    assert fake.sent[2]["params"] == {"name": "create_payment_link", "arguments": {"amount": 100_000}}
    assert payload == {"id": "plink_1"}
    assert receipt.tool_name == "create_payment_link"
    assert receipt.server_name == "razorpay-mcp-server"


def test_an_sse_framed_response_is_parsed():
    """MCP streamable HTTP may answer as `text/event-stream`. The last `data:`
    line is the response to our single request."""
    frame = "event: message\ndata: " + json.dumps(initialized()) + "\n\n"
    assert RazorpayMcpClient._parse_body("text/event-stream", frame) == initialized()


def test_structured_content_is_preferred_over_the_text_block():
    client, _ = client_with(
        initialized(), None,
        result({"structuredContent": {"id": "plink_S"},
                "content": [{"type": "text", "text": '{"id": "stale"}'}]}),
    )
    payload, _receipt = client.call_tool("fetch_payment_link", {})
    assert payload == {"id": "plink_S"}


# --- error paths ------------------------------------------------------------


def test_a_jsonrpc_error_raises_and_mints_no_receipt():
    client, _ = client_with(
        initialized(), None,
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "bad params"}},
    )
    with pytest.raises(RazorpayMcpError) as exc:
        client.call_tool("create_payment_link", {})
    assert "bad params" in str(exc.value)


def test_a_tool_level_isError_raises_and_mints_no_receipt():
    """The transport succeeded but the TOOL failed. A receipt here would let
    the UI claim MCP performed an operation it refused."""
    client, _ = client_with(
        initialized(), None,
        result({"isError": True, "content": [{"type": "text", "text": "amount too small"}]}),
    )
    with pytest.raises(RazorpayMcpError) as exc:
        client.call_tool("create_payment_link", {})
    assert "amount too small" in str(exc.value)


def test_an_empty_response_raises():
    client, _ = client_with(initialized(), None, None)
    with pytest.raises(RazorpayMcpError):
        client.call_tool("fetch_payment", {})


def test_a_non_json_tool_result_raises_rather_than_returning_a_string():
    client, _ = client_with(
        initialized(), None, result({"content": [{"type": "text", "text": "<html>oops</html>"}]})
    )
    with pytest.raises(RazorpayMcpError):
        client.call_tool("fetch_payment", {})


def test_unreachable_is_a_distinct_exception_from_refused():
    """The gateway falls back to REST for `RazorpayMcpUnavailable` and for a
    refusal alike, but the two must stay distinguishable so the fallback
    REASON shown on screen is accurate."""
    assert issubclass(RazorpayMcpUnavailable, RazorpayMcpError)


def test_list_tools_returns_only_names_the_server_actually_returned():
    client, _ = client_with(
        result({"tools": [{"name": "create_payment_link"}, {"name": "fetch_payment"}, {"no_name": 1}]}),
    )
    assert client.list_tools() == ("create_payment_link", "fetch_payment")


# --- the gateway's amounts --------------------------------------------------


def test_the_demo_amount_defaults_to_1000_inr(monkeypatch):
    monkeypatch.delenv("RAZORPAY_DEMO_AMOUNT_INR", raising=False)
    assert gateway.demo_amount_paise() == 100_000
    assert gateway.DEFAULT_DEMO_AMOUNT_INR == 1000


@pytest.mark.parametrize(
    "raw,expected", [("1000", 100_000), ("2500", 250_000), ("", 100_000), ("nonsense", 100_000), ("0", 100)]
)
def test_the_demo_amount_is_configurable_and_never_below_razorpays_minimum(monkeypatch, raw, expected):
    monkeypatch.setenv("RAZORPAY_DEMO_AMOUNT_INR", raw)
    assert gateway.demo_amount_paise() == expected


def test_the_contrast_amount_is_separate_and_above_the_allocator_break_even(monkeypatch):
    """The contrast payment exists only because 1,000 INR is genuinely below
    the frozen allocator's break-even. If that break-even ever moved, this
    test should be the thing that notices."""
    monkeypatch.delenv("RAZORPAY_CONTRAST_AMOUNT_INR", raising=False)
    from sampark.policy.soft import channel_cost, fatigue, recovery_prior

    p_hat = recovery_prior.p_hat("failed_payment", "issuer_downtime", 0)
    break_even = (fatigue.fatigue_cost_paise(0, []) + channel_cost.channel_cost_paise("sms")) / p_hat

    assert gateway.demo_amount_paise() < break_even, (
        "1,000 INR is no longer below break-even; the product page's explanation is now wrong"
    )
    assert gateway.contrast_amount_paise() > break_even, (
        "the contrast amount no longer clears break-even, so the grant path cannot be demonstrated"
    )


# --- transport selection ----------------------------------------------------


def test_transport_status_reports_configuration_and_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("RAZORPAY_MCP_TOKEN", raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake")
    status = gateway.transport_status()
    assert status["mcp_configured"] is False
    assert status["rest_configured"] is True
    assert status["preferred_transport"] == "rest_api"
    assert status["environment"] == "test"
    assert "mcp_reachable" not in status, (
        "transport_status must never imply the server answered - only probe_mcp may"
    )


def test_a_live_key_is_refused_so_rest_is_never_configured_in_live_mode(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_realkey")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "realsecret")
    assert gateway.transport_status()["rest_configured"] is False


def test_probe_reports_unreachable_rather_than_a_fabricated_tool_list(monkeypatch):
    monkeypatch.delenv("RAZORPAY_MCP_TOKEN", raising=False)
    probe = gateway.probe_mcp()
    assert probe["reachable"] is False
    assert probe["tools"] == []
    assert probe["server"] is None
    assert "RAZORPAY_MCP_TOKEN" in probe["reason"]


def test_a_read_falls_back_to_rest_and_the_provenance_says_rest(monkeypatch):
    """The fallback rule, and the property that matters most about it: the
    label follows the transport that ACTUALLY ran."""
    monkeypatch.setenv("RAZORPAY_MCP_TOKEN", "t")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake")

    def unreachable(self, tool_name, arguments):
        raise RazorpayMcpUnavailable("Razorpay MCP server at mcp.example is unreachable: URLError")

    monkeypatch.setattr(RazorpayMcpClient, "call_tool", unreachable)

    from sampark.integrations import razorpay_rest as rest
    from sampark.integrations.provenance import Provenance, RestCallReceipt
    import datetime as dt

    def fake_fetch(config, payment_id):
        return ({"id": payment_id, "status": "failed"}, Provenance.from_rest(
            RestCallReceipt("fetch_payment", "api.razorpay.com"),
            observed_at=dt.datetime.now(dt.timezone.utc), reference=payment_id))

    monkeypatch.setattr(rest, "fetch_payment", fake_fetch)

    out = gateway.fetch_payment("pay_X")
    assert out.transport is Transport.REST_API
    assert out.provenance.as_payload()["transport"] == "rest_api"
    assert "unreachable" in out.fallback_reason


def test_a_read_with_no_mcp_token_says_so_in_the_fallback_reason(monkeypatch):
    monkeypatch.delenv("RAZORPAY_MCP_TOKEN", raising=False)
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake")

    from sampark.integrations import razorpay_rest as rest
    from sampark.integrations.provenance import Provenance, RestCallReceipt
    import datetime as dt

    monkeypatch.setattr(rest, "fetch_payment", lambda config, payment_id: (
        {"id": payment_id},
        Provenance.from_rest(RestCallReceipt("fetch_payment", "api.razorpay.com"),
                             observed_at=dt.datetime.now(dt.timezone.utc)),
    ))
    out = gateway.fetch_payment("pay_X")
    assert out.fallback_reason == "RAZORPAY_MCP_TOKEN is not configured"


def test_when_no_transport_works_the_gateway_raises_rather_than_inventing_a_result(monkeypatch):
    monkeypatch.delenv("RAZORPAY_MCP_TOKEN", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    with pytest.raises(gateway.GatewayUnavailable):
        gateway.fetch_payment("pay_X")


def test_find_failed_payment_returns_none_rather_than_guessing(monkeypatch):
    """Before anyone has paid there is no failed attempt, and the honest
    answer is "none" — never the most recent failed payment on the account,
    which would attribute a stranger's attempt to this link."""
    from sampark.integrations.provenance import Provenance, RestCallReceipt
    import datetime as dt

    provenance = Provenance.from_rest(
        RestCallReceipt("fetch_payment_link", "api.razorpay.com"),
        observed_at=dt.datetime.now(dt.timezone.utc),
    )
    monkeypatch.setattr(gateway, "fetch_payment_link", lambda link_id: gateway.GatewayResult(
        payload={"id": link_id, "status": "created", "payments": []}, provenance=provenance))
    monkeypatch.setattr(gateway, "fetch_recent_payments", lambda count=20: gateway.GatewayResult(
        payload={"items": [{"id": "pay_STRANGER", "status": "failed", "notes": {}}]},
        provenance=provenance))

    lookup = gateway.find_failed_payment("plink_X", reference_id="sampark-headline-abc")
    assert lookup.payment is None
    assert lookup.matcher is None
    assert lookup.attempts_seen == 0


def test_find_failed_payment_matches_on_the_links_own_attempts(monkeypatch):
    from sampark.integrations.provenance import Provenance, RestCallReceipt
    import datetime as dt

    provenance = Provenance.from_rest(
        RestCallReceipt("fetch_payment_link", "api.razorpay.com"),
        observed_at=dt.datetime.now(dt.timezone.utc),
    )
    monkeypatch.setattr(gateway, "fetch_payment_link", lambda link_id: gateway.GatewayResult(
        payload={
            "id": link_id, "status": "created",
            "payments": [
                {"payment_id": "pay_OLD0000000001", "status": "failed", "created_at": 100},
                {"payment_id": "pay_NEW0000000002", "status": "failed", "created_at": 200},
            ],
        }, provenance=provenance))
    monkeypatch.setattr(gateway, "fetch_payment", lambda payment_id: gateway.GatewayResult(
        payload={"id": payment_id, "status": "failed"}, provenance=provenance))

    lookup = gateway.find_failed_payment("plink_X")
    assert lookup.matcher == "payment_link.payments"
    assert lookup.payment["id"] == "pay_NEW0000000002", "the most recent attempt should win"
    assert lookup.attempts_seen == 2


def test_find_failed_payment_matches_on_the_reference_note(monkeypatch):
    """Razorpay copies a payment link's notes onto the payment created from
    it, which is an EXACT match back to this session's own link."""
    from sampark.integrations.provenance import Provenance, RestCallReceipt
    import datetime as dt

    provenance = Provenance.from_rest(
        RestCallReceipt("fetch_all_payments", "api.razorpay.com"),
        observed_at=dt.datetime.now(dt.timezone.utc),
    )
    monkeypatch.setattr(gateway, "fetch_payment_link", lambda link_id: gateway.GatewayResult(
        payload={"id": link_id, "status": "created"}, provenance=provenance))
    monkeypatch.setattr(gateway, "fetch_recent_payments", lambda count=20: gateway.GatewayResult(
        payload={"items": [
            {"id": "pay_OTHER000001", "status": "failed", "notes": {gateway.NOTE_KEY: "someone-else"}},
            {"id": "pay_MINE0000001", "status": "failed", "notes": {gateway.NOTE_KEY: "ref-mine"}},
        ]}, provenance=provenance))

    lookup = gateway.find_failed_payment("plink_X", reference_id="ref-mine")
    assert lookup.matcher == "notes." + gateway.NOTE_KEY
    assert lookup.payment["id"] == "pay_MINE0000001"


# --- no credential ever escapes ---------------------------------------------


def test_no_module_in_the_integration_package_prints_or_logs(monkeypatch):
    """A stray `print` in a transport is how a token reaches a terminal.
    Checked on the AST so a docstring mentioning printing does not trip it."""
    import ast
    import pathlib

    package = pathlib.Path(__file__).resolve().parent.parent.parent / "sampark/integrations"
    offenders = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                offenders.append(path.name)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"debug", "info", "warning", "error", "exception"}:
                    offenders.append(path.name + "::" + node.func.attr)
    assert offenders == [], "sampark/integrations must not print or log: " + repr(offenders)


def test_the_authorization_header_is_never_echoed_back(monkeypatch):
    """Every error message the client can produce is checked against the
    token value, since a message reaches the API response and the screen."""
    token = "tok-must-never-appear-anywhere"
    config = RazorpayMcpConfig(url="https://mcp.example.invalid/mcp", token=token)
    client = RazorpayMcpClient(config)
    client._post = FakeTransport(  # type: ignore[method-assign]
        initialized(), None,
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "denied"}},
    )
    with pytest.raises(RazorpayMcpError) as exc:
        client.call_tool("create_payment_link", {})
    assert token not in str(exc.value)
    assert token not in repr(config)


# --- the ledger check's cache -----------------------------------------------


def test_a_passing_ledger_check_is_cached_but_a_failing_one_is_not(monkeypatch):
    """A live run showed a payment-link creation fall back to REST with
    "REST ledger unreadable" while the very next one went via MCP — a transient
    blip, not a fact about the credentials. Caching the PASS removes that
    downgrade; never caching a FAIL means a blip can never withhold MCP for the
    life of the process."""
    gateway.reset_ledger_check_cache()
    calls = {"n": 0}

    monkeypatch.setattr(gateway.rest, "list_payment_link_ids",
                        lambda config: ("plink_SHARED",))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake")
    monkeypatch.setenv("RAZORPAY_MCP_TOKEN", "t")

    def fake_call(self, tool_name, arguments):
        calls["n"] += 1
        from sampark.integrations.provenance import McpCallReceipt
        return ({"payment_links": [{"id": "plink_SHARED"}]},
                McpCallReceipt(tool_name, "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"))

    monkeypatch.setattr(RazorpayMcpClient, "call_tool", fake_call)

    ok, _reason = gateway.assert_same_test_ledger()
    assert ok is True and calls["n"] == 1
    ok, _reason = gateway.assert_same_test_ledger()
    assert ok is True and calls["n"] == 1, "a passing check was not cached"
    ok, _reason = gateway.assert_same_test_ledger(use_cache=False)
    assert ok is True and calls["n"] == 2, "use_cache=False did not force a fresh check"

    gateway.reset_ledger_check_cache()

    # A FAILING check is never cached: three calls, three real attempts.
    from sampark.integrations.razorpay import RazorpayRequestError

    def unreadable(config):
        raise RazorpayRequestError("transient")

    monkeypatch.setattr(gateway.rest, "list_payment_link_ids", unreadable)
    for _ in range(3):
        ok, reason = gateway.assert_same_test_ledger()
        assert ok is False and "unreadable" in reason
    gateway.reset_ledger_check_cache()


def test_a_transient_failure_does_not_permanently_withhold_mcp(monkeypatch):
    """The recovery property, stated directly: after a failed check, a later
    successful one is honoured."""
    from sampark.integrations.provenance import McpCallReceipt
    from sampark.integrations.razorpay import RazorpayRequestError

    gateway.reset_ledger_check_cache()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_fake")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "fake")
    monkeypatch.setenv("RAZORPAY_MCP_TOKEN", "t")
    monkeypatch.setattr(
        RazorpayMcpClient, "call_tool",
        lambda self, tool_name, arguments: (
            {"payment_links": [{"id": "plink_SHARED"}]},
            McpCallReceipt(tool_name, "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"),
        ),
    )

    state = {"fail": True}

    def flaky(config):
        if state["fail"]:
            state["fail"] = False
            raise RazorpayRequestError("transient")
        return ("plink_SHARED",)

    monkeypatch.setattr(gateway.rest, "list_payment_link_ids", flaky)
    assert gateway.assert_same_test_ledger()[0] is False
    assert gateway.assert_same_test_ledger()[0] is True
    gateway.reset_ledger_check_cache()
