"""Golden-corpus pipeline test — Phase 7 (spec §8.4).

Runs every hand-authored (English, expected IR) pair through
parse_ir -> validate (never through the LLM — these ARE the ground
truth, not its output) and asserts the pipeline reaches the expected
verdict. Reports fidelity as a NUMBER split canonical vs paraphrase
(Phase 7 design lock §8.5) — the measure of what an LLM's phrasing
robustness would actually be worth, without needing a live LLM call to
prove the DETERMINISTIC half of the pipeline is correct.
"""

from __future__ import annotations

import pytest

from sampark.policy.compiler.ir import parse_ir
from sampark.policy.compiler.validate import validate
from tests.policy.compiler.golden.corpus import CANONICAL, GOLDEN_CASES, PARAPHRASE


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c.case_id for c in GOLDEN_CASES])
def test_golden_case_reaches_expected_verdict(case):
    rule = parse_ir(case.expected_ir)
    result = validate(rule)
    assert result.verdict.value == case.expect_verdict, (
        f"{case.case_id}: expected {case.expect_verdict}, got {result.verdict.value} ({result.reasons})"
    )


def test_golden_corpus_covers_both_canonical_and_paraphrase():
    kinds = {c.kind for c in GOLDEN_CASES}
    assert kinds == {CANONICAL, PARAPHRASE}


def test_golden_corpus_includes_both_spec_example_fact_unavailable_sentences():
    """The two spec §8.4 example sentences that reference facts this
    system does not have — proving FACT_UNAVAILABLE handling is not
    hypothetical."""
    ids = {c.case_id for c in GOLDEN_CASES}
    assert "spec_no_discount_after_chargeback" in ids
    assert "spec_stop_recovery_on_rto_flag" in ids
    for case_id in ("spec_no_discount_after_chargeback", "spec_stop_recovery_on_rto_flag"):
        case = next(c for c in GOLDEN_CASES if c.case_id == case_id)
        assert case.expect_verdict == "FACT_UNAVAILABLE"


def test_golden_corpus_includes_at_least_one_deliberate_rejection():
    rejected = [c for c in GOLDEN_CASES if c.expect_verdict == "REJECTED"]
    assert len(rejected) >= 1


def test_canonical_fidelity_report(capsys):
    """Fidelity as a NUMBER, split by kind (Phase 7 design lock §8.5).
    'Fidelity' here means: does the DETERMINISTIC half of the pipeline
    (parse + validate) correctly reach the expected verdict for the
    hand-authored expected IR — the LLM's own English->IR fidelity is a
    SEPARATE, blocked measurement (no ANTHROPIC_API_KEY — see
    tests/policy/compiler/test_llm_boundary.py), reported honestly as
    unavailable, never fabricated."""
    for kind in (CANONICAL, PARAPHRASE):
        cases = [c for c in GOLDEN_CASES if c.kind == kind]
        passed = 0
        for case in cases:
            rule = parse_ir(case.expected_ir)
            result = validate(rule)
            if result.verdict.value == case.expect_verdict:
                passed += 1
        print(f"{kind}: {passed}/{len(cases)} reached expected verdict")
        assert passed == len(cases)
