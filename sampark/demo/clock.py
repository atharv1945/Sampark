"""Simulated clock + time-compression accounting — spec §12.1.

Spec §12.1 requires two things of demo time, and they pull in opposite
directions unless kept strictly separate:

    "A month of 20k events is unwatchable. Run a seeded replay of the
     interesting slice in ~40 seconds with a visible `1 sim-hour ~ 0.4s`
     badge. If you insert a delay so a step is legible, label that too.
     Unlabelled time manipulation, if noticed, costs more than the demo
     gained."

    "Deterministic. Same seed, same trace, every run."

This module keeps SIMULATED time and WALL-CLOCK time in two separate
compartments and never lets one leak into the other:

  * SIMULATED time is what the system decides on. Every instant handed to
    `mediate_window`, `issue_grant`, `execute_grant`, an audit event's
    `occurred_at` — all of it — is a real `datetime` derived from the
    scenario's own window range. It is NOT a mock, NOT a patched
    `datetime.now`, and nothing here reads a wall clock on the decision
    path. That is what preserves
    `tests/allocator/test_structural_boundaries.py::
    test_no_wall_clock_or_random_calls_on_the_decision_path` and what makes
    the replay deterministic: two runs at one seed decide over the exact
    same instants.

  * WALL-CLOCK time is presentation only. `wall_delay_for_window()` is how
    long to pause between windows so a human can follow along. Changing it
    cannot change a single decision, a reason code, an event id, or the
    event ORDER — only how fast the already-decided sequence is revealed.

`compression_ratio_s_per_sim_hour` is COMPUTED from the scenario's actual
span, never hard-coded. Spec §12.1's "0.4s" is illustrative; printing 0.4
while the real figure is something else would be precisely the unlabelled
time manipulation the same paragraph forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sampark.allocator.constants import IST, QUIET_HOURS_END_HOUR, QUIET_HOURS_START_HOUR
from sampark.budget.windows import window_start_for

# The replay's wall-clock budget, spec §12.1's "~40 seconds".
DEFAULT_WALL_SECONDS_BUDGET = 40.0

# Extra wall-clock pause inserted after a window that produced a
# "headline" event (a rollback, a strike, a revocation, a degradation) so a
# viewer can actually read it. SURFACED SEPARATELY in the API response and
# rendered as its own badge — never folded into the compression ratio.
DEFAULT_LEGIBILITY_PAUSE_SECONDS = 1.2

# One window's sendable band: 09:00 -> 21:00 IST (spec §12.3 / TCCCPR).
SENDABLE_HOURS_PER_WINDOW = QUIET_HOURS_START_HOUR - QUIET_HOURS_END_HOUR


@dataclass(frozen=True)
class DemoClock:
    """Deterministic simulated-time source for one demo run.

    Frozen: a run's time model is fixed the moment the scenario is chosen.
    Nothing mutates it mid-run, so no chaos control and no reconnecting SSE
    client can perturb the instants the system already decided on.
    """

    first_window: date
    last_window: date
    wall_seconds_budget: float = DEFAULT_WALL_SECONDS_BUDGET
    legibility_pause_seconds: float = DEFAULT_LEGIBILITY_PAUSE_SECONDS

    @property
    def window_count(self) -> int:
        return (self.last_window - self.first_window).days + 1

    @property
    def total_sim_hours(self) -> float:
        """Sendable sim-hours covered by the replay. Quiet hours are not
        counted: nothing is decided during them (they are precisely when
        the system defers), so charging the compression ratio for them
        would understate how fast the visible part is actually running."""
        return float(self.window_count * SENDABLE_HOURS_PER_WINDOW)

    @property
    def compression_ratio_s_per_sim_hour(self) -> float:
        """Wall seconds per simulated hour. This is the number the badge
        shows. Computed, never asserted."""
        return self.wall_seconds_budget / self.total_sim_hours

    def badge_text(self) -> str:
        return (
            "SIMULATION - accelerated time: 1 sim-hour ~ "
            + format(self.compression_ratio_s_per_sim_hour, ".2f")
            + "s"
        )

    def wall_delay_for_window(self) -> float:
        """Wall-clock pause between consecutive windows. Presentation only."""
        return self.wall_seconds_budget / max(self.window_count, 1)

    # --- simulated instants (the only thing the SYSTEM ever sees) ---

    def decision_at(self, window: date) -> datetime:
        """09:00 IST on `window` — the first sendable instant, identical to
        what `sim/arm_b.py`'s window loop already uses as `decision_at`."""
        return window_start_for(window)

    def windows(self) -> tuple[date, ...]:
        return tuple(
            self.first_window + timedelta(days=offset) for offset in range(self.window_count)
        )

    def quiet_hour_instant(self, window: date, hour: int = 23, minute: int = 15) -> datetime:
        """An instant INSIDE the TCCCPR blackout on `window`, for chaos
        control 3 ("Set clock to 21:40") and the rogue's 23:15 request.

        This is a normal, explicit simulated instant handed to the system as
        a request's `proposed_send_after` — exactly like every other instant
        in this codebase. It is NOT a patched system clock:
        `sampark.policy.hard.quiet_hours.evaluate` is a pure function of the
        instant it is given, so setting the instant IS the mechanism. Nothing
        is monkeypatched, and the structural no-wall-clock test stays green.
        """
        return datetime(window.year, window.month, window.day, hour, minute, tzinfo=IST)
