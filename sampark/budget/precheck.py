"""Advisory rolling-counter pre-check — Design Lock §10.3.

SHADOW-ONLY. This module's return type has NO member meaning
"definitely allowed" — only LIKELY_CAPPED and UNKNOWN — and nothing in
sampark/budget or sampark/mediation is permitted to use its verdict to
skip a candidate. It exists to measure how much latency a binding
pre-check WOULD save, at zero correctness risk, per the Design Lock's
pushback on making Redis capable of changing a decision:

    "Any pre-check that can skip a candidate is a second authority. For
    skipping to be safe, the Redis counter would have to be a
    guaranteed LOWER bound on the committed Postgres count. It cannot
    be: a process that dies between COMMIT and the Redis decrement
    leaves the counter high, and a high counter skips a candidate that
    PostgreSQL would have granted."

`sampark.budget.issuance` (owner-authored) must never import `redis` —
tests/budget/test_precheck.py asserts this module is the only place
`redis` is imported, via AST over the whole `sampark.budget` package.

`redis` is NOT a project dependency (requirements.txt is not modified
by this phase). The import is optional and isolated here so the rest
of Phase 4 works with no redis package installed at all.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

try:
    import redis as _redis_module  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover — redis is not an installed dependency
    _redis_module = None

REDIS_AVAILABLE = _redis_module is not None


class PrecheckVerdict(Enum):
    LIKELY_CAPPED = "LIKELY_CAPPED"
    UNKNOWN = "UNKNOWN"


class RollingCounterPrecheck(Protocol):
    def check(self, customer_id: str, decision_at: datetime) -> PrecheckVerdict: ...


class NullPrecheck:
    """Default: always UNKNOWN. Used whenever Redis is unavailable or
    unconfigured — the mediation path is fully functional without it."""

    def check(self, customer_id: str, decision_at: datetime) -> PrecheckVerdict:
        return PrecheckVerdict.UNKNOWN


class RedisRollingCounterPrecheck:
    """Reads a best-effort 24h contact counter from Redis. Any failure —
    connection error, missing key, malformed value — degrades to
    UNKNOWN, never raises, and never returns anything that could be
    read as "definitely allowed"."""

    def __init__(self, client: object, cap_24h: int) -> None:
        if _redis_module is None:
            raise RuntimeError("redis package is not installed")
        self._client = client
        self._cap_24h = cap_24h

    def check(self, customer_id: str, decision_at: datetime) -> PrecheckVerdict:
        try:
            raw = self._client.get(f"sampark:contact24h:{customer_id}")  # type: ignore[attr-defined]
            if raw is None:
                return PrecheckVerdict.UNKNOWN
            count = int(raw)
        except Exception:  # noqa: BLE001 — any fault degrades to UNKNOWN, never propagates
            return PrecheckVerdict.UNKNOWN
        if count >= self._cap_24h:
            return PrecheckVerdict.LIKELY_CAPPED
        return PrecheckVerdict.UNKNOWN


def record_shadow_observation(
    precheck: RollingCounterPrecheck, customer_id: str, decision_at: datetime
) -> PrecheckVerdict:
    """Run the pre-check purely for observation. The caller (mediation
    service, latency instrumentation) MUST NOT branch on this value to
    skip the real hard-filter/allocator path — see module docstring."""
    return precheck.check(customer_id, decision_at)
