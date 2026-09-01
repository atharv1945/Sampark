"""Phase 4 protection, asserted in the test suite — Phase 9.

CLAUDE.md §3 makes six paths human-owned and frozen at `aa87123`. Until now
that protection was verified by running `git diff` by hand at the end of each
phase. Phase 9 adds keyword-only parameters to `sim/environment.py` and two new
`sim/` modules, so the protection is worth asserting where it runs every time.

Two independent mechanisms, deliberately:
  1. The frozen VALUES are pinned here, so an edit to the constants fails even
     in a checkout with no git history.
  2. `git diff aa87123 HEAD` over all six paths must be empty, which catches a
     change these tests do not know to look for (a new file under
     `policy/hard/`, a changed docstring, a reordered rule).

Phase 10 note. `test_phase9_touched_no_file_under_sampark` originally compared
`PHASE8_SHA..HEAD`. That was exact while Phase 9 was the newest work, and wrong
the moment Phase 10 (the Razorpay integration) landed, because adding
`sampark/integrations/` is precisely what that phase is for. The assertion is
now anchored to `PHASE9_SHA`, which states the intended fact permanently, and
Phase 10 gets its own live guard —
`test_phase10_confined_itself_to_the_integration_layer` — plus a git-free check
that the Phase 9 audit surface was extended and never reduced. Phase 4
protection itself is untouched and still checked against HEAD.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE4_SHA = "aa87123"

# Phase boundary commits. Named explicitly so each assertion below is about a
# FIXED range of history rather than about "whatever HEAD happens to be" — the
# latter silently becomes an assertion about every later phase too, and expires
# the moment one lands.
PHASE8_SHA = "9849126"
PHASE9_SHA = "50260d0"   # the last Phase 9 commit

PROTECTED_PATHS = (
    "sampark/allocator/constants.py",
    "sampark/allocator/calibrated.py",
    "sampark/budget/issuance.py",
    "sampark/policy/hard/",
    "sampark/policy/soft/",
    "sampark/policy/types.py",
)


def test_frozen_allocator_constants_are_unchanged():
    """Pinned values, not a git check. These are the numbers every committed
    result file was produced against."""
    from sampark.allocator import constants as c

    assert c.CONTACT_CAP_24H == 1
    assert c.CONTACT_CAP_7D == 2
    assert c.QUIET_HOURS_START_HOUR == 21
    assert c.QUIET_HOURS_END_HOUR == 9
    assert c.FORWARD_HORIZON_DAYS == 30.0
    assert c.MAX_DEFERRAL_WINDOWS == 7
    assert c.AGING_BONUS_PAISE == 11_070
    assert c.GRANT_TTL_HOURS == 2.0
    assert c.MAX_SERIALIZATION_RETRIES == 5
    assert c.MERCHANT_MARGIN_BUDGET_PAISE_PER_WINDOW == 3_679_105
    assert c.CUSTOMER_MARGIN_BPS == 500
    assert c.MEAN_AMOUNT_PAISE == 387_607.0
    assert c.LAMBDA_PER_CUSTOMER_DAY == 0.13569
    assert c.CHANNEL_COST_PAISE == {"sms": 20, "whatsapp": 40, "voice": 400}


def test_frozen_simulation_coefficients_are_unchanged():
    """Phase 9 made these into parameters. The MODULE-LEVEL values, which every
    default argument resolves to, must not have moved — otherwise the sweep's
    anchor would silently no longer be the frozen world."""
    from sim.environment import BETA_FATIGUE, BETA_INCENTIVE, OPTOUT_BASE, OPTOUT_MAX

    assert BETA_FATIGUE == 1.0
    assert BETA_INCENTIVE == 4.0
    assert OPTOUT_BASE == 0.06
    assert OPTOUT_MAX == 0.5


def test_frozen_natural_recovery_prior_is_unchanged():
    """A Phase 7 owner prior, precommitted against the observed total-rupee
    gap. Phase 9 does not sweep it; sweeping it post hoc would be retroactive
    tuning."""
    from sim.natural import NATURAL_MULTIPLIER_BY_ROOT_CAUSE as t

    assert t == {
        "issuer_downtime": 0.40,
        "insufficient_funds": 0.35,
        "authentication_drop": 0.25,
        "unknown": 0.15,
        "mandate_expired": 0.10,
        "price_hesitation": 0.10,
        "intent_lost": 0.05,
        "disputed": 0.05,
    }


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60
    )


@pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0, reason="not a git checkout"
)
def test_no_diff_against_the_phase4_baseline_in_any_protected_path():
    result = _git("diff", "--stat", f"{PHASE4_SHA}", "HEAD", "--", *PROTECTED_PATHS)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        "Phase 4 protected files differ from the frozen baseline "
        f"{PHASE4_SHA}:\n{result.stdout}"
    )


@pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0, reason="not a git checkout"
)
def test_phase9_touched_no_file_under_sampark():
    """Phase 9's whole analytical layer lives in `sim/` and `tests/`. If a
    Phase 9 change had ever reached `sampark/`, this fails and forces the
    question to be asked out loud rather than absorbed.

    RE-ANCHORED. This originally compared `PHASE8_SHA..HEAD`, which was exact
    while Phase 9 was the newest work and became wrong the moment Phase 10
    landed: Phase 10 is the Razorpay integration layer, and adding
    `sampark/integrations/` is the entire point of it. The test then reported
    Phase 10's legitimate files as a Phase 9 violation.

    Comparing against `PHASE9_SHA` instead states the fact the test was always
    trying to state — *Phase 9 touched nothing under `sampark/`* — as a
    permanent property of a closed range, so no later phase can make it lie in
    either direction. Phase 10's own footprint is guarded by the next test."""
    result = _git("diff", "--name-only", PHASE8_SHA, PHASE9_SHA, "--", "sampark/")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", (
        "Phase 9 modified files under sampark/, which it is not supposed to:\n" + result.stdout
    )


# Phase 10 — the Razorpay product integration — is the first phase permitted to
# add code under `sampark/`. It may add an ADAPTER; it may not reach into the
# decision path. These are the only two shapes it is allowed to take.
PHASE10_ALLOWED_PREFIXES = (
    "sampark/integrations/",              # the whole adapter layer
    "sampark/demo/razorpay_product.py",   # the flow that drives it
)
# ...plus exactly three EXISTING files, extended additively for the one new
# audit event type. Any other pre-existing file appearing here means the
# integration reached into code it was supposed to leave alone.
PHASE10_ALLOWED_EXTENSIONS = (
    "sampark/audit/event_types.py",
    "sampark/audit/emit.py",
    "sampark/audit/sink.py",
)


@pytest.mark.skipif(
    _git("rev-parse", "--git-dir").returncode != 0, reason="not a git checkout"
)
def test_phase10_confined_itself_to_the_integration_layer():
    """The live successor to the test above. Phase 10 may add an adapter under
    `sampark/integrations/`, the flow that drives it, and an additive extension
    to the three audit modules. Anything else under `sampark/` — the allocator,
    the policy chain, the registry, issuance, the schema — means the
    integration reached somewhere it should not have."""
    result = _git("diff", "--name-only", PHASE9_SHA, "HEAD", "--", "sampark/")
    assert result.returncode == 0, result.stderr
    touched = [line for line in result.stdout.split() if line]
    unexpected = [
        path for path in touched
        if not path.startswith(PHASE10_ALLOWED_PREFIXES)
        and path not in PHASE10_ALLOWED_EXTENSIONS
    ]
    assert unexpected == [], (
        "the Razorpay integration modified files under sampark/ outside the "
        "adapter layer:\n" + "\n".join(unexpected)
    )


# The audit surface as it stood at the close of Phase 9. Pinned as literals so
# this holds in a checkout with no git history, and so an extension that
# quietly REMOVED or renamed something would fail even though the file-path
# check above passed.
PHASE9_EVENT_TYPES = (
    "agent.registered", "agent.struck", "agent.revoked",
    "request.received", "request.denied_on_scope",
    "decision.denied", "decision.deferred",
    "grant.reserved", "grant.executing", "grant.confirmed",
    "grant.rolled_back", "grant.expired",
    "holdout.assigned", "contact.opt_out", "recovery.credited",
    "model.degraded",
)
PHASE9_EMITTERS = (
    "event_for_request_received", "event_for_denied_on_scope", "event_for_decision",
    "event_for_grant_reserved", "event_for_grant_executing", "event_for_grant_confirmed",
    "event_for_grant_rolled_back", "event_for_grant_expired", "event_for_agent_registered",
    "event_for_agent_struck", "event_for_agent_revoked", "event_for_model_degraded",
    "event_for_holdout_assigned", "event_for_contact_opt_out", "event_for_recovery_credited",
)
PHASE9_SINK_METHODS = (
    "record_request_received", "record_denied_on_scope", "record_decision",
    "record_grant_reserved", "record_grant_executing", "record_grant_confirmed",
    "record_grant_rolled_back", "record_grant_expired", "record_agent_registered",
    "record_agent_struck", "record_agent_revoked", "record_model_degraded",
    "record_holdout_assigned", "record_contact_opt_out", "record_recovery_credited",
)


def test_the_phase9_audit_surface_was_extended_and_never_reduced():
    """The three audit modules Phase 10 touched were EXTENDED, not edited.

    Every event type, emitter and sink method that existed at the close of
    Phase 9 must still exist and still be spelled the same way — the committed
    Phase 4-9 chain was written through them, and a rename would make old
    events unexplainable by current code."""
    from sampark.audit import emit
    from sampark.audit.event_types import EVENT_TYPES
    from sampark.audit.sink import PostgresAuditSink

    missing_types = [t for t in PHASE9_EVENT_TYPES if t not in EVENT_TYPES]
    assert missing_types == [], "event types disappeared: " + repr(missing_types)

    missing_emitters = [name for name in PHASE9_EMITTERS if not hasattr(emit, name)]
    assert missing_emitters == [], "emitters disappeared: " + repr(missing_emitters)

    missing_methods = [m for m in PHASE9_SINK_METHODS if not hasattr(PostgresAuditSink, m)]
    assert missing_methods == [], "sink methods disappeared: " + repr(missing_methods)

    # Phase 10 added exactly one type, and it is the integration's own.
    added = set(EVENT_TYPES) - set(PHASE9_EVENT_TYPES)
    assert added == {"payment.risk_detected"}, (
        "the closed event vocabulary grew by more than the one integration "
        "event: " + repr(sorted(added))
    )


def test_committed_phase4_gate_evidence_is_intact():
    """The headline the whole project rests on, re-read from the committed
    file rather than recomputed."""
    import json

    gate = json.loads((_REPO_ROOT / "results" / "gate_headline.json").read_text(encoding="utf-8"))
    assert gate["gate_passed"] is True
    assert gate["constants_commit_sha"] == "aa87123aafdc9d812f5a01c04766c60b9198a2ce"
    assert gate["mean_a_per_contact_paise"] == pytest.approx(89387.38057)
    assert gate["mean_b_per_contact_paise"] == pytest.approx(156957.36981923878)
    assert gate["min_uplift_ratio"] == pytest.approx(1.711386778074912)
    assert gate["max_uplift_ratio"] == pytest.approx(1.8821746334249845)
    assert gate["total_a_contacts"] == 100_000
    assert gate["total_b_contacts"] == 51_542


def test_activated_policy_file_stays_empty_for_protected_evidence():
    """A compiled rule that denied candidates the frozen 11 would admit would
    change Arm B's allocation and invalidate every committed evidence file.
    Phase 7 pinned this; Phase 9 must not be the phase that breaks it."""
    from sampark.policy.compiled import compiled_hard_rules

    assert compiled_hard_rules() == ()
