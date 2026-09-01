"""The Razorpay gateway — one façade, two transports, honest labelling.

Every Razorpay operation the product surface performs goes through here, and
here is the only place that CHOOSES between the Razorpay MCP Server and the
Razorpay REST test API. The rule is fixed and stated once:

    MCP is preferred. REST is used when the MCP transport is not configured
    or could not be reached. The transport that ACTUALLY RAN is carried back
    in a `Provenance` minted by that transport's own module, so the label the
    screen shows is produced by the code path that executed and by nothing
    else (`sampark.integrations.provenance`).

A fallback is never silent: `GatewayResult.fallback_reason` carries why MCP
was not used, the API returns it, and the product page renders it.

--- Test mode, structurally ---

REST is test-mode by construction: `RazorpayConfig.from_env` refuses any key
id that is not `rzp_test_*`. The MCP token carries no such marker, so
`assert_same_test_ledger` closes that gap with a READ-ONLY check — it lists
payment links through both transports and requires that they see the SAME
ledger. If they disagree, the MCP transport is refused for WRITE operations
and the product falls back to REST, labelled. That check is what lets this
repository claim "Razorpay Test Mode" about the MCP path without assuming it.

--- What this module is not ---

It holds no SAMPARK domain type, no decision, no policy, no budget
arithmetic. It returns Razorpay dicts. `sampark.integrations.normalize` turns
one into a domain object; `sampark.demo.razorpay_product` decides what
happens next. CLAUDE.md §10.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

from sampark.integrations import razorpay_rest as rest
from sampark.integrations.provenance import Provenance, Transport
from sampark.integrations.razorpay import RazorpayConfig, RazorpayConfigError, RazorpayRequestError
from sampark.integrations.razorpay_mcp import (
    RazorpayMcpClient,
    RazorpayMcpConfig,
    RazorpayMcpConfigError,
    RazorpayMcpError,
    RazorpayMcpUnavailable,
    mcp_configured,
    provenance_for,
)

# The product demo's amount. ONE definition; the API, the page and the tests
# all read it from here rather than repeating a literal.
DEFAULT_DEMO_AMOUNT_INR = 1000

# The CONTRAST amount. Not a second headline — the demo's subject is the
# 1,000 INR payment. This exists because of a measured property of the FROZEN
# Phase 4 constants, not a preference:
#
#   expected_net = p_hat * amount - channel_cost - incentive - fatigue_cost
#
# `sampark/policy/soft/fatigue.py` prices one contact's forward opportunity
# cost at 54,120 paise for a customer with no other open items (30-day
# horizon, lambda 0.13569/day, mean at-risk amount 387,607 paise). With
# p_hat = 0.2737 for ("failed_payment", "issuer_downtime") and an sms channel
# cost of 20 paise, break-even is 197,835 paise -- about 1,978 INR.
#
# So a 1,000 INR failed payment is BELOW the line, and the allocator denies it
# with `allocation.negative_expected_net`. That is the product argument, not a
# problem to engineer around: SAMPARK declines to spend a customer's single
# contact slot on a recovery worth less than the future recoveries it would
# push further down the decay curve. Nothing here is tuned to change it.
#
# A second, clearly-labelled payment ABOVE the line is what makes the contrast
# visible and lets the grant/execute/settle path be demonstrated at all.
DEFAULT_CONTRAST_AMOUNT_INR = 4000

DEMO_CURRENCY = "INR"
DEMO_DESCRIPTION = "SAMPARK Razorpay Test Mode demo - revenue at risk"

# The MCP tool names this adapter uses. Verified present on the live server's
# `tools/list` before any of them was called; see docs/RAZORPAY_INTEGRATION.md
# for the full recorded capability list.
TOOL_CREATE_PAYMENT_LINK = "create_payment_link"
TOOL_FETCH_PAYMENT_LINK = "fetch_payment_link"
TOOL_FETCH_PAYMENT = "fetch_payment"
TOOL_FETCH_ALL_PAYMENTS = "fetch_all_payments"
TOOL_FETCH_ALL_PAYMENT_LINKS = "fetch_all_payment_links"

# Copied onto the payment link so a payment created from it can be matched
# back without guessing. Razorpay propagates a link's notes onto the payment.
NOTE_KEY = "sampark_reference"


class GatewayUnavailable(RuntimeError):
    """Neither transport could perform the operation. Raised — the product
    reports it and stops. Nothing is fabricated (CLAUDE.md §8)."""


def _rupees_from_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return max(int(raw), 1) if raw else default
    except ValueError:
        return default


def demo_amount_paise() -> int:
    """The demo amount in paise. Configurable via `RAZORPAY_DEMO_AMOUNT_INR`
    (whole rupees); defaults to 1000 INR. Razorpay's own minimum is 100
    paise, which `max(..., 1) * 100` satisfies without a second rule."""
    return _rupees_from_env("RAZORPAY_DEMO_AMOUNT_INR", DEFAULT_DEMO_AMOUNT_INR) * 100


def contrast_amount_paise() -> int:
    """The contrast amount in paise — see `DEFAULT_CONTRAST_AMOUNT_INR`.
    Configurable via `RAZORPAY_CONTRAST_AMOUNT_INR`."""
    return _rupees_from_env("RAZORPAY_CONTRAST_AMOUNT_INR", DEFAULT_CONTRAST_AMOUNT_INR) * 100


@dataclass(frozen=True)
class GatewayResult:
    """One Razorpay operation's outcome, with the transport that ran."""

    payload: Any
    provenance: Provenance
    fallback_reason: str | None = None

    @property
    def transport(self) -> Transport:
        return self.provenance.transport


# --- transport availability -------------------------------------------------


def _mcp_client() -> RazorpayMcpClient:
    return RazorpayMcpClient(RazorpayMcpConfig.from_env())


def _rest_config() -> RazorpayConfig:
    return RazorpayConfig.from_env()


def transport_status() -> dict[str, Any]:
    """What is CONFIGURED. Makes no network call, so it never implies that a
    server answered — `mcp_reachable` is deliberately absent here and is only
    ever reported by `probe_mcp`, which really calls."""
    return {
        "mcp_configured": mcp_configured(),
        "rest_configured": rest.rest_configured(),
        "preferred_transport": Transport.MCP.value if mcp_configured() else Transport.REST_API.value,
        "environment": "test",
        "amount_paise": demo_amount_paise(),
        "contrast_amount_paise": contrast_amount_paise(),
        "currency": DEMO_CURRENCY,
    }


def probe_mcp() -> dict[str, Any]:
    """Read-only liveness + capability probe: `initialize` then `tools/list`.

    Reports what the server ACTUALLY offers. Never fabricates a tool list,
    and returns `reachable: False` with the reason when it cannot connect."""
    try:
        client = _mcp_client()
    except RazorpayMcpConfigError as exc:
        return {"reachable": False, "reason": str(exc), "tools": [], "server": None}
    try:
        identity = client.initialize()
        tools = client.list_tools()
    except RazorpayMcpError as exc:
        return {"reachable": False, "reason": str(exc), "tools": [], "server": None}
    return {
        "reachable": True,
        "reason": None,
        "server": {"name": identity.name, "version": identity.version},
        "tools": sorted(tools),
        "tools_used_by_sampark": sorted(
            {
                TOOL_CREATE_PAYMENT_LINK,
                TOOL_FETCH_PAYMENT_LINK,
                TOOL_FETCH_PAYMENT,
                TOOL_FETCH_ALL_PAYMENTS,
            }
            & set(tools)
        ),
    }


# Once MCP and the rzp_test_ REST key are proven to be on one ledger, that fact
# cannot change while this process holds the same two credentials. Caching the
# POSITIVE result costs two API round trips instead of two per write, and — the
# reason this exists — stops a TRANSIENT REST error from silently downgrading a
# subsequent write to the REST transport.
#
# Observed in a live run: a first payment-link creation fell back to REST with
# "REST ledger unreadable: RazorpayRequestError" while the very next one went
# via MCP. The fallback was correct and was labelled, but it was caused by a
# blip rather than by anything about the credentials.
#
# A NEGATIVE is deliberately NOT cached. A transient failure must not withhold
# MCP for the life of the process, and a genuinely mismatched ledger will keep
# failing the check anyway — so re-running it costs nothing and can only ever
# recover.
_LEDGER_CHECK_PASSED: tuple[bool, str] | None = None


def reset_ledger_check_cache() -> None:
    """Drop the cached positive result. For tests, and for a caller that has
    changed credentials mid-process."""
    global _LEDGER_CHECK_PASSED
    _LEDGER_CHECK_PASSED = None


def assert_same_test_ledger(use_cache: bool = True) -> tuple[bool, str]:
    """READ-ONLY proof that the MCP token and the `rzp_test_` REST key are on
    the same merchant account and mode.

    Both transports list payment links; a non-empty intersection of ids means
    the two credentials see one ledger, and the REST side of that ledger is
    test-mode by construction. Returns `(ok, explanation)` and never raises —
    the caller decides what to do with a False.

    An EMPTY REST ledger is inconclusive, not a failure: a brand-new test
    account has no links to intersect. That case returns False with a reason
    saying so, and the gateway then prefers REST for writes, which is the
    conservative direction.

    A passing result is cached process-wide (see `_LEDGER_CHECK_PASSED`); pass
    `use_cache=False` to force a fresh check."""
    global _LEDGER_CHECK_PASSED
    if use_cache and _LEDGER_CHECK_PASSED is not None:
        return _LEDGER_CHECK_PASSED
    try:
        rest_ids = set(rest.list_payment_link_ids(_rest_config()))
    except (RazorpayConfigError, RazorpayRequestError) as exc:
        return False, "REST ledger unreadable: " + type(exc).__name__
    if not rest_ids:
        return False, (
            "the REST test ledger has no payment links yet, so there is nothing to "
            "cross-check the MCP credential against"
        )
    try:
        client = _mcp_client()
        content, _receipt = client.call_tool(TOOL_FETCH_ALL_PAYMENT_LINKS, {})
    except (RazorpayMcpConfigError, RazorpayMcpError) as exc:
        return False, "MCP ledger unreadable: " + type(exc).__name__
    mcp_ids = {
        str(item["id"])
        for item in _items(content, "payment_links")
        if isinstance(item, dict) and "id" in item
    }
    overlap = rest_ids & mcp_ids
    if overlap:
        result = (
            True,
            "MCP and the rzp_test_ REST key see the same payment-link ledger ("
            + str(len(overlap)) + " shared id(s)), so the MCP credential is on the "
            "same account in test mode",
        )
        _LEDGER_CHECK_PASSED = result
        return result
    return False, (
        "MCP and the rzp_test_ REST key see DIFFERENT payment-link ledgers; the MCP "
        "credential is not provably test-mode on this account"
    )


# --- operations -------------------------------------------------------------


def create_demo_payment_link(
    reference_id: str | None = None, amount_paise: int | None = None
) -> GatewayResult:
    """Create ONE test-mode payment link.

    `amount_paise` defaults to `demo_amount_paise()` (1,000 INR). The caller
    passes `contrast_amount_paise()` for the second, labelled comparison link.

    `notify_sms` / `notify_email` are both False. SAMPARK's channel adapters
    are mocked on purpose because synthetic consent cannot lawfully reach a
    real number (CLAUDE.md §8); a payment link that texts someone would walk
    straight through that rule."""
    reference_id = reference_id or ("sampark-demo-" + uuid.uuid4().hex[:16])
    amount = amount_paise if amount_paise is not None else demo_amount_paise()

    arguments = {
        "amount": amount,
        "currency": DEMO_CURRENCY,
        "description": DEMO_DESCRIPTION,
        "reference_id": reference_id,
        "notify_sms": False,
        "notify_email": False,
        "reminder_enable": False,
        "notes": {NOTE_KEY: reference_id},
    }

    fallback_reason = _mcp_write_blocked_reason()
    if fallback_reason is None:
        try:
            content, receipt = _mcp_client().call_tool(TOOL_CREATE_PAYMENT_LINK, arguments)
            return GatewayResult(payload=content, provenance=provenance_for(receipt, _id_of(content)))
        except RazorpayMcpUnavailable as exc:
            fallback_reason = "MCP unreachable: " + str(exc)[:200]
        except RazorpayMcpError as exc:
            fallback_reason = "MCP refused create_payment_link: " + str(exc)[:200]

    try:
        payload, provenance = rest.create_payment_link(
            _rest_config(),
            amount_paise=amount,
            description=DEMO_DESCRIPTION,
            reference_id=reference_id,
        )
    except (RazorpayConfigError, RazorpayRequestError) as exc:
        raise GatewayUnavailable(
            "no Razorpay transport could create the payment link. "
            + (fallback_reason + " | " if fallback_reason else "")
            + "REST: " + str(exc)[:200]
        ) from None
    return GatewayResult(payload=payload, provenance=provenance, fallback_reason=fallback_reason)


def fetch_payment_link(payment_link_id: str) -> GatewayResult:
    return _read(TOOL_FETCH_PAYMENT_LINK, {"payment_link_id": payment_link_id},
                 lambda cfg: rest.fetch_payment_link(cfg, payment_link_id), payment_link_id)


def fetch_payment(payment_id: str) -> GatewayResult:
    return _read(TOOL_FETCH_PAYMENT, {"payment_id": payment_id},
                 lambda cfg: rest.fetch_payment(cfg, payment_id), payment_id)


def fetch_recent_payments(count: int = 20) -> GatewayResult:
    return _read(TOOL_FETCH_ALL_PAYMENTS, {"count": count},
                 lambda cfg: rest.fetch_all_payments(cfg, count), None)


def _read(tool: str, arguments: dict[str, Any], rest_call, reference: str | None) -> GatewayResult:
    """Read operations prefer MCP whenever it is configured — no ledger
    cross-check is required, because a READ cannot create anything in the
    wrong mode. Writes are the guarded direction."""
    fallback_reason: str | None = None
    if mcp_configured():
        try:
            content, receipt = _mcp_client().call_tool(tool, arguments)
            return GatewayResult(payload=content, provenance=provenance_for(receipt, reference))
        except RazorpayMcpUnavailable as exc:
            fallback_reason = "MCP unreachable: " + str(exc)[:200]
        except RazorpayMcpError as exc:
            fallback_reason = "MCP refused " + tool + ": " + str(exc)[:200]
    else:
        fallback_reason = "RAZORPAY_MCP_TOKEN is not configured"

    try:
        payload, provenance = rest_call(_rest_config())
    except (RazorpayConfigError, RazorpayRequestError) as exc:
        raise GatewayUnavailable(
            "no Razorpay transport could perform " + tool + ". "
            + (fallback_reason + " | " if fallback_reason else "")
            + "REST: " + str(exc)[:200]
        ) from None
    return GatewayResult(payload=payload, provenance=provenance, fallback_reason=fallback_reason)


def _mcp_write_blocked_reason() -> str | None:
    """None when MCP may be used for a WRITE; otherwise why it may not."""
    if not mcp_configured():
        return "RAZORPAY_MCP_TOKEN is not configured"
    if os.environ.get("RAZORPAY_MCP_SKIP_LEDGER_CHECK", "").strip() == "1":
        # Escape hatch for a fresh test account with no links to cross-check.
        # Named so that using it is a deliberate, visible act.
        return None
    ok, reason = assert_same_test_ledger()
    return None if ok else "MCP write withheld - " + reason


# --- finding the failed payment behind a link -------------------------------


@dataclass(frozen=True)
class FailedPaymentLookup:
    payment: dict[str, Any] | None
    provenance: Provenance | None
    matcher: str | None  # "payment_link.payments" | "notes." + NOTE_KEY
    fallback_reason: str | None
    link_status: str | None
    attempts_seen: int


def find_failed_payment(payment_link_id: str, reference_id: str | None = None) -> FailedPaymentLookup:
    """Locate the failed payment attempt on a payment link, if one exists.

    Two matchers, tried in order, both exact — never "the most recent failed
    payment on the account", which would attribute a stranger's attempt to
    this link:

      1. the link's own `payments[]` array (Razorpay lists every attempt on
         the link, with its status);
      2. `notes.sampark_reference` on a recent payment, which Razorpay copies
         from the link's notes.

    Returns `payment=None` when neither matches. That is the normal answer
    before anyone has paid, and it is reported as "no failed attempt observed
    yet" — never as a fabricated failure."""
    link = fetch_payment_link(payment_link_id)
    link_payload = link.payload if isinstance(link.payload, dict) else {}
    attempts = [p for p in link_payload.get("payments") or [] if isinstance(p, dict)]

    for attempt in sorted(attempts, key=lambda p: p.get("created_at") or 0, reverse=True):
        if str(attempt.get("status", "")).lower() != "failed":
            continue
        payment_id = str(attempt.get("payment_id") or attempt.get("id") or "")
        if not payment_id.startswith("pay_"):
            continue
        full = fetch_payment(payment_id)
        if isinstance(full.payload, dict):
            return FailedPaymentLookup(
                payment=full.payload, provenance=full.provenance, matcher="payment_link.payments",
                fallback_reason=full.fallback_reason or link.fallback_reason,
                link_status=str(link_payload.get("status") or "") or None,
                attempts_seen=len(attempts),
            )

    if reference_id:
        recent = fetch_recent_payments(count=20)
        items = _items(recent.payload, "items")
        for payment in sorted(items, key=lambda p: p.get("created_at") or 0, reverse=True):
            if not isinstance(payment, dict):
                continue
            if str(payment.get("status", "")).lower() != "failed":
                continue
            notes = payment.get("notes")
            if isinstance(notes, dict) and str(notes.get(NOTE_KEY, "")) == reference_id:
                return FailedPaymentLookup(
                    payment=payment, provenance=recent.provenance, matcher="notes." + NOTE_KEY,
                    fallback_reason=recent.fallback_reason or link.fallback_reason,
                    link_status=str(link_payload.get("status") or "") or None,
                    attempts_seen=len(attempts),
                )

    return FailedPaymentLookup(
        payment=None, provenance=None, matcher=None, fallback_reason=link.fallback_reason,
        link_status=str(link_payload.get("status") or "") or None,
        attempts_seen=len(attempts),
    )


def _items(payload: Any, key: str) -> list[Any]:
    """Razorpay collections arrive as `{"<key>": [...]}` or `{"items": [...]}`
    depending on endpoint and transport. Both shapes are read; neither is
    assumed."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for candidate in (key, "items", "payment_links", "payments"):
        value = payload.get(candidate)
        if isinstance(value, list):
            return value
    return []


def _id_of(payload: Any) -> str | None:
    return str(payload["id"]) if isinstance(payload, dict) and payload.get("id") else None


__all__ = [
    "DEFAULT_CONTRAST_AMOUNT_INR",
    "DEFAULT_DEMO_AMOUNT_INR",
    "DEMO_CURRENCY",
    "DEMO_DESCRIPTION",
    "NOTE_KEY",
    "TOOL_CREATE_PAYMENT_LINK",
    "TOOL_FETCH_ALL_PAYMENTS",
    "TOOL_FETCH_ALL_PAYMENT_LINKS",
    "TOOL_FETCH_PAYMENT",
    "TOOL_FETCH_PAYMENT_LINK",
    "FailedPaymentLookup",
    "GatewayResult",
    "GatewayUnavailable",
    "assert_same_test_ledger",
    "contrast_amount_paise",
    "create_demo_payment_link",
    "demo_amount_paise",
    "fetch_payment",
    "fetch_payment_link",
    "fetch_recent_payments",
    "find_failed_payment",
    "probe_mcp",
    "reset_ledger_check_cache",
    "transport_status",
]
