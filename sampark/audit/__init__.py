"""SAMPARK Phase 5 — tamper-evident audit chain + deterministic
explainability. See Phase 5A design doc (session-reported, not
committed) for architecture; CLAUDE.md §7 for the LLM boundary this
package respects (no LLM anywhere in sampark/audit/).

Wired to Phase 4 as of U-2 (Phase 5B, second pass): `AuditSink` /
`PostgresAuditSink` (sampark.audit.sink) is the object
`sampark.mediation.service.mediate_window` and `sim/arm_b.py` call, when
given one, at the points identified in the Phase 5A/5B investigation.
Both default the parameter to `None`, so every pre-U-2 call site is
unaffected.
"""

from __future__ import annotations

from sampark.audit.canonical import GENESIS_HASH, canonical_bytes, hash_event, iso_utc_micros
from sampark.audit.chain import (
    NS_AUDIT,
    AlreadyAppended,
    Appended,
    AppendResult,
    ChainForkError,
    MissingSchemaMigrationError,
    PENDING_PREV_HASH,
    VerificationReport,
    append,
    event_id_for,
    head,
    verify_chain,
)
from sampark.audit.explain import DecisionExplanation, IncompleteLogError, explain_contested_window, explain_request, format_explanation
from sampark.audit.sink import AuditSink, MissingClaimError, PostgresAuditSink

__all__ = [
    "GENESIS_HASH",
    "canonical_bytes",
    "hash_event",
    "iso_utc_micros",
    "NS_AUDIT",
    "PENDING_PREV_HASH",
    "AlreadyAppended",
    "Appended",
    "AppendResult",
    "ChainForkError",
    "MissingSchemaMigrationError",
    "VerificationReport",
    "append",
    "event_id_for",
    "head",
    "verify_chain",
    "DecisionExplanation",
    "IncompleteLogError",
    "explain_contested_window",
    "explain_request",
    "format_explanation",
    "AuditSink",
    "MissingClaimError",
    "PostgresAuditSink",
]
