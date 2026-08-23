"""Grant — CONTRACTS.md Part 1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sampark.contracts import Grant, GrantState


def _window() -> tuple[datetime, datetime]:
    send_after = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    expires_at = send_after + timedelta(minutes=15)
    return send_after, expires_at


def test_grant_valid_construction():
    send_after, expires_at = _window()
    grant = Grant(
        grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
        send_after=send_after, expires_at=expires_at, state=GrantState.RESERVED,
    )
    assert grant.state is GrantState.RESERVED


def test_grant_rejects_negative_incentive_ceiling_paise():
    send_after, expires_at = _window()
    with pytest.raises(ValidationError):
        Grant(
            grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=-1,
            send_after=send_after, expires_at=expires_at, state=GrantState.RESERVED,
        )


def test_grant_accepts_zero_incentive_ceiling_boundary():
    send_after, expires_at = _window()
    grant = Grant(
        grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
        send_after=send_after, expires_at=expires_at, state=GrantState.RESERVED,
    )
    assert grant.incentive_ceiling_paise == 0


def test_grant_rejects_send_after_equal_to_expires_at():
    when = datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        Grant(
            grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
            send_after=when, expires_at=when, state=GrantState.RESERVED,
        )


def test_grant_rejects_send_after_later_than_expires_at():
    send_after, expires_at = _window()
    with pytest.raises(ValidationError):
        Grant(
            grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
            send_after=expires_at, expires_at=send_after, state=GrantState.RESERVED,
        )


def test_grant_rejects_unapproved_state_value():
    send_after, expires_at = _window()
    with pytest.raises(ValidationError):
        Grant(
            grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
            send_after=send_after, expires_at=expires_at, state="CANCELLED",
        )


@pytest.mark.parametrize(
    "state",
    [
        GrantState.RESERVED,
        GrantState.EXECUTING,
        GrantState.CONFIRMED,
        GrantState.ROLLED_BACK,
        GrantState.EXPIRED,
    ],
)
def test_grant_accepts_every_approved_state_value(state):
    send_after, expires_at = _window()
    grant = Grant(
        grant_id=uuid4(), channel="whatsapp", incentive_ceiling_paise=0,
        send_after=send_after, expires_at=expires_at, state=state,
    )
    assert grant.state is state


def test_grant_has_no_request_id_field():
    send_after, expires_at = _window()
    with pytest.raises(ValidationError):
        Grant(
            grant_id=uuid4(), request_id=uuid4(), channel="whatsapp",
            incentive_ceiling_paise=0, send_after=send_after,
            expires_at=expires_at, state=GrantState.RESERVED,
        )
