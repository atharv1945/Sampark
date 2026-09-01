"""The Razorpay product-demo session — one run, one isolated schema.

Mirrors `ui.session.DemoSession`'s safety model exactly, and deliberately
reuses its primitives rather than re-deriving them:

  * every write goes to a throwaway `sampark_demo_<...>` schema created by
    `sampark.demo.isolation`, whose regex refuses any name that is not one
    it produced;
  * teardown drops that schema, and `drop_demo_schema` leaves the connection
    with an EMPTY `search_path`, so anything escaping teardown fails loudly
    instead of resolving against `public`;
  * `public.audit_events` is never written and is only ever READ, through
    `isolation.public_audit_fingerprint`.

DIFFERENT from `DemoSession` in one respect, on purpose: there is no
background thread. The synthetic demo replays five simulated windows over
~40 wall seconds and needs one; the product flow decides one real payment
and returns in well under a second, so it runs inline on the request thread
and needs no second connection, no cooperative stop, and no join. That
removes the entire class of failure the Phase 8 `reset()`-mid-run incident
came from.

A lock still guards the session, because a judge can click two buttons at
once and a psycopg connection is not safe for concurrent use.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import psycopg

from sampark.demo import isolation
from sampark.demo.provider import ProviderFailureMode
from sampark.demo.razorpay_product import IngestOutcome, RazorpayProductRun
from sampark.integrations import gateway
from sampark.integrations.normalize import RecoveryOpportunity, normalize_payment
from sampark.integrations.provenance import Provenance
from sim.persistence import PostgresConfig


class NoActiveSessionError(RuntimeError):
    """No product-demo session is open. Mapped to HTTP 409."""


class NoPaymentLinkError(RuntimeError):
    """No payment link has been created in this session. Mapped to 409."""


@dataclass(frozen=True)
class PaymentLinkState:
    """CONTROL STATE, and labelled as such wherever it renders.

    A payment link is not an audit fact: it exists before SAMPARK has any
    opinion, and nothing about it has been decided. It is shown in the
    product page's marked "Razorpay Test Mode" region alongside the
    provenance of the call that created it — never in the trace."""

    payment_link_id: str
    short_url: str | None
    reference_id: str
    amount_paise: int
    currency: str
    status: str | None
    provenance: Provenance
    fallback_reason: str | None
    # "headline" (the 1,000 INR subject of the demo) or "contrast" (a second,
    # clearly-labelled payment above the allocator's break-even, which exists
    # only so the grant/execute/settle path is demonstrable at all — see
    # `sampark.integrations.gateway.DEFAULT_CONTRAST_AMOUNT_INR`).
    role: str = "headline"

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "payment_link_id": self.payment_link_id,
            "short_url": self.short_url,
            "reference_id": self.reference_id,
            "amount_paise": self.amount_paise,
            "amount_inr": "{:,.2f}".format(self.amount_paise / 100),
            "currency": self.currency,
            "status": self.status,
            "provenance": self.provenance.as_display(),
            "fallback_reason": self.fallback_reason,
        }


@dataclass
class RazorpayProductSession:
    config: PostgresConfig

    _lock: threading.RLock = None  # type: ignore[assignment]
    session_id: str | None = None
    schema: str | None = None
    run: RazorpayProductRun | None = None
    link: PaymentLinkState | None = None          # the most recently created
    links: list[PaymentLinkState] = field(default_factory=list)
    _conn: psycopg.Connection | None = None
    # Razorpay's own event id (or the derived fallback) -> the payment id it
    # produced. This is the WEBHOOK-level duplicate guard. The payment-level
    # guard lives in `RazorpayProductRun.ingest`, and the event-level guard is
    # the audit chain's deterministic `event_id`. Three independent layers,
    # because a webhook can be redelivered at any of the three granularities.
    _seen_webhook_keys: dict[str, str] = field(default_factory=dict)
    _notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()

    # --- connections ----------------------------------------------------

    def _connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self.config.conninfo(), connect_timeout=5)
        conn.autocommit = True
        return conn

    def open_reader(self) -> psycopg.Connection:
        """A fresh read connection on the active schema. Caller closes it.

        The SSE stream and every read endpoint use one of these, so a reader
        never shares a connection with the flow."""
        schema = self.require_schema()
        conn = self._connect()
        isolation.set_search_path(conn, schema)
        return conn

    def require_schema(self) -> str:
        with self._lock:
            if self.schema is None:
                raise NoActiveSessionError(
                    "no Razorpay product session is open - POST "
                    "/api/integrations/razorpay/session first"
                )
            return self.schema

    def require_run(self) -> RazorpayProductRun:
        with self._lock:
            if self.run is None:
                raise NoActiveSessionError(
                    "no Razorpay product session is open - POST "
                    "/api/integrations/razorpay/session first"
                )
            return self.run

    # --- lifecycle ------------------------------------------------------

    def start(self) -> dict[str, Any]:
        with self._lock:
            self._teardown_locked()
            conn = self._connect()
            schema = isolation.create_demo_schema(conn)
            run = RazorpayProductRun(conn=conn, schema=schema)
            run.prepare(at=datetime.now(timezone.utc))

            self._conn = conn
            self.schema = schema
            self.run = run
            self.session_id = uuid.uuid4().hex[:12]
            self.links = []
            self._seen_webhook_keys = {}
            self._notes = []
            return self.state()

    def reset(self) -> dict[str, Any]:
        with self._lock:
            dropped = self.schema
            self._teardown_locked()
            return {"dropped_schema": dropped}

    def _teardown_locked(self) -> None:
        if self._conn is not None and self.schema is not None:
            try:
                isolation.drop_demo_schema(self._conn, self.schema)
            except Exception:
                # The startup sweep in ui.app's lifespan is the backstop.
                pass
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self.schema = None
        self.run = None
        self.link = None
        self.links = []
        self.session_id = None
        self._seen_webhook_keys = {}
        self._notes = []

    # --- Razorpay operations --------------------------------------------

    def create_payment_link(self, role: str = "headline", amount_inr: int | None = None) -> dict[str, Any]:
        """Create a demo payment link through the gateway.

        `role` is presentation only and never reaches Razorpay: "headline" is
        the 1,000 INR subject of the demo, "contrast" is the second,
        above-break-even payment. The AMOUNT is what actually differs, and it
        defaults from the gateway rather than from this module.

        The gateway decides MCP vs REST and hands back the provenance minted
        by whichever transport ran; this method never sets a transport
        label."""
        with self._lock:
            self.require_run()
            if role not in ("headline", "contrast"):
                raise ValueError("role must be 'headline' or 'contrast', got " + repr(role))
            if amount_inr is not None:
                amount_paise = max(int(amount_inr), 1) * 100
            elif role == "contrast":
                amount_paise = gateway.contrast_amount_paise()
            else:
                amount_paise = gateway.demo_amount_paise()
            reference_id = "sampark-" + role + "-" + uuid.uuid4().hex[:14]
            result = gateway.create_demo_payment_link(reference_id, amount_paise=amount_paise)
            payload = result.payload if isinstance(result.payload, dict) else {}
            link_id = str(payload.get("id") or "")
            if not link_id:
                raise gateway.GatewayUnavailable(
                    "Razorpay accepted the request but returned no payment link id"
                )
            link = PaymentLinkState(
                payment_link_id=link_id,
                short_url=str(payload.get("short_url")) if payload.get("short_url") else None,
                reference_id=str(payload.get("reference_id") or reference_id),
                amount_paise=int(payload.get("amount") or gateway.demo_amount_paise()),
                currency=str(payload.get("currency") or gateway.DEMO_CURRENCY),
                status=str(payload.get("status")) if payload.get("status") else None,
                provenance=result.provenance,
                fallback_reason=result.fallback_reason,
                role=role,
            )
            self.links.append(link)
            self.link = link
            self._note(
                role + " payment link " + link_id + " created via " + result.provenance.transport.value
            )
            return link.as_dict()

    def refresh_payment_links(self) -> dict[str, Any]:
        """Re-read every link in this session so the page can show live
        status and attempt counts. Read-only, and every read reports the
        transport that performed it."""
        with self._lock:
            self._require_links()
            out = []
            for link in self.links:
                result = gateway.fetch_payment_link(link.payment_link_id)
                payload = result.payload if isinstance(result.payload, dict) else {}
                attempts = [p for p in payload.get("payments") or [] if isinstance(p, dict)]
                out.append({
                    **link.as_dict(),
                    "status": str(payload.get("status") or "") or link.status,
                    "amount_paid": payload.get("amount_paid"),
                    "attempts": [
                        {
                            "payment_id": str(a.get("payment_id") or a.get("id") or ""),
                            "status": str(a.get("status") or ""),
                            "method": str(a.get("method") or ""),
                            "created_at": a.get("created_at"),
                        }
                        for a in attempts
                    ],
                    "read_provenance": result.provenance.as_display(),
                    "read_fallback_reason": result.fallback_reason,
                })
            return {"links": out}

    def poll_and_ingest(self) -> dict[str, Any]:
        """Look for failed attempts across EVERY link in this session and run
        each one through SAMPARK.

        Returns `ingested: false` with a reason when nothing has failed yet.
        That is the honest answer before anyone has paid — no failure is ever
        invented to keep the demo moving (CLAUDE.md §8)."""
        with self._lock:
            links = self._require_links()
            results: list[dict[str, Any]] = []
            skipped: list[dict[str, Any]] = []
            for link in links:
                lookup = gateway.find_failed_payment(link.payment_link_id, link.reference_id)
                if lookup.payment is None or lookup.provenance is None:
                    skipped.append({
                        "role": link.role,
                        "payment_link_id": link.payment_link_id,
                        "link_status": lookup.link_status,
                        "attempts_seen": lookup.attempts_seen,
                        "reason": "no failed payment attempt observed on this link yet",
                    })
                    continue
                outcome = self._ingest_payment(
                    lookup.payment, lookup.provenance, payment_link_id=link.payment_link_id
                )
                results.append({
                    "role": link.role,
                    "matcher": lookup.matcher,
                    "fallback_reason": lookup.fallback_reason,
                    **outcome,
                })
            if not results:
                return {
                    "ingested": False,
                    "results": [],
                    "skipped": skipped,
                    "reason": (
                        "no failed payment attempt has been observed on any link in this "
                        "session yet. Open a link and pay with a Razorpay test card that fails."
                    ),
                }
            return {"ingested": True, "results": results, "skipped": skipped}

    def ingest_webhook_payment(
        self, payment: dict[str, Any], provenance: Provenance, idempotency_key: str
    ) -> dict[str, Any]:
        """Ingest a payment that arrived on the verified webhook path."""
        with self._lock:
            self.require_run()
            seen = self._seen_webhook_keys.get(idempotency_key)
            if seen is not None:
                run = self.require_run()
                previous = run.outcome_for(seen)
                # The overrides come AFTER the spread, deliberately: the stored
                # outcome carries `duplicate=False` (it was the FIRST delivery),
                # and spreading it last would silently overwrite the very flag
                # this branch exists to set. `tests/ui/test_razorpay_api.py::
                # test_a_duplicate_webhook_delivery_creates_no_second_recovery_action`
                # caught exactly that.
                return {
                    **(_outcome_dict(previous) if previous is not None else {}),
                    "ingested": True,
                    "duplicate": True,
                    "reason": "webhook event " + idempotency_key + " was already processed",
                }
            result = self._ingest_payment(payment, provenance, payment_link_id=None)
            self._seen_webhook_keys[idempotency_key] = str(payment.get("id"))
            return {"ingested": True, **result}

    def _ingest_payment(
        self, payment: dict[str, Any], provenance: Provenance, payment_link_id: str | None
    ) -> dict[str, Any]:
        run = self.require_run()
        opportunity: RecoveryOpportunity = normalize_payment(
            payment, provenance, payment_link_id=payment_link_id
        )
        outcome = run.ingest(opportunity)
        self._note(
            "payment " + opportunity.payment_id + " -> " + outcome.outcome
            + (" (" + outcome.reason_code + ")" if outcome.reason_code else "")
        )
        return {"opportunity": opportunity.as_public_dict(), **_outcome_dict(outcome)}

    def arm_provider_failure(self, mode: str | None) -> str:
        with self._lock:
            run = self.require_run()
            try:
                parsed = ProviderFailureMode(mode) if mode else ProviderFailureMode.HARD_DOWN
            except ValueError:
                raise ValueError("unknown provider failure mode: " + repr(mode))
            run.arm_provider_failure(parsed)
            self._note("provider armed (" + parsed.value + ") for the next send")
            return parsed.value

    # --- state ----------------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self._lock:
            run = self.run
            return {
                "session_id": self.session_id,
                "demo_schema": self.schema,
                "active": run is not None,
                "agent_id": None if run is None else "payment_retry_agent",
                "model_degraded": None if run is None else run.degraded,
                "scorer": None if run is None else run.scorer.inner_name,
                "ingested_payment_ids": () if run is None else run.ingested_payment_ids(),
                "payment_link": None if self.link is None else self.link.as_dict(),
                "payment_links": [link.as_dict() for link in self.links],
                "transport": gateway.transport_status(),
                "notes": tuple(self._notes[-12:]),
            }

    def _require_links(self) -> list[PaymentLinkState]:
        self.require_run()
        if not self.links:
            raise NoPaymentLinkError(
                "no payment link in this session - POST "
                "/api/integrations/razorpay/payment-link first"
            )
        return list(self.links)

    def _note(self, text: str) -> None:
        self._notes.append(datetime.now(timezone.utc).strftime("%H:%M:%S") + "  " + text)


def _outcome_dict(outcome: IngestOutcome) -> dict[str, Any]:
    delivery = outcome.delivery
    return {
        "payment_id": outcome.payment_id,
        "request_id": outcome.request_id,
        "risk_id": outcome.risk_id,
        "customer_id": outcome.customer_id,
        "duplicate": outcome.duplicate,
        "outcome": outcome.outcome,
        "reason_code": outcome.reason_code,
        "windows_evaluated": list(outcome.windows_evaluated),
        "grant_id": outcome.grant_id,
        "delivery": None if delivery is None else {
            "delivered": delivery.delivered,
            "attempts": delivery.attempts,
            "deduplicated": delivery.deduplicated,
            "channel": delivery.channel,
            "rolled_back": delivery.rolled_back,
        },
    }


__all__ = [
    "NoActiveSessionError",
    "NoPaymentLinkError",
    "PaymentLinkState",
    "RazorpayProductSession",
]
