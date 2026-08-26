#!/usr/bin/env bash
set -euo pipefail

SEEDS=(42 101 2024 31337)
ABLATION="fifo_under_cap"

cleanup_registry() {
    echo
    echo "Cleaning persistent registry state..."

    docker compose exec -T postgres psql -U sampark -d sampark -c "
        DELETE FROM capability_scopes;
        DELETE FROM agents;
    "

    echo "Registry cleaned."
}

echo "============================================================"
echo "SAMPARK PHASE 4 — FIFO REMAINING RUNS"
echo "============================================================"
echo "Seed 7 is already complete and will NOT be rerun."
echo

# Clean registry before starting the remaining runs.
cleanup_registry

for seed in "${SEEDS[@]}"; do
    echo
    echo "============================================================"
    echo "START: seed=${seed}, ablation=${ABLATION}"
    echo "============================================================"

    python -m sim.arm_b_cli \
        --seed "${seed}" \
        --ablation "${ABLATION}"

    echo
    echo "Completed: seed=${seed}, ablation=${ABLATION}"

    cleanup_registry

    echo "============================================================"
    echo "READY FOR NEXT SEED"
    echo "============================================================"
done

echo
echo "============================================================"
echo "ALL REMAINING FIFO RUNS COMPLETED"
echo "============================================================"

echo
echo "FIFO result files:"
ls -lh results/arm_b_fifo_under_cap_metrics_*.json 2>/dev/null || true