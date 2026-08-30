"""Explainability, export and verification — all REUSED, never reimplemented.

Spec §8.10: the explanation is generated "from the log, never from the
model's memory. It can be wrong about phrasing; it cannot be wrong about
facts." Phase 8 therefore calls `sampark.audit.explain` and returns both the
sentence AND the events it came from, so a viewer can check one against the
other instead of trusting it.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

pytestmark = pytest.mark.postgres


# ------------------------------------------------------------------ explain


def _first_of(client, event_type, reason_code=None):
    for event in client.get("/api/events?limit=5000").json():
        if event["event_type"] == event_type and (
            reason_code is None or event["reason_code"] == reason_code
        ):
            return event
    raise AssertionError("no " + event_type + " event in this run")


def test_a_scope_denial_explains_that_the_allocator_never_ran(demo_api):
    """The single most important sentence in the demo."""
    client, _conn = demo_api
    event = _first_of(client, "request.denied_on_scope", "scope.channel_not_allowed")
    body = client.get("/api/explain/request/" + event["payload"]["request_id"]).json()
    assert body["outcome"] == "DENIED"
    assert "DENIED on scope" in body["explanation"]
    assert "allocator never ran" in body["explanation"]


def test_a_granted_request_explains_its_grant(demo_api):
    client, _conn = demo_api
    event = _first_of(client, "grant.reserved")
    body = client.get("/api/explain/request/" + event["payload"]["request_id"]).json()
    assert body["outcome"] == "GRANTED"
    assert "GRANTED" in body["explanation"]
    assert "Scope check passed" in body["explanation"]


def test_a_rate_ceiling_denial_is_explained_with_its_reason_code(demo_api):
    client, _conn = demo_api
    event = _first_of(client, "decision.denied", "agent.rate_ceiling_exceeded")
    body = client.get("/api/explain/request/" + event["payload"]["request_id"]).json()
    assert "agent.rate_ceiling_exceeded" in body["explanation"]


def test_the_explanation_ships_with_the_events_it_was_derived_from(demo_api):
    """So the sentence can be checked against the record rather than trusted."""
    client, _conn = demo_api
    event = _first_of(client, "grant.confirmed")
    request_id = event["payload"]["request_id"]
    body = client.get("/api/explain/request/" + request_id).json()
    assert body["events"], "no supporting events returned"
    assert all(e["payload"]["request_id"] == request_id for e in body["events"])
    assert {e["event_type"] for e in body["events"]} >= {
        "request.received", "grant.reserved", "grant.executing", "grant.confirmed"
    }


def test_explain_is_404_for_an_unknown_request_and_400_for_a_non_uuid(demo_api):
    client, _conn = demo_api
    assert client.get("/api/explain/request/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/explain/request/not-a-uuid").status_code == 400


def test_contested_window_explanation_reconstructs_the_competitors(demo_api):
    client, conn = demo_api
    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload->>'customer_id', payload->>'window_id', count(*) "
            "FROM audit_events WHERE payload ? 'window_id' AND payload ? 'customer_id' "
            "GROUP BY 1,2 ORDER BY 3 DESC LIMIT 1"
        )
        customer_id, window_id, _n = cur.fetchone()
    body = client.get("/api/explain/window/" + customer_id + "/" + window_id).json()
    assert body["customer_id"] == customer_id
    assert isinstance(body["competitors"], list) and body["competitors"]


def test_explain_window_rejects_a_bad_date(demo_api):
    client, _conn = demo_api
    assert client.get("/api/explain/window/cust_x/not-a-date").status_code == 400


# ------------------------------------------------------------------- verify


def test_verify_reports_a_valid_chain_after_all_three_failures(demo_api):
    client, _conn = demo_api
    body = client.get("/api/verify").json()
    assert body["valid"] is True
    assert body["genesis_ok"] is True and body["linkage_ok"] is True
    assert body["missing_grant_reservations"] == []
    assert len(body["head_hash"]) == 64
    assert "VALID: True" in body["summary"]


# ------------------------------------------------------------------- export


def test_export_streams_canonical_jsonl_that_re_hashes_into_the_chain(demo_api):
    """Each exported line is the event's canonical bytes, so a reader can
    re-hash any line and get the value its successor's prev_hash must equal.
    That is the whole point of the export format."""
    import hashlib

    client, _conn = demo_api
    response = client.get("/api/export")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")

    lines = [line for line in response.text.splitlines() if line.strip()]
    summary = json.loads(lines[-1])
    assert "export_summary" in summary

    events = [json.loads(line) for line in lines[:-1]]
    assert len(events) == summary["export_summary"]["event_count"]

    for previous_line, current in zip(lines[:-2], events[1:]):
        digest = hashlib.sha256(previous_line.encode("utf-8")).hexdigest()
        assert current["prev_hash"] == digest, "exported line does not re-hash into the chain"

    assert hashlib.sha256(lines[-2].encode("utf-8")).hexdigest() == summary["export_summary"]["head_hash"]


# ------------------------------------------------- no second engine anywhere


def test_the_route_layer_delegates_and_does_not_reimplement():
    """§14 of the design lock: reuse `sampark.audit.explain` / `.export` /
    `.chain`, never build a parallel engine that could disagree with the log."""
    source = pathlib.Path("ui/routes.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported.add(node.module + "." + alias.name)
    assert "sampark.audit.explain.format_explanation" in imported
    assert "sampark.audit.explain.explain_request" in imported
    assert "sampark.audit.explain.explain_contested_window" in imported
    assert "sampark.audit.chain.verify_chain" in imported

    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "export_jsonl" in called
    # No hand-rolled hashing or chain walking in the route layer.
    assert "sha256" not in source and "GENESIS_HASH" not in source
