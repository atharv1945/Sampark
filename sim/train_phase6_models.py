"""Phase 6 model training entry point.

    python -m sim.train_phase6_models

Calls `sampark.models.artifact.build_model_artifact(seed=42)` — same
seed-42-only rule as Design Lock §14.1's Phase 4 calibration ("Calibrate
on seed 42 only, and use that one table for all five gate seeds") — and
writes the result as a GENERATED FILE,
`sampark/models/artifact_data.py`, following
`sim/calibration.py::render_calibrated_module`'s exact convention:
deterministic formatting, sorted keys, never hand-edited.

Run this BEFORE any Phase 6 evidence run (`sim/arm_b_cli.py --ablation
phase6_model`) — the committed artifact's git commit must predate the
evidence run's own `constants_commit_sha` for Design Lock §13.4's
precommitment device to mean anything for Phase 6, exactly as it does
for Phase 4.
"""

from __future__ import annotations

from pathlib import Path

from sampark.models.artifact import ModelArtifact, build_model_artifact

TRAINING_SEED = 42

_ARTIFACT_MODULE_PATH = Path(__file__).resolve().parent.parent / "sampark" / "models" / "artifact_data.py"


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


def render_artifact_module(artifact: ModelArtifact) -> str:
    return f'''"""GENERATED FILE — DO NOT EDIT BY HAND.

Produced by sim/train_phase6_models.py::main() from Arm A's outcome log
at seed {artifact.seed} only (mirrors Design Lock section 14.1's Phase 4
calibration rule). Re-run `python -m sim.train_phase6_models` to
regenerate; do not hand-edit these values.

Deterministic: sampark.models.uplift.train_uplift_model and
sampark.models.fatigue_hazard.train_fatigue_hazard_model both run a
structural, non-random data-adequacy check before fitting anything, so
re-running this script against the same seed always reproduces this
exact file.
"""

from __future__ import annotations

TRAINING_SEED: int = {artifact.seed!r}

UPLIFT_AVAILABLE: bool = {artifact.uplift_available!r}
UPLIFT_UNAVAILABLE_REASON: str | None = {artifact.uplift_reason!r}

FATIGUE_HAZARD_AVAILABLE: bool = {artifact.fatigue_hazard_available!r}
FATIGUE_HAZARD_UNAVAILABLE_REASON: str | None = {artifact.fatigue_hazard_reason!r}

{_render_uplift_tables(artifact)}
{_render_fatigue_table(artifact)}
'''


def main() -> None:
    artifact = build_model_artifact(TRAINING_SEED)
    module_source = render_artifact_module(artifact)
    _ARTIFACT_MODULE_PATH.write_text(module_source, encoding="utf-8")

    print(f"seed: {artifact.seed}")
    print(f"uplift_available: {artifact.uplift_available}")
    if not artifact.uplift_available:
        print(f"uplift_unavailable_reason: {artifact.uplift_reason}")
    print(f"fatigue_hazard_available: {artifact.fatigue_hazard_available}")
    if not artifact.fatigue_hazard_available:
        print(f"fatigue_hazard_unavailable_reason: {artifact.fatigue_hazard_reason}")
    print(f"wrote: {_ARTIFACT_MODULE_PATH}")


if __name__ == "__main__":
    main()
