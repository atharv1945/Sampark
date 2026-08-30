"""sampark.policy.compiler.generate — rule/test generation, Phase 7 (spec §8.4).

Proves the generated test is a REAL behavioral assertion (not a trivial
"is importable" stub) by executing generated source against both a
correct rule and a deliberately corrupted one — demonstrating the
activation gate (spec §18.1's Phase 7 exit criterion: "compiled rules
pass their own generated tests before activating") is a genuine gate,
not a decoration. At least one case here is a rule whose generated test
FAILS and is therefore correctly refused activation.
"""

from __future__ import annotations

import datetime as dt
import types
from uuid import uuid4

import pytest

from sampark.allocator.candidate import build_candidate
from sampark.contracts import GrantRequest, RiskItem
from sampark.policy.compiler.generate import generate_rule_function, generate_test_source
from sampark.policy.compiler.ir import parse_ir
from sampark.policy.compiler.validate import validate
from sampark.policy.types import PolicyContext, Verdict as HardVerdictEnum

AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)


class _FakeLedger:
    def rolling_contact_counts(self, customer_id, decision_at):
        return (0, 0)


def _candidate(channel="voice", intent="payment_retry", incentive_bps=0):
    item = RiskItem(risk_id="r1", source="failed_payment", amount_paise=100_000, root_cause="insufficient_funds", detected_at=AT)
    request = GrantRequest(
        request_id=uuid4(), agent_id="a", customer_id="c1", risk_id="r1", intent=intent,
        requested_channel=channel, requested_max_incentive_bps=incentive_bps, issued_at=AT, signature="sig",
    )
    return build_candidate(request, item, "c1", AT)


def _run_generated_test(rule_id: str, rule_fn):
    """Executes the generated pytest source in an isolated namespace,
    substituting `rule_fn` for `_load()`'s output — this is exactly the
    activation gate's actual check: does THIS rule function pass ITS
    generated test."""
    channel_restriction_ir = {
        "rule_id": rule_id, "family": "channel_restriction", "params": {"channel": "voice"},
        "source_text": "Never contact customers by voice call.",
    }
    result = validate(parse_ir(channel_restriction_ir))
    source = generate_test_source(result)

    namespace: dict = {}
    exec(compile(source, f"<generated:{rule_id}>", "exec"), namespace)
    namespace["_load"] = lambda: rule_fn
    test_fn_name = f"test_{rule_id}_denies_the_restricted_channel"
    test_fn = namespace[test_fn_name]
    test_fn()  # raises AssertionError on failure, exactly like pytest would


def test_generated_test_passes_for_the_correctly_compiled_rule():
    ir = {
        "rule_id": "voice_ban_correct", "family": "channel_restriction", "params": {"channel": "voice"},
        "source_text": "Never contact customers by voice call.",
    }
    result = validate(parse_ir(ir))
    correct_fn = generate_rule_function(result)
    _run_generated_test("voice_ban_correct", correct_fn)  # must not raise


def test_generated_test_fails_for_a_deliberately_wrong_rule_and_it_is_therefore_not_activated():
    """THE deliberate-failure demonstration (Phase 7 design lock §8.8):
    a rule function that restricts the WRONG channel (sms, not voice —
    simulating a corrupted or hand-edited compiled artifact whose
    params no longer match the IR `--check` would have caught) must
    fail its generated test. An activation script (policies/activated.yaml's
    real gate) would refuse to add this rule_id — demonstrated here by
    the AssertionError itself, which is exactly what would abort a
    `pytest tests/policy/compiled/` run before any owner could commit
    the rule_id to activated.yaml."""
    from sampark.policy.types import HardVerdict

    def _wrong_fn(candidate, ctx):
        # Deliberately restricts 'sms', not 'voice' -- contradicts the
        # IR's own params (channel='voice') the generated test checks
        # against, simulating a compiled/IR mismatch.
        if candidate.request.requested_channel == "sms":
            return HardVerdict.deny("compiled.wrong")
        return HardVerdict.admissible()

    with pytest.raises(AssertionError):
        _run_generated_test("voice_ban_wrong", _wrong_fn)


def test_generate_rule_source_is_deterministic():
    ir = {
        "rule_id": "det_test", "family": "channel_restriction", "params": {"channel": "voice"},
        "source_text": "Never contact customers by voice call.",
    }
    result = validate(parse_ir(ir))
    from sampark.policy.compiler.generate import generate_rule_source

    a = generate_rule_source(result)
    b = generate_rule_source(result)
    assert a == b


def test_generate_test_source_is_deterministic():
    ir = {
        "rule_id": "det_test2", "family": "channel_restriction", "params": {"channel": "voice"},
        "source_text": "Never contact customers by voice call.",
    }
    result = validate(parse_ir(ir))
    a = generate_test_source(result)
    b = generate_test_source(result)
    assert a == b


def test_generation_refuses_a_non_accepted_result():
    from sampark.policy.compiler.generate import GenerationError

    ir = {
        "rule_id": "bad", "family": "incentive_prohibition",
        "condition": {"fact": "chargeback_90d", "op": "exists"}, "source_text": "x",
    }
    result = validate(parse_ir(ir))
    assert not result.accepted
    with pytest.raises(GenerationError):
        generate_rule_function(result)
    with pytest.raises(GenerationError):
        generate_test_source(result)
