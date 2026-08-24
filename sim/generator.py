"""Stage B — raw risk signals, spec §11: "~20,000 risk items across four
sources."

Each signal is sampled against a `Person` from Stage A (sim/population.py)
with replacement, so a person can (and, at this scale, routinely does)
appear in more than one source — that is what makes identity resolution a
real problem to solve rather than a pass-through. The signal's own contact
fields are independently reformatted per occurrence (spacing, a leading
`+91`, a leading trunk `0`) to mimic different upstream systems recording
the same underlying number differently; canonicalization
(sampark/identity/resolution.py) is what recovers the shared identity
despite that.

`context_code` is the raw, source-specific failure/abandonment context fed
into the root-cause YAML lookup (sampark/rootcause) — never the taxonomy
value itself. A small fraction of signals deliberately draw a code absent
from the YAML, to exercise the `unknown` fallback at real data scale, not
just in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

from sim.population import Population

N_SIGNALS = 20_000
MONTH_START = datetime(2025, 9, 1, tzinfo=timezone.utc)
MONTH_LENGTH_DAYS = 30

SOURCES = ("failed_payment", "abandoned_checkout", "mandate_failure", "overdue_invoice")

_UNMAPPED_CONTEXT_CODE = "UNMAPPED_CONTEXT"
_UNMAPPED_CONTEXT_PROB = 0.03

_CONTEXT_CODES_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "failed_payment": (
        "BAD_REQUEST_ERROR",
        "GATEWAY_ERROR",
        "SERVER_ERROR",
        "INSUFFICIENT_FUNDS",
        "AUTHENTICATION_FAILED",
    ),
    "mandate_failure": (
        "INSUFFICIENT_FUNDS",
        "MANDATE_EXPIRED",
        "AUTH_DECLINED",
        "ISSUER_DOWN",
    ),
    "abandoned_checkout": (
        "PRICE_SHOWN_HIGH",
        "CART_IDLE_TIMEOUT",
        "PAYMENT_PAGE_ABANDONED",
    ),
    "overdue_invoice": (
        "DISPUTE_RAISED",
        "NO_RESPONSE",
        "PARTIAL_PAYMENT_STALLED",
    ),
}

# Amount distribution parameters (lognormal, in rupees) per source — not
# spec-mandated, chosen to give each source a plausible, distinct shape.
_AMOUNT_LOGNORMAL_PARAMS: dict[str, tuple[float, float]] = {
    "failed_payment": (7.3, 0.8),      # median ~ Rs 1,480
    "abandoned_checkout": (7.6, 0.9),  # median ~ Rs 2,000
    "mandate_failure": (7.9, 0.6),     # median ~ Rs 2,700
    "overdue_invoice": (8.5, 0.9),     # median ~ Rs 4,900
}
_AMOUNT_MIN_PAISE = 5_000       # Rs 50
_AMOUNT_MAX_PAISE = 10_000_000  # Rs 1,00,000


@dataclass(frozen=True)
class RawSignal:
    signal_id: str
    person_id: str
    source: str
    context_code: str
    amount_paise: int
    detected_at: datetime
    raw_phone: str | None
    raw_email: str | None


def _reformat_phone(rng: np.random.Generator, digits: str) -> str:
    style = rng.integers(0, 4)
    if style == 0:
        return digits
    if style == 1:
        return f"+91 {digits[:5]} {digits[5:]}"
    if style == 2:
        return f"0{digits}"
    return f"{digits[:5]}-{digits[5:]}"


def _reformat_email(rng: np.random.Generator, email: str) -> str:
    return email.upper() if rng.random() < 0.1 else email


def _draw_context_code(rng: np.random.Generator, source: str) -> str:
    if rng.random() < _UNMAPPED_CONTEXT_PROB:
        return _UNMAPPED_CONTEXT_CODE
    codes = _CONTEXT_CODES_BY_SOURCE[source]
    return str(rng.choice(codes))


def _draw_amount_paise(rng: np.random.Generator, source: str) -> int:
    mu, sigma = _AMOUNT_LOGNORMAL_PARAMS[source]
    rupees = rng.lognormal(mean=mu, sigma=sigma)
    paise = int(round(rupees * 100))
    return int(np.clip(paise, _AMOUNT_MIN_PAISE, _AMOUNT_MAX_PAISE))


def _draw_detected_at(rng: np.random.Generator) -> datetime:
    offset_seconds = rng.uniform(0, MONTH_LENGTH_DAYS * 24 * 3600)
    return MONTH_START + timedelta(seconds=float(offset_seconds))


def generate_signals(
    population: Population, rng: np.random.Generator, seed: int
) -> tuple[RawSignal, ...]:
    """`seed` is the top-level generation seed (sim.cli's --seed), not the
    spawned per-stage RNG's internal state — it has no other way to reach
    this function. It is embedded directly in signal_id so that two
    different seeds can never produce the same id for the same position,
    while the same seed always reproduces the same id for the same
    position (spec §18.1's reproducibility requirement, and the seed-
    scoping fix that requirement turned out to demand)."""
    people = population.people
    n_people = len(people)

    person_indices = rng.integers(0, n_people, size=N_SIGNALS)
    source_indices = rng.integers(0, len(SOURCES), size=N_SIGNALS)

    signals: list[RawSignal] = []
    for i in range(N_SIGNALS):
        person = people[int(person_indices[i])]
        source = SOURCES[int(source_indices[i])]

        raw_phone = (
            _reformat_phone(rng, person.raw_phone) if person.raw_phone is not None else None
        )
        raw_email = (
            _reformat_email(rng, person.raw_email) if person.raw_email is not None else None
        )

        signals.append(
            RawSignal(
                signal_id=f"risk-{seed}-{i:06d}",
                person_id=person.person_id,
                source=source,
                context_code=_draw_context_code(rng, source),
                amount_paise=_draw_amount_paise(rng, source),
                detected_at=_draw_detected_at(rng),
                raw_phone=raw_phone,
                raw_email=raw_email,
            )
        )

    return tuple(signals)
