"""The Phase 8 HTTP surface — semantics, not status codes for their own sake.

These assert what each endpoint DOES (and refuses to do), not merely that it
returns 200.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.postgres


# --------------------------------------------------------------- lifecycle


def test_health_reports_the_protected_chain_read_only(api_client):
    body = api_client.get("/api/health").json()
    assert body["postgres"] == "ok"
    assert body["public_audit_events_present"] is True
    assert body["public_audit_event_count"] > 0
    assert body["run_state"] == "idle"


def test_reset_is_idempotent_when_idle(api_client):
    assert api_client.post("/api/reset").status_code == 200
    assert api_client.post("/api/reset").status_code == 200


def test_read_endpoints_409_before_a_run_exists(api_client):
    api_client.post("/api/reset")
    for path in ("/api/events", "/api/stream", "/api/verify", "/api/export"):
        assert api_client.get(path).status_code == 409, path


def test_chaos_409s_before_a_run_exists(api_client):
    api_client.post("/api/reset")
    assert api_client.post("/api/chaos/kill_model", json={}).status_code == 409


def test_a_second_concurrent_run_is_refused(api_client):
    """One run per process: parallel runs would multiply schemas and cleanup
    paths for no reviewer value."""
    first = api_client.post("/api/run", json={"pace": True})
    assert first.status_code == 200
    try:
        assert api_client.post("/api/run", json={"pace": True}).status_code == 409
    finally:
        api_client.post("/api/reset")


def test_run_reports_the_computed_compression_ratio_not_a_hard_coded_one(api_client):
    """spec §12.1 forbids unlabelled time manipulation. The badge must carry
    the ratio actually derived from the scenario."""
    body = api_client.post("/api/run", json={"pace": False}).json()
    try:
        ratio = body["compression_ratio_s_per_sim_hour"]
        assert ratio > 0
        assert "SIMULATION" in body["badge_text"]
        assert format(ratio, ".2f") in body["badge_text"]
        assert body["window_count"] >= 1 and body["customer_count"] >= 1
    finally:
        api_client.post("/api/reset")


def test_run_rejects_an_unknown_field(api_client):
    assert api_client.post("/api/run", json={"nope": 1}).status_code == 422


# ------------------------------------------------------------------ events


def test_events_are_served_in_strict_chain_order(demo_api):
    client, _conn = demo_api
    events = client.get("/api/events?limit=5000").json()
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))


def test_events_carry_the_fields_a_reviewer_needs_to_check_the_record(demo_api):
    client, _conn = demo_api
    event = client.get("/api/events?limit=1").json()[0]
    assert set(event) == {
        "seq", "event_id", "event_type", "occurred_at", "prev_hash",
        "hash", "agent_signature", "reason_code", "payload",
    }


def test_after_seq_paginates(demo_api):
    client, _conn = demo_api
    everything = client.get("/api/events?limit=5000").json()
    midpoint = everything[len(everything) // 2]["seq"]
    tail = client.get("/api/events?after_seq=" + str(midpoint) + "&limit=5000").json()
    assert tail[0]["seq"] == midpoint + 1
    assert len(tail) == len(everything) - everything.index(
        next(e for e in everything if e["seq"] == midpoint)
    ) - 1


# ------------------------------------------------------ the run's semantics


def test_the_run_demonstrated_all_three_failures(demo_api):
    """The single most important API assertion: one hands-off replay produces
    every failure spec §12.3 requires, with no chaos input at all."""
    client, _conn = demo_api
    events = client.get("/api/events?limit=5000").json()
    by_type: dict[str, list] = {}
    for e in events:
        by_type.setdefault(e["event_type"], []).append(e)

    # 1. provider timeout -> rollback
    assert by_type.get("grant.rolled_back"), "no rollback occurred"

    # 2. rogue: stage one (scope) and stage two (rate -> strike -> revoke)
    scope_reasons = {e["reason_code"] for e in by_type.get("request.denied_on_scope", [])}
    assert "scope.channel_not_allowed" in scope_reasons
    assert "scope.incentive_ceiling_exceeded" in scope_reasons
    rate = [e for e in by_type.get("decision.denied", []) if e["reason_code"] == "agent.rate_ceiling_exceeded"]
    assert len(rate) == 3
    assert len(by_type.get("agent.struck", [])) == 3
    assert len(by_type.get("agent.revoked", [])) == 1
    assert "scope.agent_revoked" in scope_reasons

    # 3. model kill -> logged degradation
    degraded = {e["reason_code"] for e in by_type.get("model.degraded", [])}
    assert degraded == {"model.artifact_unavailable", "model.killed_by_operator"}

    # ...and the system kept working throughout.
    assert by_type.get("grant.confirmed"), "no grants were confirmed"


def test_status_reflects_real_runner_state_not_a_guess(demo_api):
    client, _conn = demo_api
    status = client.get("/api/status").json()
    assert status["state"] == "finished"
    assert status["rollback_count"] >= 1
    assert status["model_degraded"] is True
    assert status["scorer"] == "HeuristicScorer"
    assert status["error"] is None


def test_scenario_endpoint_exposes_the_scripted_rogue_sequence(demo_api):
    client, _conn = demo_api
    scenario = client.get("/api/scenario").json()
    labels = [r["label"] for r in scenario["rogue_requests"]]
    assert "stage1_channel" in labels and "stage1_incentive" in labels
    assert sum(1 for x in labels if x.startswith("stage2_burst_")) == 6
    assert "post_revocation" in labels
    assert len(scenario["windows"]) >= 1


# ------------------------------------------------------------------- chaos


def test_all_seven_controls_are_advertised_with_their_specification_names(demo_api):
    client, _conn = demo_api
    controls = client.get("/api/chaos").json()
    assert len(controls) == 7
    assert [c["spec_name"] for c in controls] == [
        "Kill uplift model", "Revoke agent key", "Set clock to 21:40",
        "Force provider timeout", "Flood rogue agent to 6 req/min",
        "Mark customer opted-out mid-run", "Trigger RTO flag on an active cart",
    ]
    for control in controls:
        assert control["mechanism"] and control["expected_audit"]


def test_an_unknown_control_is_400_not_a_silent_no_op(demo_api):
    client, _conn = demo_api
    assert client.post("/api/chaos/not_a_control", json={}).status_code == 400


def test_an_inapplicable_control_is_409(demo_api):
    """The run has finished and the rogue is already revoked, so flooding it
    is genuinely impossible. It must say so rather than pretend."""
    client, _conn = demo_api
    response = client.post("/api/chaos/flood_rogue_agent", json={})
    assert response.status_code == 409
    assert "revoked" in response.json()["detail"]


def test_chaos_snapshot_is_labelled_demo_control_state(demo_api):
    """It is real, but it is not system truth, and the API must expose the
    substitution note so the UI can show it."""
    client, _conn = demo_api
    controls = {c["control_id"]: c for c in client.get("/api/chaos").json()}
    assert "SUBSTITUTION" in controls["trigger_interlock_on_cart"]["spec_note"]
