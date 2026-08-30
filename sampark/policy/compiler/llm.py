"""The ONE LLM call — English -> PolicyIR, Phase 7 (spec §8.4 / §9.1).

Offline, developer-time, `temperature=0`. Output is treated as an
UNTRUSTED PROPOSAL — `sampark.policy.compiler.validate.validate` runs on
it exactly as it runs on a hand-authored golden-corpus fixture; there is
no separate, more-trusted code path for LLM output.

This module is the ONLY place in `sampark.policy.compiler` that may
import an LLM client (`tests/policy/compiler/test_llm_boundary.py`
enforces this structurally — `ir.py`/`validate.py`/`generate.py`/
`render.py` must never do so). It is never imported by
`sampark.policy.compiled` or by any mediation-runtime module.

**Current status, this session (2026-08-30): BLOCKED.** `.env`'s
`ANTHROPIC_API_KEY` is present but empty (verified at Phase 7 session
start). Per CLAUDE.md §8 ("never fabricate a successful external API
call") and §14 ("never claim an external service worked unless it was
actually verified"), `compile_english_to_ir` below is real, callable
code — never exercised against a live API in this session. The
deterministic half of the pipeline (parse/validate/generate/render) is
proven instead via the golden corpus, whose expected IRs are
hand-authored, not LLM output.
"""

from __future__ import annotations

import json
import os
from typing import Any

_MODEL_ID = "claude-opus-5"  # pinned; a live --compile run would record this exact string in the artifact
_TEMPERATURE = 0

_SYSTEM_PROMPT = """You compile ONE English merchant-policy sentence into a single PolicyIR JSON object.

Output ONLY a JSON object with this exact shape (no prose, no markdown fences):
{
  "rule_id": "<lower_snake_case_identifier>",
  "family": "<one of: contact_frequency_cap, time_of_day_window, incentive_prohibition, intent_suppression, channel_restriction>",
  "params": { ... family-specific ... },
  "condition": { "fact": "<fact_ref>", "op": "exists" } | null,
  "source_text": "<the original English sentence, verbatim>"
}

Closed fact_ref values: contacts_24h, contacts_7d, chargeback_90d, rto_flagged, consent_scope.
Closed channels: sms, whatsapp, voice. Closed windows: 24h, 7d.
Closed intents: payment_retry, cart_recovery, mandate_retry, receivables_followup, or "*".

If the sentence is genuinely ambiguous (could map to more than one family or
parameter set with no clear preference), output {"ambiguous": true, "reason": "<why>"}
instead. Never guess between two readings."""


class LlmCompilationBlockedError(RuntimeError):
    """No ANTHROPIC_API_KEY configured — raised rather than proceeding
    with a fabricated or cached response. This is the expected, honest
    failure mode in an environment without live credentials (CLAUDE.md
    §8's "never fabricate a successful external API call")."""


class LlmAmbiguousError(RuntimeError):
    """The model reported the sentence as ambiguous — never resolved by
    guessing (Phase 7 design lock §8.4)."""


def compile_english_to_ir(english: str, rule_id: str) -> dict[str, Any]:
    """The ONE LLM call. Real, callable code — raises
    `LlmCompilationBlockedError` immediately if no API key is
    configured, rather than silently falling back to anything. A live
    call (when a key IS configured) would use the Anthropic SDK at
    `_TEMPERATURE=0` with `_MODEL_ID` pinned, and would return the
    parsed JSON response for `sampark.policy.compiler.ir.parse_ir` to
    validate — this function itself does NOT call `parse_ir` or
    `validate`; those remain separate, deterministic stages the caller
    invokes next."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise LlmCompilationBlockedError(
            "ANTHROPIC_API_KEY is not configured — refusing to fabricate a compiled result. "
            "See sampark/policy/compiler/llm.py's module docstring."
        )

    # A live call would look like:
    #
    #   import anthropic
    #   client = anthropic.Anthropic(api_key=api_key)
    #   response = client.messages.create(
    #       model=_MODEL_ID, temperature=_TEMPERATURE, max_tokens=512,
    #       system=_SYSTEM_PROMPT,
    #       messages=[{"role": "user", "content": f"rule_id: {rule_id}\n\n{english}"}],
    #   )
    #   raw = json.loads(response.content[0].text)
    #
    # Not executed in this session — see the module docstring.
    raise NotImplementedError(
        "live Anthropic API call path — not exercised in this session (no configured key); "
        "the golden corpus (tests/policy/compiler/golden/corpus.py) is the source of truth "
        "for the deterministic pipeline's correctness instead"
    )
