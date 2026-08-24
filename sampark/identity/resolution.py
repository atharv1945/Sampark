"""Identity resolution — spec §8.2.

"Signals arrive from Razorpay webhooks (test mode) and the simulator. The
normaliser maps each to a RISK_ITEM with a canonical amount, source, and
detected time, then resolves the owner to one CUSTOMER via hashed
phone/email. Deduplication happens here, before any agent sees anything."

Pipeline: canonicalize -> SHA-256 -> union-find into equivalence classes.

`resolve_customer_ids` never accepts a ground-truth person identifier —
`ContactSignal` carries only a `signal_id` (the raw signal's own id, e.g.
a risk_id) and the two hashes. This is deliberate: in production there is
no ground-truth id to resolve against, only hashed contact info, and this
module's public API is held to that standard by construction rather than
by convention.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence


def canonicalize_phone(raw: str) -> str | None:
    """Digits-only, collapsed to the bare 10-digit subscriber number.

    Strips formatting (spaces, dashes, parens) and a leading country/trunk
    prefix (e.g. "+91", "091") by keeping only the last 10 digits. Returns
    None if fewer than 10 digits remain — not a usable phone number.
    """
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 10:
        return None
    return digits[-10:]


def canonicalize_email(raw: str) -> str | None:
    """Trimmed, lowercased email. Returns None if nothing remains."""
    email = raw.strip().lower()
    return email or None


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def phone_hash(raw_phone: str | None) -> str | None:
    if raw_phone is None:
        return None
    canonical = canonicalize_phone(raw_phone)
    return _sha256_hex(canonical) if canonical else None


def email_hash(raw_email: str | None) -> str | None:
    if raw_email is None:
        return None
    canonical = canonicalize_email(raw_email)
    return _sha256_hex(canonical) if canonical else None


@dataclass(frozen=True)
class ContactSignal:
    """One raw signal's hashed identity fields.

    `signal_id` identifies the raw signal itself (e.g. a risk_id) — it is
    never a ground-truth person id, and nothing in this module treats it
    as one.
    """

    signal_id: str
    phone_hash: str | None
    email_hash: str | None


class _UnionFind:
    """Union-find keyed by arbitrary string nodes, path-compressing find."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self._parent.setdefault(key, key)
        while self._parent[key] != key:
            self._parent[key] = self._parent[self._parent[key]]
            key = self._parent[key]
        return key

    def union(self, a: str, b: str) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a == root_b:
            return
        # Deterministic tie-break so the resulting tree (and therefore
        # every root id) does not depend on the order signals are unioned
        # in — required for run-to-run reproducibility.
        if root_b < root_a:
            root_a, root_b = root_b, root_a
        self._parent[root_b] = root_a


def resolve_customer_ids(signals: Sequence[ContactSignal]) -> dict[str, str]:
    """Map each signal_id to a deterministic customer_id.

    Two signals sharing a phone_hash or an email_hash resolve to the same
    customer, transitively. A signal with neither hash is its own
    singleton — nothing to deduplicate it against.

    customer_id is derived from the equivalence class's own hash material
    (sorted, hashed) so that resolving the same population twice with the
    same seed reproduces the same ids — never a random UUID.
    """
    uf = _UnionFind()
    node_of_signal: dict[str, str] = {}

    for signal in signals:
        node = f"signal:{signal.signal_id}"
        node_of_signal[signal.signal_id] = node
        uf.find(node)
        if signal.phone_hash:
            uf.union(node, f"phone:{signal.phone_hash}")
        if signal.email_hash:
            uf.union(node, f"email:{signal.email_hash}")

    identity_keys_by_root: dict[str, set[str]] = {}
    signal_ids_by_root: dict[str, list[str]] = {}
    for signal in signals:
        root = uf.find(node_of_signal[signal.signal_id])
        signal_ids_by_root.setdefault(root, []).append(signal.signal_id)
        keys = identity_keys_by_root.setdefault(root, set())
        if signal.phone_hash:
            keys.add(f"phone:{signal.phone_hash}")
        if signal.email_hash:
            keys.add(f"email:{signal.email_hash}")

    customer_id_by_root: dict[str, str] = {}
    for root, signal_ids in signal_ids_by_root.items():
        identity_keys = identity_keys_by_root[root]
        # A group with no hash material at all (neither phone nor email on
        # any of its signals) has nothing identity-derived to key on; fall
        # back to its own signal ids so it still gets a stable id.
        basis = identity_keys or {f"signal:{sid}" for sid in signal_ids}
        canonical = ",".join(sorted(basis))
        customer_id_by_root[root] = "cust_" + _sha256_hex(canonical)[:24]

    return {
        signal.signal_id: customer_id_by_root[uf.find(node_of_signal[signal.signal_id])]
        for signal in signals
    }
