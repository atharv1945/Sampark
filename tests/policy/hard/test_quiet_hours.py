from __future__ import annotations

import datetime as dt

from sampark.allocator.constants import IST
from sampark.allocator.reason_codes import QUIET_HOURS
from sampark.policy.hard import quiet_hours
from sampark.policy.types import Verdict


def _ist(y, m, d, h, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=IST)


def test_09_00_is_admissible(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 9, 0))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_20_59_is_admissible(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 20, 59))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.ADMISSIBLE


def test_21_00_is_deferred_to_next_day_09_00(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 21, 0))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.reason_code == QUIET_HOURS
    assert verdict.is_defer
    assert verdict.next_eligible_at == _ist(2025, 9, 11, 9, 0)


def test_08_59_is_deferred_to_same_day_09_00(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 8, 59))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.verdict is Verdict.INADMISSIBLE
    assert verdict.is_defer
    assert verdict.next_eligible_at == _ist(2025, 9, 10, 9, 0)


def test_midnight_is_deferred_to_same_day_09_00(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 0, 0))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.is_defer
    assert verdict.next_eligible_at == _ist(2025, 9, 10, 9, 0)


def test_2359_is_deferred_to_next_day_09_00(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 23, 59))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.is_defer
    assert verdict.next_eligible_at == _ist(2025, 9, 11, 9, 0)


def test_quiet_hours_never_fact_unavailable(make_candidate, policy_context):
    candidate = make_candidate(proposed_send_after=_ist(2025, 9, 10, 3, 0))
    verdict = quiet_hours.evaluate(candidate, policy_context())
    assert verdict.verdict is not Verdict.FACT_UNAVAILABLE
