"""sampark.policy.compiler.llm — Phase 7. Proves the blocked state is
handled honestly (no fabricated response), never that a live call
succeeded."""

from __future__ import annotations

import pytest

from sampark.policy.compiler.llm import LlmCompilationBlockedError, compile_english_to_ir


def test_compile_raises_blocked_error_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LlmCompilationBlockedError):
        compile_english_to_ir("Never contact anyone by voice.", rule_id="test_rule")


def test_compile_never_returns_a_fabricated_result_without_a_key(monkeypatch):
    """Regression guard against the exact failure mode CLAUDE.md §8
    forbids: this call must raise, never return a plausible-looking
    dict it invented."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        compile_english_to_ir("Never contact anyone by voice.", rule_id="test_rule")
        assert False, "expected LlmCompilationBlockedError, got a return value instead"
    except LlmCompilationBlockedError:
        pass
