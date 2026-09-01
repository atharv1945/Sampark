"""The Razorpay product-integration HTTP surface.

Deliberately the smallest coherent API. Every route is a thin adapter:
validate input, call `ui.razorpay_session` or the existing Phase 5 audit read
path, map exceptions to status codes. There is NO business logic here — no
normalisation, no decision, no policy, no explanation, no verification, no
export. Those live in `sampark/integrations/**`, `sampark/demo/**` and
`sampark/audit/**`, which is where they can be tested without HTTP.

    GET  /api/integrations/razorpay/health            configuration + live probe
    POST /api/integrations/razorpay/session           open an isolated session
    POST /api/integrations/razorpay/reset             drop it
    GET  /api/integrations/razorpay/state             session control state
    POST /api/integrations/razorpay/payment-link      create a demo link
    GET  /api/integrations/razorpay/payment-link      re-read them (live status)
    POST /api/integrations/razorpay/ingest            poll every link for a
                                                      failed payment and
                                                      decide each one
    POST /api/integrations/razorpay/webhook           Razorpay webhook receiver
    POST /api/integrations/razorpay/provider-failure  arm the mock channel
    GET  /api/integrations/razorpay/stream            SSE over audit_events
    GET  /api/integrations/razorpay/events            the same rows, paged
    GET  /api/integrations/razorpay/verify            chain verification
    GET  /api/integrations/razorpay/explain/request/{id}

The last four REUSE `ui.sse` and `sampark.audit.*` unchanged. In particular
the SSE stream calls `ui.sse.event_stream`, whose single SQL statement names
only `audit_events` — so the product page is bound by the identical
trace-integrity rule as the system page, enforced by the identical test.

Error mapping, once:
    409  no session / no payment link
    400  malformed input
    401  webhook signature missing, invalid, or unverifiable
    422  the log cannot support the answer / malformed webhook body
    404  unknown request_id
    502  Razorpay could not be reached through any transport
    503  Postgres unreachable
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from sampark.audit import store as audit_store
from sampark.audit.chain import verify_chain
from sampark.audit.explain import IncompleteLogError, explain_request, format_explanation
from sampark.demo.isolation import public_audit_fingerprint
from sampark.integrations import gateway, webhook
from sampark.integrations.normalize import (
    NotAPaymentError,
    UnsupportedPaymentStateError,
    is_recoverable,
)
from sampark.integrations.razorpay import RazorpayConfigError, RazorpayRequestError
from ui import sse
from ui.models import PaymentLinkRequest, ProviderFailureRequest
from ui.razorpay_session import NoActiveSessionError, NoPaymentLinkError

router = APIRouter(prefix="/api/integrations/razorpay")


def _session(request: Request):
    return request.app.state.razorpay_session


def _require_session(request: Request):
    session = _session(request)
    try:
        session.require_run()
    except NoActiveSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return session


# --- health -----------------------------------------------------------------


@router.get("/health")
def health(request: Request, probe: bool = False) -> dict:
    """What is configured, and — only when `probe=1` — what actually answered.

    The two are kept apart on purpose. `transport_status()` makes no network
    call and therefore never implies the server is up; `probe_mcp()` really
    calls `initialize` + `tools/list` and reports the tool list the server
    itself returned. Nothing here reports a capability that was not observed.
    """
    session = _session(request)
    body: dict = {
        "transport": gateway.transport_status(),
        "webhook_configured": webhook.webhook_configured(),
        "session_active": session.run is not None,
        "demo_schema": session.schema,
    }
    try:
        conn = session._connect()
    except Exception as exc:  # pragma: no cover - environment failure
        raise HTTPException(status_code=503, detail="postgres unreachable: " + str(exc))
    try:
        count, head = public_audit_fingerprint(conn)
    finally:
        conn.close()
    body["protected_public_audit_event_count"] = count
    body["protected_public_audit_max_event_id"] = head

    if probe:
        body["mcp_probe"] = gateway.probe_mcp()
        ok, reason = gateway.assert_same_test_ledger()
        body["mcp_test_mode_check"] = {"same_test_ledger": ok, "detail": reason}
    return body


# --- session lifecycle ------------------------------------------------------


@router.post("/session")
def start_session(request: Request) -> dict:
    try:
        return _session(request).start()
    except Exception as exc:  # pragma: no cover - environment failure
        raise HTTPException(status_code=503, detail="could not open a session: " + str(exc))


@router.post("/reset")
def reset_session(request: Request) -> dict:
    return _session(request).reset()


@router.get("/state")
def state(request: Request) -> dict:
    return _session(request).state()


# --- Razorpay operations ----------------------------------------------------


@router.post("/payment-link")
def create_payment_link(request: Request, body: PaymentLinkRequest | None = None) -> dict:
    session = _require_session(request)
    body = body or PaymentLinkRequest()
    try:
        return session.create_payment_link(role=body.role, amount_inr=body.amount_inr)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except gateway.GatewayUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except (RazorpayConfigError, RazorpayRequestError) as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/payment-link")
def read_payment_links(request: Request) -> dict:
    session = _require_session(request)
    try:
        return session.refresh_payment_links()
    except NoPaymentLinkError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gateway.GatewayUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/ingest")
def ingest(request: Request) -> dict:
    """Poll Razorpay for a failed attempt on this session's link and, if one
    exists, run it through the mediation layer."""
    session = _require_session(request)
    try:
        return session.poll_and_ingest()
    except NoPaymentLinkError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except gateway.GatewayUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except (NotAPaymentError, UnsupportedPaymentStateError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/provider-failure")
def provider_failure(request: Request, body: ProviderFailureRequest | None = None) -> dict:
    session = _require_session(request)
    try:
        mode = session.arm_provider_failure(body.mode if body is not None else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"armed": True, "mode": mode}


# --- the webhook ------------------------------------------------------------


@router.post("/webhook")
async def receive_webhook(request: Request) -> Response:
    """Razorpay webhook receiver.

    VERIFIED: HMAC-SHA256 over the RAW body against RAZORPAY_WEBHOOK_SECRET,
    compared with `hmac.compare_digest`. That is the entirety of what
    Razorpay's webhook format supports, and no additional mechanism is
    invented (`sampark.integrations.webhook` documents exactly what this does
    and does not prove).

    An unverified body is refused with 401 and never reaches the ledger, the
    allocator or the chain.

    A verified body for an event this adapter does not act on, or arriving
    with no open session, is ACCEPTED with `ingested: false` and a reason —
    202 rather than an error, so Razorpay's retry loop is not driven by a
    demo that simply is not listening yet.
    """
    session = _session(request)
    raw = await request.body()
    headers = {key: value for key, value in request.headers.items()}

    try:
        envelope = webhook.verify_and_parse(raw, headers)
    except webhook.WebhookConfigError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except webhook.WebhookVerificationError as exc:
        raise HTTPException(status_code=401, detail="webhook signature rejected: " + str(exc))
    except webhook.WebhookMalformedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not envelope.is_recoverable:
        return _json202(
            {
                "accepted": True,
                "ingested": False,
                "event": envelope.event,
                "reason": "this adapter acts only on " + ", ".join(sorted(webhook.RECOVERABLE_EVENTS)),
            }
        )
    if session.run is None:
        return _json202(
            {
                "accepted": True,
                "ingested": False,
                "event": envelope.event,
                "reason": "no Razorpay product session is open, so there is nowhere to record it",
            }
        )
    if not is_recoverable(envelope.entity):
        return _json202(
            {
                "accepted": True,
                "ingested": False,
                "event": envelope.event,
                "reason": "the payment entity's status is not `failed`",
            }
        )

    try:
        body = session.ingest_webhook_payment(
            envelope.entity, envelope.provenance(), envelope.idempotency_key
        )
    except (NotAPaymentError, UnsupportedPaymentStateError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _json202({"accepted": True, "event": envelope.event, **body})


def _json202(body: dict) -> Response:
    import json

    return Response(content=json.dumps(body), media_type="application/json", status_code=202)


# --- the audit stream (the ONLY source of trace data) -----------------------


@router.get("/stream")
def stream(request: Request, after_seq: int = 0) -> StreamingResponse:
    """SSE over `audit_events` in the product session's schema.

    Calls `ui.sse.event_stream` — the SAME function the system demo streams
    through, whose one SQL statement names one table. There is no second
    stream implementation and no second query, so the trace-integrity rule
    covers this page by construction."""
    session = _session(request)
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            after_seq = int(last_event_id)
        except ValueError:
            pass
    try:
        conn = session.open_reader()
    except NoActiveSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    def generate():
        try:
            # The product flow is synchronous: by the time a client is
            # streaming, no write is in flight, so the stream drains what
            # exists and ends. A client reopens it after each action.
            yield from sse.event_stream(conn, after_seq, is_finished=lambda: True)
        finally:
            conn.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/events")
def events(request: Request, after_seq: int = 0, limit: int = 500) -> list[dict]:
    session = _session(request)
    try:
        conn = session.open_reader()
    except NoActiveSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        return sse.fetch_events(conn, after_seq, limit)
    finally:
        conn.close()


@router.get("/verify")
def verify(request: Request) -> dict:
    session = _session(request)
    try:
        conn = session.open_reader()
    except NoActiveSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        report = verify_chain(conn)
        return {
            "valid": report.ok,
            "event_count": report.event_count,
            "genesis_ok": report.genesis_ok,
            "linkage_ok": report.linkage_ok,
            "head_hash": report.head_hash,
            "missing_grant_reservations": [str(g) for g in report.missing_grant_reservations],
            "summary": report.summary(),
        }
    finally:
        conn.close()


@router.get("/explain/request/{request_id}")
def explain(request: Request, request_id: str) -> dict:
    """Reuses `sampark.audit.explain` — never a second explanation engine."""
    session = _session(request)
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="request_id is not a UUID")
    try:
        conn = session.open_reader()
    except NoActiveSessionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        events_for = audit_store.events_for_request(conn, rid)
        if not events_for:
            raise HTTPException(status_code=404, detail="no audit events for request " + request_id)
        try:
            explanation = explain_request(events_for)
        except IncompleteLogError as exc:
            raise HTTPException(status_code=422, detail="incomplete log: " + str(exc))
        all_events = sse.fetch_events(conn, 0, 10_000)
        return {
            "request_id": request_id,
            "explanation": format_explanation(explanation),
            "outcome": explanation.outcome,
            "events": [e for e in all_events if e["payload"].get("request_id") == request_id],
        }
    finally:
        conn.close()
