"""SAMPARK Phase 4 allocator — budgeted greedy, Design Lock §8.

Receives only hard-policy survivors (sampark.policy.hard.evaluate_all).
No function here has a parameter or return value by which a hard denial
could be reversed — tests/allocator/test_structural_boundaries.py
asserts this module never imports sampark.policy.hard.
"""

from __future__ import annotations
