"""consent_scope — DPDP Act 2023 purpose limitation + TCCCPR 7-day
transactional-consent validity, Design Lock §2, §4.3.

Fact source: contact_states.consent_scopes, shape
{intent: {"granted_at": ..., "expires_at": ...}}. This fact is NOT
available in the Phase 1 schema: `consent_scopes = {}` is a
PLACEHOLDER, not a true statement. Read as "no consent recorded" it
would deny every candidate; read as "consent for everything" it would
silently admit every candidate. Both readings are wrong, so this rule
always reports FACT_UNAVAILABLE rather than interpreting the empty
dict either way (Design Lock §4.3's distinction from opt_out.py, where
the empty mapping IS a complete, true statement).

This is a permanent property of the current dataset, not a per-request
check — the rule does not even read ctx.ledger.consent_scopes() to
decide, because there is nothing in it that could ever be a legitimate
"yes" for this synthetic world. It is called here (not skipped) only
so the read happens and the interface stays honest for a future
dataset that does populate this field.
"""

from __future__ import annotations

from sampark.allocator.candidate import Candidate
from sampark.allocator.reason_codes import FACT_UNAVAILABLE_CONSENT_SCOPE
from sampark.policy.types import HardVerdict, PolicyContext


def evaluate(candidate: Candidate, ctx: PolicyContext) -> HardVerdict:
    ctx.ledger.consent_scopes(candidate.customer_id)  # read, but never interpreted — see module docstring
    return HardVerdict.fact_unavailable(FACT_UNAVAILABLE_CONSENT_SCOPE)
