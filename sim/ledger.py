"""Ledger assembly — normaliser + identity resolution, spec §8.2.

Ties Stage A (sim/population.py) and Stage B (sim/generator.py) together:
hashes each signal's raw contact info, resolves it to a customer_id via
sampark.identity (hash-only, no person_id ever crosses this boundary),
classifies its root cause via sampark.rootcause (deterministic YAML), and
emits the three approved ledger contracts from CONTRACTS.md: Customer,
ContactState, RiskItem.

The hidden response process (sim.population.Population.hidden_response) is
NOT touched here and never enters a Ledger — it is generated and returned
separately by the caller (sim/cli.py) and must never be joined back onto
anything a model or allocator can read (spec §11).
"""

from __future__ import annotations

from dataclasses import dataclass

from sampark.contracts import ContactState, Customer, RiskItem
from sampark.identity import ContactSignal, email_hash, phone_hash, resolve_customer_ids
from sampark.rootcause import classify
from sim.generator import RawSignal


@dataclass(frozen=True)
class Ledger:
    customers: tuple[Customer, ...]
    contact_states: dict[str, ContactState]  # keyed by customer_id
    risk_items: tuple[RiskItem, ...]
    risk_customer_map: dict[str, str]  # risk_id -> customer_id


def build_ledger(signals: tuple[RawSignal, ...]) -> Ledger:
    contact_signals = [
        ContactSignal(
            signal_id=s.signal_id,
            phone_hash=phone_hash(s.raw_phone),
            email_hash=email_hash(s.raw_email),
        )
        for s in signals
    ]
    customer_id_by_signal = resolve_customer_ids(contact_signals)

    # Group signal indices by resolved customer_id, in generation order, so
    # "first non-null hash in the group" is itself a deterministic choice.
    indices_by_customer: dict[str, list[int]] = {}
    for idx, s in enumerate(signals):
        cust_id = customer_id_by_signal[s.signal_id]
        indices_by_customer.setdefault(cust_id, []).append(idx)

    customers: list[Customer] = []
    contact_states: dict[str, ContactState] = {}
    for cust_id, indices in indices_by_customer.items():
        group_phone_hash = next(
            (phone_hash(signals[i].raw_phone) for i in indices if signals[i].raw_phone),
            None,
        )
        group_email_hash = next(
            (email_hash(signals[i].raw_email) for i in indices if signals[i].raw_email),
            None,
        )
        customers.append(
            Customer(customer_id=cust_id, phone_hash=group_phone_hash, email_hash=group_email_hash)
        )
        contact_states[cust_id] = ContactState(
            contacts_24h=0,
            contacts_7d=0,
            last_contact_at=None,
            optouts_by_channel={},
            consent_scopes={},
            fatigue_score=0.0,
        )

    risk_items: list[RiskItem] = []
    risk_customer_map: dict[str, str] = {}
    for s in signals:
        risk_items.append(
            RiskItem(
                risk_id=s.signal_id,
                source=s.source,
                amount_paise=s.amount_paise,
                root_cause=classify(s.source, s.context_code),
                detected_at=s.detected_at,
            )
        )
        risk_customer_map[s.signal_id] = customer_id_by_signal[s.signal_id]

    customers.sort(key=lambda c: c.customer_id)  # stable, deterministic output order

    return Ledger(
        customers=tuple(customers),
        contact_states=contact_states,
        risk_items=tuple(risk_items),
        risk_customer_map=risk_customer_map,
    )
