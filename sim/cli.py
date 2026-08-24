"""SAMPARK Phase 1 data-spine generator entry point.

    python -m sim.cli --seed 42
    python -m sim.cli --seed 42 --skip-postgres

Generates the seeded synthetic dataset (population -> signals -> ledger)
and, unless --skip-postgres is given, loads it into the running
PostgreSQL instance (docker-compose.yml, schema applied separately) via
sim/persistence.py.

Nothing here writes a dataset file to disk. The generator is committed;
its output is not (Phase 1 decision) — running this file is how the
20,000 risk items get (re)produced, not a checked-in artifact.
"""

from __future__ import annotations

import argparse

import psycopg

from sim.generator import generate_signals
from sim.ledger import Ledger, build_ledger
from sim.persistence import PostgresConfig, load_ledger
from sim.population import Population, generate_population
from sim.seeding import make_rngs

_STAGES = ("population", "signals")


def build_dataset(seed: int) -> tuple[Population, tuple, Ledger]:
    rngs = make_rngs(seed, _STAGES)
    population = generate_population(rngs["population"])
    signals = generate_signals(population, rngs["signals"], seed)
    ledger = build_ledger(signals)
    return population, signals, ledger


def _print_summary(population: Population, signals: tuple, ledger: Ledger) -> None:
    print(f"people: {len(population.people)}")
    print(f"signals: {len(signals)}")
    print(f"customers resolved: {len(ledger.customers)}")
    print(f"risk_items: {len(ledger.risk_items)}")

    counts_by_source: dict[str, int] = {}
    for s in signals:
        counts_by_source[s.source] = counts_by_source.get(s.source, 0) + 1
    for source in sorted(counts_by_source):
        print(f"  {source}: {counts_by_source[source]}")

    unknown_count = sum(1 for r in ledger.risk_items if r.root_cause == "unknown")
    total = len(ledger.risk_items)
    print(f"unknown root_cause: {unknown_count} ({unknown_count / total:.2%})")


def main() -> None:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 1 data-spine generator")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--skip-postgres", action="store_true")
    args = parser.parse_args()

    population, signals, ledger = build_dataset(args.seed)
    _print_summary(population, signals, ledger)

    if not args.skip_postgres:
        config = PostgresConfig.from_env()
        with psycopg.connect(config.conninfo()) as conn:
            load_ledger(conn, ledger)
        print(f"loaded into postgres: {config.dbname}@{config.host}:{config.port}")


if __name__ == "__main__":
    main()
