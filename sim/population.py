"""Stage A — synthetic population, spec §11.

"~5,000 customers ... including the hidden response process (conversion
propensity, fatigue hazard, price sensitivity) that the models are *not*
given."

`Person` carries the raw (pre-hash) contact info the simulator's four
signal sources will draw from. `HiddenResponseProfile` carries the ground-
truth response parameters — kept in a structure entirely separate from
anything that reaches the ledger, so nothing downstream of identity
resolution can see it. `person_id` is internal to the simulator only: it
never crosses into a ContactSignal, a Customer, or any persisted row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

N_PEOPLE = 5_000

_PHONE_PRESENCE_PROB = 0.95
_EMAIL_PRESENCE_PROB = 0.70


@dataclass(frozen=True)
class Person:
    person_id: str
    raw_phone: str | None
    raw_email: str | None


@dataclass(frozen=True)
class HiddenResponseProfile:
    person_id: str
    conversion_propensity: float
    fatigue_hazard: float
    price_sensitivity: float


@dataclass(frozen=True)
class Population:
    people: tuple[Person, ...]
    hidden_response: tuple[HiddenResponseProfile, ...]


def _synthetic_phone(rng: np.random.Generator) -> str:
    # Indian mobile numbers: 10 digits, first digit 6-9.
    first = rng.integers(6, 10)
    rest = "".join(str(d) for d in rng.integers(0, 10, size=9))
    return f"{first}{rest}"


def _synthetic_email(rng: np.random.Generator, person_id: str) -> str:
    domain = rng.choice(["example.com", "mail.test", "demo.in"])
    return f"{person_id}@{domain}"


def generate_population(rng: np.random.Generator) -> Population:
    people: list[Person] = []
    hidden: list[HiddenResponseProfile] = []

    has_phone = rng.random(N_PEOPLE) < _PHONE_PRESENCE_PROB
    has_email = rng.random(N_PEOPLE) < _EMAIL_PRESENCE_PROB
    conversion_propensity = rng.beta(2.0, 5.0, size=N_PEOPLE)
    fatigue_hazard = rng.beta(2.0, 8.0, size=N_PEOPLE)
    price_sensitivity = rng.beta(2.0, 2.0, size=N_PEOPLE)

    for i in range(N_PEOPLE):
        person_id = f"person-{i:05d}"
        # Every person needs at least one contact channel to be reachable
        # by any signal source at all.
        phone_present = bool(has_phone[i]) or not bool(has_email[i])
        raw_phone = _synthetic_phone(rng) if phone_present else None
        raw_email = _synthetic_email(rng, person_id) if bool(has_email[i]) else None

        people.append(Person(person_id=person_id, raw_phone=raw_phone, raw_email=raw_email))
        hidden.append(
            HiddenResponseProfile(
                person_id=person_id,
                conversion_propensity=float(conversion_propensity[i]),
                fatigue_hazard=float(fatigue_hazard[i]),
                price_sensitivity=float(price_sensitivity[i]),
            )
        )

    return Population(people=tuple(people), hidden_response=tuple(hidden))
