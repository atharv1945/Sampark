"""Integration provenance — WHICH transport actually performed an operation.

The product surface states, on screen, whether the demo's Razorpay artifact
was created *through the Razorpay MCP Server* or *through the Razorpay Test
API*. Spec-adjacent honesty rules (CLAUDE.md §8/§14) make that claim only
worth showing if it cannot be wrong: a label that says "MCP" while a REST
call actually ran is a fabricated external-service claim, which is exactly
what §8 forbids.

So provenance is not a string a caller may set. A `Provenance` is minted
only by presenting a RECEIPT, and each receipt type is constructed at
exactly ONE place in this repository — the code path that actually made the
call and saw its response:

    McpCallReceipt    sampark/integrations/razorpay_mcp.py, after a
                      successful JSON-RPC `tools/call` response.
    RestCallReceipt   sampark/integrations/razorpay_rest.py, after a
                      successful Razorpay REST response.
    WebhookReceipt    sampark/integrations/webhook.py, after the HMAC-SHA256
                      signature over the raw body verified.

`tests/integrations/test_provenance_cannot_be_fabricated.py` asserts those
call-site counts across the whole repository by AST, so a later change that
mints an MCP receipt from anywhere but the MCP client fails the suite. That
is the enforcement; this docstring is only the explanation.

Nothing here carries a credential. A receipt records the endpoint HOST and
the operation NAME, never a token, a key, or an Authorization header.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone


class Transport(str, enum.Enum):
    """How an operation reached Razorpay. Never inferred, never defaulted."""

    MCP = "mcp"
    REST_API = "rest_api"
    WEBHOOK = "webhook"


class Environment(str, enum.Enum):
    """Razorpay mode. Only TEST is ever produced by this repository:
    `sampark.integrations.razorpay.RazorpayConfig.from_env` refuses any key
    id that does not begin `rzp_test_`, and the MCP path is gated by
    `sampark.integrations.razorpay_mcp.assert_test_mode` (CLAUDE.md §8)."""

    TEST = "test"


@dataclass(frozen=True)
class McpCallReceipt:
    """Proof that a Razorpay MCP `tools/call` actually completed.

    CONSTRUCTED IN EXACTLY ONE PLACE: `razorpay_mcp.RazorpayMcpClient.call_tool`,
    on the success branch, after the JSON-RPC response has been parsed and
    found to carry no `error`. Do not construct it anywhere else."""

    tool_name: str
    endpoint_host: str
    server_name: str
    server_version: str


@dataclass(frozen=True)
class RestCallReceipt:
    """Proof that a Razorpay REST call actually completed.

    CONSTRUCTED IN EXACTLY ONE PLACE: `razorpay_rest` — see that module."""

    operation: str
    endpoint_host: str


@dataclass(frozen=True)
class WebhookReceipt:
    """Proof that a Razorpay webhook body passed HMAC-SHA256 verification.

    CONSTRUCTED IN EXACTLY ONE PLACE: `webhook.verify_and_parse`, after
    `hmac.compare_digest` returned True. An unverified body never produces
    one, so an unverified body can never acquire provenance."""

    event_name: str
    razorpay_event_id: str | None


@dataclass(frozen=True)
class Provenance:
    """One line of the "Integration" panel, and one payload fragment of the
    `payment.risk_detected` audit event.

    Every field is a controlled ASCII identifier so the whole record can be
    copied into an audit payload unchanged (`sampark.audit.canonical`'s
    `_SAFE_PAYLOAD_STRING_RE`)."""

    provider: str
    environment: Environment
    operation: str
    transport: Transport
    observed_at: datetime
    reference: str | None = None
    detail: str | None = None

    # --- the only three constructors -----------------------------------

    @classmethod
    def from_mcp(
        cls,
        receipt: McpCallReceipt,
        *,
        observed_at: datetime,
        reference: str | None = None,
    ) -> "Provenance":
        return cls(
            provider="razorpay",
            environment=Environment.TEST,
            operation=receipt.tool_name,
            transport=Transport.MCP,
            observed_at=observed_at,
            reference=reference,
            detail=receipt.server_name + ":" + receipt.server_version,
        )

    @classmethod
    def from_rest(
        cls,
        receipt: RestCallReceipt,
        *,
        observed_at: datetime,
        reference: str | None = None,
    ) -> "Provenance":
        return cls(
            provider="razorpay",
            environment=Environment.TEST,
            operation=receipt.operation,
            transport=Transport.REST_API,
            observed_at=observed_at,
            reference=reference,
            detail=receipt.endpoint_host,
        )

    @classmethod
    def from_webhook(
        cls,
        receipt: WebhookReceipt,
        *,
        observed_at: datetime,
        reference: str | None = None,
    ) -> "Provenance":
        return cls(
            provider="razorpay",
            environment=Environment.TEST,
            operation=receipt.event_name.replace(".", "_"),
            transport=Transport.WEBHOOK,
            observed_at=observed_at,
            reference=reference,
            detail=receipt.razorpay_event_id,
        )

    # --- projections ----------------------------------------------------

    def as_payload(self) -> dict[str, str | None]:
        """The audit-payload fragment. ASCII identifiers only — no host, no
        URL, no credential, nothing free-form."""
        return {
            "provider": self.provider,
            "environment": self.environment.value,
            "operation": self.operation,
            "transport": self.transport.value,
            "reference": self.reference,
        }

    def as_display(self) -> dict[str, str | None]:
        """The "Integration" panel the product page renders. Adds
        `observed_at` and `detail`, neither of which belongs in a payload."""
        return {
            **self.as_payload(),
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "detail": self.detail,
        }


__all__ = [
    "Environment",
    "McpCallReceipt",
    "Provenance",
    "RestCallReceipt",
    "Transport",
    "WebhookReceipt",
]
