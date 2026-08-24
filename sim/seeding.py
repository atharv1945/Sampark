"""Seeding — spec §7: "Simulator | NumPy/Pandas, seeded, deterministic."

Every generation stage gets its own independent substream via
`SeedSequence.spawn()`, keyed by stage name, so adding, removing, or
reordering a stage never perturbs the draws made by another stage. No
generator code may import Python's stdlib `random` or call an unseeded
`numpy.random.*` convenience function — every draw goes through a
`Generator` obtained from `make_rngs`.
"""

from __future__ import annotations

import numpy as np


def make_rngs(seed: int, stage_names: tuple[str, ...]) -> dict[str, np.random.Generator]:
    """One independent, seeded Generator per named stage.

    `stage_names` order determines which child SeedSequence each stage
    gets — callers must pass a fixed, literal tuple so the mapping is
    stable across runs.
    """
    seed_sequence = np.random.SeedSequence(seed)
    children = seed_sequence.spawn(len(stage_names))
    return {
        name: np.random.default_rng(child)
        for name, child in zip(stage_names, children)
    }
