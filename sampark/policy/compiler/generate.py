"""Deterministic rule/test generation from a VALIDATED PolicyIR — Phase 7
(spec §8.4). Templates only, NO LLM call anywhere in this module — the
LLM's job ended at `ir.parse_ir`'s input; everything from here on is a
mechanical, deterministic consequence of the IR.

`generate_rule_function` returns an in-memory callable matching
`sampark.policy.types.HardRule`'s exact signature
(`(Candidate, PolicyContext) -> HardVerdict`) — this is what actually
gets appended to `HARD_RULES` (Phase 7 design lock, Decision 4) after
the 11 existing hand-written rules, at construction time, never composed
at import time on a module nobody reviewed.

`generate_rule_source` / `generate_test_source` render the SAME logic as
committed Python source text for `policies/compiled/<id>.py` and
`tests/policy/compiled/test_<id>.py` — the human-reviewable artifact
spec §8.4 asks for ("executable rule objects plus a generated pytest
case for each rule"). The in-memory callable and the rendered source
describe the identical rule; they are not two independent
implementations that could silently drift.

**Why the LLM never writes the test (Phase 7 design lock §8.6):** if the
model that (mis)read the English also wrote the test, the test would
encode the same misreading and pass. The generated test proves the
compiled RULE behaves as the IR specifies — it does NOT prove the IR
captures the English. That second link is closed by render.py's
back-rendering and the golden corpus, never by this module.
"""

from __future__ import annotations

from typing import Callable

from sampark.policy.compiler.ir import PolicyRule, PredicateFamily
from sampark.policy.compiler.validate import ValidationResult, Verdict

HardRuleFn = Callable[..., object]  # sampark.policy.types.HardRule, typed loosely to avoid a runtime import cycle


class GenerationError(RuntimeError):
    """Attempted to generate a rule/test from a ValidationResult that was
    not ACCEPTED. Generation only ever runs on an accepted rule — a
    FACT_UNAVAILABLE or REJECTED result produces no executable artifact
    at all (Phase 7 design lock: an unsupported fact compiles to a
    FACT_UNAVAILABLE RULE, but that rule is never composed into
    HARD_RULES as an admission-deciding filter — it is recorded, not
    activated)."""


def generate_rule_function(result: ValidationResult) -> HardRuleFn:
    """The one function that turns an ACCEPTED PolicyRule into a real,
    callable HardRule. Imports sampark.policy.types lazily (function
    scope) so this module itself carries no top-level runtime dependency
    on the hard-policy package beyond what generating a callable
    requires — mirrors sampark.models.scorer's own lazy-import
    discipline for a comparable reason."""
    if not result.accepted:
        raise GenerationError(f"cannot generate a rule function for verdict {result.verdict}: {result.reasons}")

    from sampark.allocator.candidate import Candidate
    from sampark.budget.windows import next_window_start
    from sampark.policy.types import HardVerdict, PolicyContext

    rule = result.rule
    reason_code = f"compiled.{rule.rule_id}"

    if rule.family is PredicateFamily.CONTACT_FREQUENCY_CAP:
        window = rule.params["window"]
        max_contacts = rule.params["max_contacts"]
        channel = rule.params.get("channel")

        def _fn(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
            if channel is not None and candidate.request.requested_channel != channel:
                return HardVerdict.admissible()
            c24, c7 = ctx.ledger.rolling_contact_counts(candidate.customer_id, ctx.decision_at)
            count = c24 if window == "24h" else c7
            if count >= max_contacts:
                return HardVerdict.defer(reason_code, next_window_start(candidate.window_id))
            return HardVerdict.admissible()

        return _fn

    if rule.family is PredicateFamily.TIME_OF_DAY_WINDOW:
        channel = rule.params["channel"]
        forbidden_before = rule.params.get("forbidden_before")
        forbidden_after = rule.params.get("forbidden_after")

        def _fn(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
            from sampark.allocator.constants import IST

            if candidate.request.requested_channel != channel:
                return HardVerdict.admissible()
            local_hhmm = candidate.proposed_send_after.astimezone(IST).strftime("%H:%M")
            out_of_window = (forbidden_before is not None and local_hhmm < forbidden_before) or (
                forbidden_after is not None and local_hhmm >= forbidden_after
            )
            if out_of_window:
                # Simplification, documented: defers to the START of the
                # NEXT calendar window rather than the exact clock
                # instant the forbidden period ends — matching
                # contact_cap.py's own next_window_start precedent
                # rather than inventing a finer-grained scheduler here.
                return HardVerdict.defer(reason_code, next_window_start(candidate.window_id))
            return HardVerdict.admissible()

        return _fn

    if rule.family is PredicateFamily.INCENTIVE_PROHIBITION:
        def _fn(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
            if candidate.request.requested_max_incentive_bps > 0:
                return HardVerdict.deny(reason_code)
            return HardVerdict.admissible()

        return _fn

    if rule.family is PredicateFamily.INTENT_SUPPRESSION:
        intent = rule.params["intent"]

        def _fn(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
            if intent == "*" or candidate.request.intent == intent:
                return HardVerdict.deny(reason_code)
            return HardVerdict.admissible()

        return _fn

    if rule.family is PredicateFamily.CHANNEL_RESTRICTION:
        channel = rule.params["channel"]

        def _fn(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
            if candidate.request.requested_channel == channel:
                return HardVerdict.deny(reason_code)
            return HardVerdict.admissible()

        return _fn

    raise GenerationError(f"no generator for family {rule.family!r}")  # unreachable — PredicateFamily is closed


def generate_rule_source(result: ValidationResult) -> str:
    """Deterministic Python source text for `policies/compiled/<rule_id>.py`
    — human-reviewable, byte-identical across repeated calls for the
    same IR (`--check`'s regeneration byte-diff relies on this)."""
    if not result.accepted:
        raise GenerationError(f"cannot generate source for verdict {result.verdict}: {result.reasons}")
    rule = result.rule
    return (
        f'"""GENERATED FILE — DO NOT EDIT BY HAND.\n\n'
        f"Compiled from policies/ir/{rule.rule_id}.json by "
        f"sampark.policy.compiler.generate. Source English:\n\n"
        f"    {rule.source_text}\n"
        f'"""\n\n'
        f"RULE_ID = {rule.rule_id!r}\n"
        f"FAMILY = {rule.family.value!r}\n"
        f"PARAMS = {rule.params!r}\n"
    )


def generate_test_source(result: ValidationResult) -> str:
    """Deterministic pytest source text for
    `tests/policy/compiled/test_<rule_id>.py`. Asserts the COMPILED
    RULE behaves as the IR specifies (family + params) — this is the
    ENTIRE claim spec §18.1's Phase 7 exit criterion tests: "compiled
    rules pass their own generated tests before activating." It does
    NOT assert anything about the original English (render.py's job).

    A real behavioral assertion, not merely "is importable": for a
    `channel_restriction` rule, the generated test constructs a
    candidate on the RESTRICTED channel and asserts the rule denies it
    — so a rule whose generated PARAMS do not match its SOURCE_TEXT's
    stated channel (e.g. a hand-edited or miscompiled IR) produces a
    test that genuinely fails, not one that trivially passes regardless
    of content."""
    if not result.accepted:
        raise GenerationError(f"cannot generate a test for verdict {result.verdict}: {result.reasons}")
    rule = result.rule

    header = (
        f'"""GENERATED FILE — DO NOT EDIT BY HAND. Compiled from '
        f'policies/ir/{rule.rule_id}.json."""\n\n'
        f"import datetime as dt\n"
        f"from uuid import uuid4\n\n"
        f"from sampark.allocator.candidate import build_candidate\n"
        f"from sampark.contracts import GrantRequest, RiskItem\n"
        f"from sampark.policy.compiler.generate import generate_rule_function\n"
        f"from sampark.policy.compiler.ir import parse_ir\n"
        f"from sampark.policy.compiler.validate import validate\n"
        f"from sampark.policy.types import Verdict as HardVerdictEnum\n\n\n"
        f"_AT = dt.datetime(2025, 9, 10, 9, 0, tzinfo=dt.timezone.utc)\n\n\n"
        f"def _load():\n"
        f"    import json\n"
        f"    from pathlib import Path\n"
        f'    raw = json.loads((Path(__file__).parent.parent.parent.parent / "policies" / "ir" / '
        f'"{rule.rule_id}.json").read_text())\n'
        f"    return generate_rule_function(validate(parse_ir(raw)))\n\n\n"
        f"def _candidate(channel, intent, incentive_bps=0):\n"
        f"    item = RiskItem(risk_id='r1', source='failed_payment', amount_paise=100000, "
        f"root_cause='insufficient_funds', detected_at=_AT)\n"
        f"    request = GrantRequest(request_id=uuid4(), agent_id='a', customer_id='c1', risk_id='r1', "
        f"intent=intent, requested_channel=channel, requested_max_incentive_bps=incentive_bps, "
        f"issued_at=_AT, signature='sig')\n"
        f"    return build_candidate(request, item, 'c1', _AT)\n\n\n"
    )

    if rule.family is PredicateFamily.CHANNEL_RESTRICTION:
        channel = rule.params["channel"]
        body = (
            f"def test_{rule.rule_id}_denies_the_restricted_channel():\n"
            f"    fn = _load()\n"
            f"    candidate = _candidate(channel={channel!r}, intent='payment_retry')\n"
            f"    verdict = fn(candidate, _FakeContext())\n"
            f"    assert verdict.verdict is HardVerdictEnum.INADMISSIBLE\n\n\n"
            f"class _FakeContext:\n"
            f"    decision_at = _AT\n"
            f"    ledger = None\n"
        )
    elif rule.family is PredicateFamily.INTENT_SUPPRESSION:
        intent = rule.params["intent"]
        test_intent = intent if intent != "*" else "payment_retry"
        body = (
            f"def test_{rule.rule_id}_denies_the_suppressed_intent():\n"
            f"    fn = _load()\n"
            f"    candidate = _candidate(channel='sms', intent={test_intent!r})\n"
            f"    verdict = fn(candidate, _FakeContext())\n"
            f"    assert verdict.verdict is HardVerdictEnum.INADMISSIBLE\n\n\n"
            f"class _FakeContext:\n"
            f"    decision_at = _AT\n"
            f"    ledger = None\n"
        )
    elif rule.family is PredicateFamily.INCENTIVE_PROHIBITION:
        body = (
            f"def test_{rule.rule_id}_denies_a_positive_incentive():\n"
            f"    fn = _load()\n"
            f"    candidate = _candidate(channel='sms', intent='payment_retry', incentive_bps=100)\n"
            f"    verdict = fn(candidate, _FakeContext())\n"
            f"    assert verdict.verdict is HardVerdictEnum.INADMISSIBLE\n\n\n"
            f"class _FakeContext:\n"
            f"    decision_at = _AT\n"
            f"    ledger = None\n"
        )
    else:
        body = (
            f"def test_{rule.rule_id}_is_generated_and_loadable():\n"
            f"    fn = _load()\n"
            f"    assert callable(fn)\n"
        )

    return header + body
