"""Identity resolution — spec §8.2.

Pure functions only; no persistence, no allocation logic.
"""

from __future__ import annotations

from sampark.identity.resolution import (
    ContactSignal,
    canonicalize_email,
    canonicalize_phone,
    email_hash,
    phone_hash,
    resolve_customer_ids,
)

__all__ = [
    "ContactSignal",
    "canonicalize_email",
    "canonicalize_phone",
    "email_hash",
    "phone_hash",
    "resolve_customer_ids",
]
