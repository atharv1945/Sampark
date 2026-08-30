"""sampark.policy.compiler.render — deterministic back-rendering, Phase 7."""

from __future__ import annotations

from sampark.policy.compiler.ir import parse_ir
from sampark.policy.compiler.render import render_english
from sampark.policy.compiler.validate import validate


def test_render_accepted_contact_frequency_cap():
    ir = parse_ir({"rule_id": "r1", "family": "contact_frequency_cap", "params": {"window": "24h", "max_contacts": 0, "channel": "sms"}, "source_text": "x"})
    text = render_english(validate(ir))
    assert "0 time(s) in 24 hours" in text
    assert "sms" in text


def test_render_fact_unavailable_names_the_missing_fact():
    ir = parse_ir({"rule_id": "r2", "family": "incentive_prohibition", "condition": {"fact": "chargeback_90d", "op": "exists"}, "source_text": "no discount after chargeback"})
    text = render_english(validate(ir))
    assert "CANNOT BE ENFORCED" in text
    assert "chargeback_90d" in text


def test_render_rejected_shows_reasons():
    ir = parse_ir({"rule_id": "r3", "family": "contact_frequency_cap", "params": {"window": "24h", "max_contacts": 5}, "source_text": "x"})
    text = render_english(validate(ir))
    assert "REJECTED" in text


def test_render_is_deterministic():
    ir = parse_ir({"rule_id": "r4", "family": "channel_restriction", "params": {"channel": "voice"}, "source_text": "x"})
    result = validate(ir)
    assert render_english(result) == render_english(result)
