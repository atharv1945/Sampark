"""SAMPARK Phase 4 budget layer — contact/margin reservation building
blocks, Design Lock §2, §3, §10, §11.

sampark/budget/issuance.py (the owner-authored SERIALIZABLE grant
issuance transaction) is NOT created by this package — see
sampark/budget/store.py's module docstring for the callable contract
it implements and the in-memory reference double used until it lands.
"""

from __future__ import annotations
