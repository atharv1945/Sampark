"""Phase 7 model training entry point (spec §8.9).

    python -m sim.train_phase7_models --seed 42 --fraction 0.10

Calls `sampark.models.artifact.build_model_artifact_holdout(seed, fraction)`
— the Phase 7 holdout-aware path — and writes the result as a GENERATED
FILE, `sampark/models/artifact_data_phase7.py`, mirroring
`sim/train_phase6_models.py`'s exact rendering convention. This is a
SEPARATE file from `sampark/models/artifact_data.py` (Phase 6, untouched)
— `phase6_model`'s committed evidence stays reproducible forever
regardless of what Phase 7 finds.

Run this BEFORE any `phase7_model`/`phase7_model_uplift` evidence run,
exactly as `sim/train_phase6_models.py` must run before `phase6_model` —
the committed artifact's git commit must predate the evidence run's own
`constants_commit_sha` for the precommitment device to mean anything.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sampark.models.artifact import ModelArtifact, build_model_artifact_holdout

_ARTIFACT_MODULE_PATH = Path(__file__).resolve().parent.parent / "sampark" / "models" / "artifact_data_phase7.py"


def _render_uplift_tables(artifact: ModelArtifact) -> str:
    if not artifact.uplift_available or artifact.uplift_model is None:
        return "UPLIFT_TREATED_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {}\nUPLIFT_CONTROL_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {}\n"
    treated = ",\n".join(
        f'    ("{s}", "{r}"): {v!r}' for (s, r), v in sorted(artifact.uplift_model.treated_response_by_bucket.items())
    )
    control = ",\n".join(
        f'    ("{s}", "{r}"): {v!r}' for (s, r), v in sorted(artifact.uplift_model.control_response_by_bucket.items())
    )
    return (
        f"UPLIFT_TREATED_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {{\n{treated}\n}}\n"
        f"UPLIFT_CONTROL_RESPONSE_BY_BUCKET: dict[tuple[str, str], float] = {{\n{control}\n}}\n"
    )


def _render_fatigue_table(artifact: ModelArtifact) -> str:
    if not artifact.fatigue_hazard_available or artifact.fatigue_hazard_model is None:
        return "FATIGUE_HAZARD_BY_BUCKET: dict[tuple[str, str, int], float] = {}\n"
    lines = ",\n".join(
        f'    ("{s}", "{r}", {n!r}): {v!r}'
        for (s, r, n), v in sorted(artifact.fatigue_hazard_model.hazard_by_bucket.items())
    )
    return f"FATIGUE_HAZARD_BY_BUCKET: dict[tuple[str, str, int], float] = {{\n{lines}\n}}\n"


def render_artifact_module(artifact: ModelArtifact, fraction: float) -> str:
    return f'''"""GENERATED FILE — DO NOT EDIT BY HAND.

Produced by sim/train_phase7_models.py::main() from Arm A-H's outcome
log (sim.arm_a_holdout.run_arm_a_holdout) at seed {artifact.seed}, holdout
fraction {fraction!r} only. Re-run `python -m sim.train_phase7_models
--seed {artifact.seed} --fraction {fraction!r}` to regenerate; do not
hand-edit these values.

A SEPARATE artifact from sampark/models/artifact_data.py (Phase 6) —
phase6_model's committed evidence is untouched by this file's existence
or content.

Deterministic: sampark.models.uplift.train_uplift_model_holdout and
sampark.models.fatigue_hazard.train_fatigue_hazard_model_holdout both
run structural, non-random data-adequacy checks before fitting
anything, so re-running this script against the same (seed, fraction)
always reproduces this exact file.
"""

from __future__ import annotations

TRAINING_SEED: int = {artifact.seed!r}
HOLDOUT_FRACTION: float = {fraction!r}

UPLIFT_AVAILABLE: bool = {artifact.uplift_available!r}
UPLIFT_UNAVAILABLE_REASON: str | None = {artifact.uplift_reason!r}

FATIGUE_HAZARD_AVAILABLE: bool = {artifact.fatigue_hazard_available!r}
FATIGUE_HAZARD_UNAVAILABLE_REASON: str | None = {artifact.fatigue_hazard_reason!r}

{_render_uplift_tables(artifact)}
{_render_fatigue_table(artifact)}
'''


def main() -> None:
    parser = argparse.ArgumentParser(description="SAMPARK Phase 7 model training entry point")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fraction", type=float, default=0.10)
    args = parser.parse_args()

    artifact = build_model_artifact_holdout(args.seed, args.fraction)
    module_source = render_artifact_module(artifact, args.fraction)
    _ARTIFACT_MODULE_PATH.write_text(module_source, encoding="utf-8")

    print(f"seed: {artifact.seed}")
    print(f"fraction: {args.fraction}")
    print(f"uplift_available: {artifact.uplift_available}")
    if not artifact.uplift_available:
        print(f"uplift_unavailable_reason: {artifact.uplift_reason}")
    print(f"fatigue_hazard_available: {artifact.fatigue_hazard_available}")
    if not artifact.fatigue_hazard_available:
        print(f"fatigue_hazard_unavailable_reason: {artifact.fatigue_hazard_reason}")
    print(f"wrote: {_ARTIFACT_MODULE_PATH}")


if __name__ == "__main__":
    main()
