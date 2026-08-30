"""HTTP surface — intentionally small (spec §12.2: "not the focus").

Every route is a thin adapter: validate input, call `sampark.demo` or the
existing Phase 5 audit read-path, map exceptions to status codes. There is
NO business logic here, and in particular:

  * no explanation is composed here — `sampark.audit.explain` does it;
  * no export is serialised here — `sampark.audit.export` does it;
  * no chain is verified here — `sampark.audit.chain.verify_chain` does it;
  * no decision, denial, grant or metric is computed here at all.

`tests/ui/test_explain_export.py` asserts the first three structurally, so a
second explanation engine cannot quietly appear.

Error mapping, once:
    409  no active run / run already active / chaos control inapplicable
    404  unknown request_id (no events for it)
    422  the log cannot support the answer (IncompleteLogError)
    400  malformed chaos control id
    503  Postgres unreachable
"""

from __future__ import annotations

import io
import uuid
from datetime import date

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse

from sampark.audit import export as audit_export
from sampark.audit import store as audit_store
from sampark.audit.chain import verify_chain
from sampark.audit.explain import IncompleteLogError, explain_contested_window, explain_request, format_explanation
from sampark.demo.chaos import ChaosControlId, ChaosInapplicableError
from sampark.demo.isolation import public_audit_fingerprint
from ui import sse
from ui.models import ChaosFireRequest, RunRequest
from ui.session import NoActiveRunError, RunAlreadyActiveError

router = APIRouter()


def _session(request: Request):
    return request.app.state.session


# --- health ---------------------------------------------------------------


@router.get("/api/health")
def health(request: Request) -> dict:
    session = _session(request)
    try:
        conn = session._connect()
    except Exception as exc:  # pragma: no cover - environment failure
        raise HTTPException(status_code=503, detail="postgres unreachable: " + str(exc))
    try:
        count, head = public_audit_fingerprint(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.audit_events')")
            has_audit = cur.fetchone()[0] is not None
    finally:
        conn.close()
    return {
        "postgres": "ok",
        "public_audit_events_present": has_audit,
        "public_audit_event_count": count,
        "public_audit_max_event_id": head,
        "demo_schema": session.schema,
        "run_state": session.status().get("state"),
    }


# --- run lifecycle --------------------------------------------------------


@router.post("/api/run")
def start_run(request: Request, body: RunRequest | None = None) -> dict:
    session = _session(request)
    body = body or RunRequest()
    try:
        return session.start(seed=body.seed, pace=body.pace)
    except RunAlreadyActiveError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/api/reset")
def reset_run(request: Request) -> dict:
    return _session(request).reset()


@router.get("/api/status")
def status(request: Request) -> dict:
    return _session(request).status()


@router.get("/api/scenario")
def scenario(request: Request) -> dict:
    return _session(request).scenario_brief()


# --- the audit stream (the ONLY source of trace data) ---------------------


@router.get("/api/stream")
def stream(request: Request, after_seq: int = 0) -> StreamingResponse:
    session = _session(request)
    last_event_id = request.headers.get("last-event-id")
    if last_event_id:
        try:
            after_seq = int(last_event_id)
        except ValueError:
            pass  # malformed resume header: start from `after_seq`
    try:
        conn = session.open_reader()
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    def generate():
        try:
            yield from sse.event_stream(conn, after_seq, is_finished=lambda: not session.is_running())
        finally:
            conn.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/events")
def events(request: Request, after_seq: int = 0, limit: int = 500) -> list[dict]:
    session = _session(request)
    try:
        conn = session.open_reader()
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        return sse.fetch_events(conn, after_seq, limit)
    finally:
        conn.close()


# --- explainability (reuses sampark.audit.explain, never reimplements) ----


@router.get("/api/explain/request/{request_id}")
def explain(request: Request, request_id: str) -> dict:
    session = _session(request)
    try:
        rid = uuid.UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="request_id is not a UUID")
    try:
        conn = session.open_reader()
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        events_for = audit_store.events_for_request(conn, rid)
        if not events_for:
            raise HTTPException(status_code=404, detail="no audit events for request " + request_id)
        try:
            explanation = explain_request(events_for)
        except IncompleteLogError as exc:
            raise HTTPException(status_code=422, detail="incomplete log: " + str(exc))
        # The raw events the sentence was derived FROM are returned alongside
        # it, so the UI can show both and a viewer can check the explanation
        # against the record rather than taking it on trust (spec §12.1).
        all_events = sse.fetch_events(conn, 0, 10_000)
        return {
            "request_id": request_id,
            "explanation": format_explanation(explanation),
            "outcome": explanation.outcome,
            "events": [e for e in all_events if e["payload"].get("request_id") == request_id],
        }
    finally:
        conn.close()


@router.get("/api/explain/window/{customer_id}/{window_id}")
def explain_window(request: Request, customer_id: str, window_id: str) -> dict:
    session = _session(request)
    try:
        window = date.fromisoformat(window_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="window_id must be an ISO date")
    try:
        conn = session.open_reader()
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        events_for = audit_store.events_for_customer_window(conn, customer_id, window)
        if not events_for:
            raise HTTPException(status_code=404, detail="no audit events for that customer/window")
        try:
            summary = explain_contested_window(events_for)
        except IncompleteLogError as exc:
            raise HTTPException(status_code=422, detail="incomplete log: " + str(exc))
        def as_dict(c) -> dict:
            return {
                "request_id": str(c.request_id),
                "agent_id": c.agent_id,
                "risk_id": c.risk_id,
                "outcome": c.outcome,
                "reason_code": c.reason_code,
                "expected_net_paise": c.expected_net_paise,
            }

        # `winner` / `losers` are `sampark.audit.explain`'s own field names —
        # copied, not restated under different ones, so the API cannot drift
        # from the explanation engine's shape.
        return {
            "customer_id": summary.customer_id,
            "window_id": summary.window_id,
            "winner": as_dict(summary.winner) if summary.winner is not None else None,
            "competitors": (
                ([as_dict(summary.winner)] if summary.winner is not None else [])
                + [as_dict(c) for c in summary.losers]
            ),
        }
    finally:
        conn.close()


# --- verification + export (both reuse Phase 5 code unchanged) ------------


@router.get("/api/verify")
def verify(request: Request) -> dict:
    session = _session(request)
    try:
        conn = session.open_reader()
    except NoActiveRunError as exc:
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


@router.get("/api/export")
def export(request: Request) -> Response:
    session = _session(request)
    try:
        conn = session.open_reader()
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        buffer = io.StringIO()
        audit_export.export_jsonl(conn, buffer)
        return Response(
            content=buffer.getvalue(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="sampark-demo-audit.jsonl"'},
        )
    finally:
        conn.close()


# --- chaos ----------------------------------------------------------------


@router.get("/api/chaos")
def chaos_list(request: Request) -> list[dict]:
    return _session(request).chaos_snapshot()


@router.post("/api/chaos/{control_id}")
def chaos_fire(request: Request, control_id: str, body: ChaosFireRequest | None = None) -> dict:
    session = _session(request)
    try:
        parsed = ChaosControlId(control_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown chaos control: " + control_id)
    target = body.target if body is not None else None
    try:
        effect = session.fire_chaos(parsed, target)
    except NoActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ChaosInapplicableError as exc:
        # 409, no state change, no audit event. Never a fabricated effect.
        raise HTTPException(status_code=409, detail=str(exc))
    return {"control_id": control_id, "effect": effect}
