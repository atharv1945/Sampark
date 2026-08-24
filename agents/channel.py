"""Mock channel adapters — Arm A (CLAUDE.md §8, spec §18.2).

No network I/O, no real customer communication. The recorded payload is
exactly what would have been sent, but carries only customer_id (a
resolved, hashed-identity id) — a raw phone number or email address is
never in scope for this module, since ContactAction itself never carries
one.

Mock delivery is always successful in Phase 2 — no deliverability
modeling (spec §18.2 lists deliverability as an explicit, out-of-scope
limitation).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Mapping

from agents.types import ContactAction

SUPPORTED_CHANNELS = ("sms", "whatsapp", "voice")


@dataclass(frozen=True)
class DeliveryReceipt:
    action: ContactAction
    delivered: bool
    payload: Mapping[str, Any]


class ChannelAdapter(abc.ABC):
    channel: str

    @abc.abstractmethod
    def send(self, action: ContactAction) -> DeliveryReceipt:
        raise NotImplementedError


class MockChannelAdapter(ChannelAdapter):
    """One generic mock adapter, parameterised by channel name."""

    def __init__(self, channel: str) -> None:
        if channel not in SUPPORTED_CHANNELS:
            raise ValueError(f"Unsupported channel: {channel!r}")
        self.channel = channel

    def send(self, action: ContactAction) -> DeliveryReceipt:
        if action.channel != self.channel:
            raise ValueError(
                f"{self.channel} adapter cannot send a {action.channel!r} action "
                f"(risk_id={action.risk_id!r})"
            )
        payload = {
            "channel": self.channel,
            "to_customer_id": action.customer_id,
            "agent_id": action.agent_id,
            "intent": action.intent,
            "risk_id": action.risk_id,
            "incentive_bps": action.incentive_bps,
            "scheduled_at": action.scheduled_at.isoformat(),
        }
        return DeliveryReceipt(action=action, delivered=True, payload=payload)
