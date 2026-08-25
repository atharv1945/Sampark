"""SAMPARK Phase 4 hard/soft policy split — Design Lock §4, CLAUDE.md §6.

`sampark.policy.hard` — filters. Every rule eliminates a candidate
BEFORE scoring, or reports it cannot evaluate at all
(FACT_UNAVAILABLE). No rule in this package computes or reads
expected_net; that structural separation is enforced by
tests/allocator/test_structural_boundaries.py.

`sampark.policy.soft` — scoring terms only. Nothing in this package can
veto a candidate; it has no HardVerdict-shaped return type.
"""

from __future__ import annotations
