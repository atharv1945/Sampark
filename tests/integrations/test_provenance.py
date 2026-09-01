"""Provenance, and why an MCP label cannot be fabricated.

The product surface says, on screen, whether a Razorpay artifact was created
*through the Razorpay MCP Server* or *through the Razorpay Test API*. CLAUDE.md
§8 forbids fabricating a successful external API call, so that label has to be
produced by the code path that actually ran — not chosen by a caller.

`sampark.integrations.provenance` enforces that structurally: a `Provenance`
carrying `Transport.MCP` can only be built from an `McpCallReceipt`, and an
`McpCallReceipt` is constructed in exactly one place in the repository. THIS
FILE IS THAT ENFORCEMENT — the AST scan below is the actual guarantee, and the
docstrings elsewhere are only its explanation.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib

import pytest

from sampark.integrations.provenance import (
    Environment,
    McpCallReceipt,
    Provenance,
    RestCallReceipt,
    Transport,
    WebhookReceipt,
)

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
AT = dt.datetime(2026, 9, 1, 10, 0, tzinfo=dt.timezone.utc)

# receipt class -> the ONE module allowed to construct it.
SOLE_CONSTRUCTION_SITES = {
    "McpCallReceipt": "sampark/integrations/razorpay_mcp.py",
    "RestCallReceipt": "sampark/integrations/razorpay_rest.py",
    "WebhookReceipt": "sampark/integrations/webhook.py",
}


def _source_files() -> list[pathlib.Path]:
    """Every shipped Python file. `tests/` is excluded deliberately: a test
    must be able to build a receipt to exercise the type — the invariant is
    about PRODUCTION code, where a fabricated label would reach a screen."""
    out: list[pathlib.Path] = []
    for directory in ("sampark", "ui", "sim", "agents", "scripts"):
        for path in (REPO / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            out.append(path)
    return out


def _construction_sites(class_name: str) -> list[str]:
    """Repo-relative paths of every `ClassName(...)` call in shipped code."""
    sites: list[str] = []
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == class_name:
                sites.append(path.relative_to(REPO).as_posix())
    return sites


@pytest.mark.parametrize("class_name,expected", sorted(SOLE_CONSTRUCTION_SITES.items()))
def test_each_receipt_is_constructed_in_exactly_one_module(class_name, expected):
    """This is THE guarantee. If a future change mints an `McpCallReceipt`
    anywhere but inside the MCP client's success branch, the product surface
    could show "via Razorpay MCP Server" for an operation MCP never performed.
    That is a fabricated external-service claim, and this test is what stops
    it reaching main."""
    sites = _construction_sites(class_name)
    assert sites == [expected], (
        class_name + " is constructed in " + repr(sites) + "; it may only be constructed in "
        + expected + " (see sampark/integrations/provenance.py)"
    )


def test_the_mcp_receipt_is_minted_only_after_a_successful_tools_call():
    """Not just the right FILE — the right place inside it.

    The construction must sit in `RazorpayMcpClient.call_tool`, textually
    after the `_rpc("tools/call", ...)` that produced the response and after
    the `isError` guard that rejects a tool-level failure."""
    source = (REPO / "sampark/integrations/razorpay_mcp.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    call_tool = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "call_tool":
            call_tool = node
    assert call_tool is not None, "RazorpayMcpClient.call_tool has gone missing"

    body = ast.get_source_segment(source, call_tool) or ""
    assert "McpCallReceipt(" in body, "the receipt is no longer minted inside call_tool"
    assert body.index('_rpc("tools/call"') < body.index("McpCallReceipt("), (
        "the receipt is minted BEFORE the tools/call is issued"
    )
    assert body.index('result.get("isError")') < body.index("McpCallReceipt("), (
        "the receipt is minted before the isError guard, so a failed tool call "
        "would still produce an MCP provenance label"
    )


def test_provenance_has_no_public_constructor_that_takes_a_transport_string():
    """A caller must present a receipt. `Provenance(...)` is a dataclass and
    can technically be built directly, so the real check is that no shipped
    module does: every construction outside provenance.py goes through
    `from_mcp` / `from_rest` / `from_webhook`."""
    sites = [s for s in _construction_sites("Provenance") if s != "sampark/integrations/provenance.py"]
    assert sites == [], "Provenance is constructed directly in " + repr(sites)


# --- behaviour --------------------------------------------------------------


def test_from_mcp_reports_mcp_and_test_mode():
    p = Provenance.from_mcp(
        McpCallReceipt(
            tool_name="create_payment_link", endpoint_host="mcp.razorpay.com",
            server_name="razorpay-mcp-server", server_version="1.0.0",
        ),
        observed_at=AT, reference="plink_ABC",
    )
    assert p.transport is Transport.MCP
    assert p.environment is Environment.TEST
    assert p.operation == "create_payment_link"
    assert p.as_payload() == {
        "provider": "razorpay", "environment": "test",
        "operation": "create_payment_link", "transport": "mcp", "reference": "plink_ABC",
    }


def test_from_rest_reports_rest_and_never_mcp():
    p = Provenance.from_rest(
        RestCallReceipt(operation="fetch_payment", endpoint_host="api.razorpay.com"),
        observed_at=AT, reference="pay_ABC",
    )
    assert p.transport is Transport.REST_API
    assert p.as_payload()["transport"] == "rest_api"


def test_from_webhook_reports_webhook_and_normalises_the_event_name():
    """`payment.failed` becomes `payment_failed`: the operation string lands in
    an audit payload, and `sampark.audit.canonical` allows dots — but the
    payload already uses dots as its own path separator in reason codes, so an
    operation name is kept dot-free to stay unambiguous."""
    p = Provenance.from_webhook(
        WebhookReceipt(event_name="payment.failed", razorpay_event_id="evt_1"),
        observed_at=AT, reference="pay_ABC",
    )
    assert p.transport is Transport.WEBHOOK
    assert p.operation == "payment_failed"


def test_every_payload_field_is_canonical_payload_safe():
    """The provenance fragment is copied verbatim into a
    `payment.risk_detected` payload, so it must satisfy
    `sampark.audit.canonical`'s controlled-ASCII rule or the event cannot be
    hashed at all."""
    from sampark.audit.canonical import _SAFE_PAYLOAD_STRING_RE

    for p in (
        Provenance.from_mcp(
            McpCallReceipt("create_payment_link", "mcp.razorpay.com", "razorpay-mcp-server", "1.0.0"),
            observed_at=AT, reference="plink_ABC",
        ),
        Provenance.from_rest(RestCallReceipt("fetch_payment", "api.razorpay.com"), observed_at=AT),
        Provenance.from_webhook(WebhookReceipt("payment.failed", "evt_1"), observed_at=AT),
    ):
        for key, value in p.as_payload().items():
            assert _SAFE_PAYLOAD_STRING_RE.match(key), key
            if value is not None:
                assert _SAFE_PAYLOAD_STRING_RE.match(value), (key, value)


def test_only_test_mode_exists_in_the_environment_vocabulary():
    """There is deliberately no LIVE member. A live-mode operation cannot be
    described by this module at all, which is a stronger guarantee than a
    runtime check (CLAUDE.md §8: test credentials only)."""
    assert [e.value for e in Environment] == ["test"]
