"""Razorpay MCP Server client — JSON-RPC 2.0 over streamable HTTP.

CLAUDE.md §10: MCP is an external integration capability, not where business
logic lives. This module is a TRANSPORT and nothing else. It creates and
reads Razorpay test-mode artifacts; it never decides anything, never touches
the allocator, the ledger, the policy chain or the audit chain, and holds no
SAMPARK domain type.

--- Why a client here, rather than an editor's MCP tool layer ---

`sampark.demo.razorpay_product` runs inside the FastAPI process, driven by a
button a judge presses. It cannot borrow an AI assistant's tool layer, so
SAMPARK speaks MCP itself. That is also the more defensible claim: the
integration is a property of the product, reproducible by anyone holding the
credentials, not of whoever happened to be at the terminal.

--- Dependencies ---

`urllib.request` from the standard library, deliberately. `httpx` is in
requirements.txt as a TEST-ONLY dependency (fastapi.testclient); importing it
at runtime here would silently promote it. Nothing else is needed: MCP over
streamable HTTP is POST + JSON, with an optional `text/event-stream` reply
framing that `_parse_body` handles in six lines.

--- Credentials ---

`RAZORPAY_MCP_TOKEN` is read from the environment and placed in an
Authorization header. It is never logged, never returned, never placed in an
exception message, and never reaches a payload, a response body or the
frontend (CLAUDE.md §8). Error messages carry the tool name and the server's
own text only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sampark.integrations.provenance import McpCallReceipt, Provenance

DEFAULT_MCP_URL = "https://mcp.razorpay.com/mcp"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "sampark-razorpay-adapter"
CLIENT_VERSION = "1.0.0"
DEFAULT_TIMEOUT_SECONDS = 45.0


class RazorpayMcpConfigError(RuntimeError):
    """The MCP server is not configured. Raised — never worked around with a
    fabricated response (CLAUDE.md §8: never fabricate an external call)."""


class RazorpayMcpError(RuntimeError):
    """The MCP server rejected or failed a call. Carries the tool name and
    the server's own message; never a header, a token or an authed URL."""


class RazorpayMcpUnavailable(RazorpayMcpError):
    """The MCP server could not be reached at all (DNS, TCP, TLS, timeout,
    HTTP error). Distinguished from `RazorpayMcpError` so the gateway falls
    back to the REST transport for THIS reason and only this reason — a tool
    that ran and legitimately refused must not be retried as REST."""


@dataclass(frozen=True)
class RazorpayMcpConfig:
    url: str
    token: str = field(repr=False)  # never printed, even in a traceback

    @classmethod
    def from_env(cls) -> "RazorpayMcpConfig":
        token = os.environ.get("RAZORPAY_MCP_TOKEN", "").strip()
        if not token:
            raise RazorpayMcpConfigError(
                "RAZORPAY_MCP_TOKEN is not set. The Razorpay MCP transport is "
                "unavailable; the REST test API is used instead and the product "
                "surface is labelled accordingly."
            )
        url = os.environ.get("RAZORPAY_MCP_URL", "").strip() or DEFAULT_MCP_URL
        return cls(url=url, token=token)

    @property
    def host(self) -> str:
        return urllib.parse.urlparse(self.url).hostname or "unknown"

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return "RazorpayMcpConfig(url=" + repr(self.url) + ", token=<redacted>)"


def mcp_configured() -> bool:
    """True when a token is present. Reports CONFIGURATION only — it makes no
    network call and is never evidence that the server answered."""
    return bool(os.environ.get("RAZORPAY_MCP_TOKEN", "").strip())


@dataclass(frozen=True)
class ServerIdentity:
    name: str
    version: str


class RazorpayMcpClient:
    """One initialized MCP session. Not thread-safe; construct one per use.

    Lifecycle: `initialize` -> `notifications/initialized` -> N x `tools/call`.
    A server-issued `Mcp-Session-Id` is carried when present (Razorpay's
    server currently issues none, but ignoring one would fail silently)."""

    def __init__(self, config: RazorpayMcpConfig, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._config = config
        self._timeout = timeout
        self._session_id: str | None = None
        self._identity: ServerIdentity | None = None
        self._next_id = 0

    # --- transport ------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Authorization": self._config.token,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _parse_body(content_type: str, raw: str) -> dict[str, Any] | None:
        """MCP streamable HTTP answers as JSON or as a one-shot SSE frame.
        The LAST `data:` line is the response to our single request."""
        if "text/event-stream" in content_type:
            message: dict[str, Any] | None = None
            for line in raw.splitlines():
                if line.startswith("data:"):
                    message = json.loads(line[5:].strip())
            return message
        return json.loads(raw) if raw.strip() else None

    def _post(self, body: dict[str, Any]) -> dict[str, Any] | None:
        request = urllib.request.Request(
            self._config.url,
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                return self._parse_body(
                    response.headers.get("Content-Type", ""),
                    response.read().decode("utf-8", "replace"),
                )
        except urllib.error.HTTPError as exc:
            # Status and the server's own body only. The request headers,
            # which carry the token, are never read back or reported.
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise RazorpayMcpUnavailable(
                "Razorpay MCP server returned HTTP " + str(exc.code) + ": " + detail
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RazorpayMcpUnavailable(
                "Razorpay MCP server at " + self._config.host + " is unreachable: " + type(exc).__name__
            ) from None
        except json.JSONDecodeError as exc:
            raise RazorpayMcpError("Razorpay MCP server returned a non-JSON body: " + str(exc)) from None

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            body["params"] = params
        message = self._post(body)
        if message is None:
            raise RazorpayMcpError(method + ": empty response from the Razorpay MCP server")
        if "error" in message:
            error = message["error"] or {}
            raise RazorpayMcpError(
                method + ": " + str(error.get("message", "unknown MCP error"))
                + " (code " + str(error.get("code", "?")) + ")"
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise RazorpayMcpError(method + ": response carried no result object")
        return result

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        self._post(body)

    # --- session --------------------------------------------------------

    def initialize(self) -> ServerIdentity:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        info = result.get("serverInfo") or {}
        self._identity = ServerIdentity(
            name=str(info.get("name", "unknown")), version=str(info.get("version", "unknown"))
        )
        self._notify("notifications/initialized", {})
        return self._identity

    @property
    def identity(self) -> ServerIdentity:
        if self._identity is None:
            return self.initialize()
        return self._identity

    def list_tools(self) -> tuple[str, ...]:
        """Tool NAMES only. Read-only; used by the health endpoint to report
        what the server actually offers rather than what this module hopes
        it does."""
        result = self._rpc("tools/list", {})
        return tuple(str(tool["name"]) for tool in result.get("tools", []) if "name" in tool)

    # --- the one call that mints provenance -----------------------------

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> tuple[Any, McpCallReceipt]:
        """Invoke one Razorpay MCP tool.

        Returns `(parsed_content, receipt)`. THIS IS THE ONLY PLACE IN THE
        REPOSITORY WHERE AN `McpCallReceipt` IS CONSTRUCTED, and it happens
        strictly after a JSON-RPC response carrying no `error` and no
        `isError` — so the receipt, and therefore any "transport: mcp" label
        the product surface shows, cannot exist unless this call really
        succeeded. Enforced by
        `tests/integrations/test_provenance_cannot_be_fabricated.py`.
        """
        identity = self.identity
        result = self._rpc("tools/call", {"name": tool_name, "arguments": arguments})

        if result.get("isError"):
            raise RazorpayMcpError(tool_name + ": " + _text_of(result)[:400])

        receipt = McpCallReceipt(
            tool_name=tool_name,
            endpoint_host=self._config.host,
            server_name=identity.name,
            server_version=identity.version,
        )
        return _content_of(result), receipt


def _text_of(result: dict[str, Any]) -> str:
    return "".join(
        str(block.get("text", ""))
        for block in result.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _content_of(result: dict[str, Any]) -> Any:
    """The tool's payload. Razorpay's MCP server returns a JSON document as a
    single text block; `structuredContent` is preferred where present."""
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)):
        return structured
    text = _text_of(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RazorpayMcpError("MCP tool result was not JSON: " + text[:200]) from None


def provenance_for(receipt: McpCallReceipt, reference: str | None = None) -> Provenance:
    """Convenience wrapper so callers do not restate `datetime.now(utc)`."""
    return Provenance.from_mcp(receipt, observed_at=datetime.now(timezone.utc), reference=reference)


__all__ = [
    "DEFAULT_MCP_URL",
    "PROTOCOL_VERSION",
    "RazorpayMcpClient",
    "RazorpayMcpConfig",
    "RazorpayMcpConfigError",
    "RazorpayMcpError",
    "RazorpayMcpUnavailable",
    "ServerIdentity",
    "mcp_configured",
    "provenance_for",
]
