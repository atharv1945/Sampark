"""Canonical byte representation + hashing — Phase 5A §4.

ONE canonicalization convention for this repository, reusing exactly the
precedent already approved and load-bearing on the signature path
(sampark/contracts/grant_request.py's `canonical_bytes()`):

    json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

No `hash` field is ever stored on `AuditEvent` (there is none in the
approved contract, and there must not be — a stored hash can be tampered
to agree with a tampered payload; a recomputed one cannot). `hash_event()`
recomputes from the event's own fields every time.

`payload["v"]` selects a canonicalizer so that adding a field is a new
version, never a silent edit to how an existing version's bytes are
produced — an old event must hash identically forever.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sampark.contracts import AuditEvent

GENESIS_HASH = "0" * 64

# Payload string values must be controlled ASCII identifiers — UUIDs,
# dotted reason codes, ISO-UTC timestamps, dates, snake_case ids. This is
# simultaneously Phase 5A §4.3 rule 3 (no free text -> no Unicode
# normalization ambiguity -> deterministic bytes) and the privacy rule
# (§10): the same regex enforces both properties. Applies to PAYLOAD
# string values only — not to `agent_signature` (base64: `+`, `/`, `=`
# are legitimate and already ASCII-safe by construction) or other
# top-level AuditEvent fields, which are already typed by the contract.
_SAFE_PAYLOAD_STRING_RE = re.compile(r"^[A-Za-z0-9_.:+\-]*$")


class NaiveDatetimeError(ValueError):
    """A naive (tzinfo=None) datetime was passed where a canonical
    timestamp is required. Never assumed to be UTC — the caller must say
    so explicitly, because guessing is exactly how the isoformat()
    determinism hazard (Phase 5A §4.3 rule 5) gets reintroduced."""


class PayloadValidationError(ValueError):
    """A payload violates Phase 5A §4.3's determinism/privacy rules:
    a float value, a non-ASCII / non-identifier string, or a type outside
    {str, int, bool, None, dict, list} (i.e. no bytes, no set, no
    datetime — every payload value must already be a canonical-JSON
    primitive when it reaches this module)."""


def iso_utc_micros(dt: datetime) -> str:
    """Fixed-width UTC, literal 'Z', always exactly six microsecond
    digits — Phase 5A §4.3 rule 5. `datetime.isoformat()` is NOT used:
    it omits `.%f` when microseconds are zero and renders the offset
    per `tzinfo` (`+00:00` vs `+05:30`), so byte-identical instants can
    produce different bytes depending on how the datetime arrived at
    this call (freshly constructed vs. round-tripped through Postgres's
    session timezone). This function collapses both hazards by always
    normalizing to UTC and always writing all six fractional digits."""
    if dt.tzinfo is None:
        raise NaiveDatetimeError(f"naive datetime is not allowed in a canonical timestamp: {dt!r}")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _validate_payload_value(value: Any, path: str) -> None:
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return
    if isinstance(value, float):
        raise PayloadValidationError(
            f"payload{path}: float values are banned (Phase 5A §4.3 rule 7) — "
            f"round to int paise before building the payload, got {value!r}"
        )
    if isinstance(value, str):
        if not _SAFE_PAYLOAD_STRING_RE.match(value):
            raise PayloadValidationError(
                f"payload{path}: string {value!r} is not a controlled ASCII identifier "
                f"(Phase 5A §4.3 rule 3 / §10 privacy rule)"
            )
        return
    if isinstance(value, dict):
        for key, sub_value in value.items():
            if not isinstance(key, str) or not _SAFE_PAYLOAD_STRING_RE.match(key):
                raise PayloadValidationError(f"payload{path}: key {key!r} is not a controlled ASCII identifier")
            _validate_payload_value(sub_value, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _validate_payload_value(item, f"{path}[{i}]")
        return
    raise PayloadValidationError(f"payload{path}: unsupported type {type(value).__name__} for value {value!r}")


def validate_payload(payload: dict[str, Any]) -> None:
    """Raises PayloadValidationError / raises nothing. Walks the full
    payload tree — every string must be a controlled ASCII identifier,
    no float anywhere, no unsupported type. Called by every canonicalizer
    version before it builds the preimage, and callable directly by
    emit.py at construction time so a bad payload fails at the point it
    was built, not silently at hash time."""
    if "v" not in payload:
        raise PayloadValidationError("payload must carry payload['v'] (Phase 5A §4.4 versioning)")
    _validate_payload_value(payload, "")


def _canonical_preimage_v1(event: AuditEvent) -> dict[str, Any]:
    validate_payload(event.payload)
    return {
        "event_id": str(event.event_id),
        "event_type": event.event_type,
        "occurred_at": iso_utc_micros(event.occurred_at),
        "prev_hash": event.prev_hash,
        "agent_signature": event.agent_signature,
        "reason_code": event.reason_code,
        "payload": event.payload,
    }


# Version-dispatchable (Phase 5A §4.4): every historical canonicalizer is
# retained forever, so an event written under v=N hashes identically no
# matter how many later versions exist. Adding a payload shape is a new
# entry here, never an edit to an existing one.
_CANONICALIZERS: dict[int, Any] = {
    1: _canonical_preimage_v1,
}


def canonical_bytes(event: AuditEvent) -> bytes:
    """Deterministic UTF-8 JSON encoding of the event's full preimage —
    every AuditEvent field except the absent `hash` (there is none to
    exclude; recomputed hashes are the whole point, Phase 5A §0) and
    the persistence-only `seq` (not a contract field; Phase 5A §4.2)."""
    version = event.payload.get("v")
    if not isinstance(version, int) or version not in _CANONICALIZERS:
        raise PayloadValidationError(
            f"unknown or missing payload version {version!r}; known versions: {sorted(_CANONICALIZERS)}"
        )
    preimage = _CANONICALIZERS[version](event)
    return json.dumps(preimage, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def hash_event(event: AuditEvent) -> str:
    """sha256(canonical_bytes(event)).hexdigest() — always recomputed,
    never read from a stored column (Phase 5A §0, §5.1)."""
    return hashlib.sha256(canonical_bytes(event)).hexdigest()
