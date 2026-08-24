"""MockChannelAdapter — no network I/O, no real communication, no raw
contact fields in the recorded payload (CLAUDE.md §8)."""

from __future__ import annotations

import datetime as dt

import pytest

import agents.channel as channel_module
from agents.channel import MockChannelAdapter
from agents.types import ContactAction


def _action(channel: str = "sms") -> ContactAction:
    return ContactAction(
        agent_id="payment_retry_agent",
        risk_id="fp-1",
        customer_id="cust-1",
        channel=channel,
        intent="payment_retry",
        incentive_bps=0,
        scheduled_at=dt.datetime(2025, 9, 1, 12, 0, tzinfo=dt.timezone.utc),
    )


def test_send_delivers_and_records_payload() -> None:
    adapter = MockChannelAdapter("sms")
    receipt = adapter.send(_action())
    assert receipt.delivered is True
    assert receipt.payload["to_customer_id"] == "cust-1"
    assert receipt.payload["channel"] == "sms"


def test_payload_never_contains_raw_contact_fields() -> None:
    adapter = MockChannelAdapter("whatsapp")
    receipt = adapter.send(_action(channel="whatsapp"))
    forbidden_keys = {"phone", "email", "raw_phone", "raw_email", "phone_number", "phone_hash", "email_hash"}
    assert forbidden_keys.isdisjoint(receipt.payload.keys())


def test_mismatched_channel_raises() -> None:
    adapter = MockChannelAdapter("voice")
    with pytest.raises(ValueError):
        adapter.send(_action(channel="sms"))


def test_unsupported_channel_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        MockChannelAdapter("carrier_pigeon")


def test_no_network_modules_imported() -> None:
    forbidden_names = {"socket", "requests", "httpx", "urllib", "http"}
    assert forbidden_names.isdisjoint(channel_module.__dict__.keys())
