"""Contact-slot capacity — Design Lock §3.3, §3.6.

`CAPACITY_CONSUMING_STATES` is the single source of truth for "does
this grant/claim state occupy a contact slot, count toward the rolling
caps, and hold a margin reservation" — used by both the mediation
ledger's rolling-count query (sampark/budget/store.py) and the claim
active-index predicate it mirrors. A behavioural test
(tests/budget/test_contact.py) asserts a ROLLED_BACK claim frees its
window, rather than parsing DDL that does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

CAPACITY_CONSUMING_STATES: frozenset[str] = frozenset({"RESERVED", "EXECUTING", "CONFIRMED"})

TERMINAL_STATES: frozenset[str] = frozenset({"CONFIRMED", "ROLLED_BACK", "EXPIRED"})

RELEASING_STATES: frozenset[str] = frozenset({"ROLLED_BACK", "EXPIRED"})


@dataclass(frozen=True)
class ContactCacheUpdate:
    contacts_24h: int
    contacts_7d: int
    last_contact_at: datetime


def next_contact_cache(
    freshly_recomputed_c24: int, freshly_recomputed_c7: int, send_after: datetime
) -> ContactCacheUpdate:
    """The cache value to WRITE after this grant reserves a slot.
    `freshly_recomputed_*` must be the rolling count computed in THIS
    transaction, immediately before this grant — never a value read from
    a prior cache write (Design Lock §3.6: "never blind-incremented")."""
    return ContactCacheUpdate(
        contacts_24h=freshly_recomputed_c24 + 1,
        contacts_7d=freshly_recomputed_c7 + 1,
        last_contact_at=send_after,
    )
