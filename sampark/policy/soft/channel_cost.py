"""channel_cost — Design Lock §14.3.

Committed constants, sms/whatsapp/voice. Near-inert at these amounts
(~0.01% of a typical score, per the Design Lock's own reconnaissance
note) — retained because spec §8.6's formula names it, not because it
materially changes any decision.
"""

from __future__ import annotations

from sampark.allocator.constants import CHANNEL_COST_PAISE


def channel_cost_paise(channel: str) -> int:
    return CHANNEL_COST_PAISE[channel]
