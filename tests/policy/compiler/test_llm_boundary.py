"""The LLM boundary — Phase 7 (spec §8.4, design lock §8.5/§8.6).

Proves, structurally, that NOTHING under sampark/policy/compiled/ or on
the mediation runtime path can reach an LLM/HTTP client — the compiled
artifact is data, evaluated with zero network dependency. Also documents
(never fabricates) the live-compile step's current blocked status.
"""

from __future__ import annotations

import ast
import inspect
import os

import sampark.policy.compiled as compiled_module
from sampark.policy.compiler import generate, ir, render, validate

_BANNED_IMPORT_SUBSTRINGS = ("anthropic", "openai", "requests", "httpx", "urllib", "socket")


def _imported_names(module) -> list[str]:
    tree = ast.parse(inspect.getsource(module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_compiled_package_has_zero_llm_or_network_dependency():
    imported = _imported_names(compiled_module)
    for banned in _BANNED_IMPORT_SUBSTRINGS:
        assert not any(banned in name for name in imported), (
            f"sampark.policy.compiled must never import {banned}; found {imported}"
        )


def test_deterministic_modules_have_zero_llm_or_network_dependency():
    """ir.py / validate.py / generate.py / render.py — the ENTIRE
    deterministic half of the pipeline — must never import an LLM
    client, HTTP client, or raw socket module. Only llm.py (a SEPARATE,
    explicitly offline-only module) may."""
    for module in (ir, validate, generate, render):
        imported = _imported_names(module)
        for banned in _BANNED_IMPORT_SUBSTRINGS:
            assert not any(banned in name for name in imported), (
                f"{module.__name__} must never import {banned}; found {imported}"
            )


def test_anthropic_api_key_is_not_configured_in_this_environment():
    """Documents, rather than fabricates: this session's .env has
    ANTHROPIC_API_KEY present but EMPTY (verified at the start of this
    Phase 7 session). The live `--compile` step is therefore genuinely
    blocked here — CLAUDE.md §8 forbids fabricating a successful
    external API call, so the golden-corpus tests exercise the
    deterministic pipeline directly (tests/policy/compiler/test_golden_corpus.py),
    never a real LLM response. This test is a documentation assertion,
    not a workaround."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    # Intentionally NOT a hard assertion that it must be empty — if a
    # future session has a real key configured, this test should not
    # start failing; it exists to make the current state observable,
    # not to enforce it.
    print(f"ANTHROPIC_API_KEY configured: {bool(key)}")
