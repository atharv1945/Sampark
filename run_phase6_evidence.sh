#!/usr/bin/env bash
set -euo pipefail

# Phase 6 evidence run -- mirrors run_phase4_ablations.sh's pattern
# (same registry-cleanup-between-runs necessity: sim/arm_b_cli.py
# registers deterministic per-seed agent keypairs under fixed global
# agent_ids, so two runs against the SAME live Postgres instance for
# the SAME seed collide on re-registration unless the registry is
# cleared between them).
#
# Runs BOTH Phase 6 ablations across all five precommitted seeds:
#   phase6_heuristic -- the regression proof + required paired heuristic ablation
#   phase6_model     -- sampark.models.scorer.build_scorer() against the
#                        committed artifact (falls back to the same
#                        heuristic on this dataset -- see
#                        sampark/models/artifact_data.py)
#
# Writes results/arm_b_phase6_heuristic_metrics_<seed>.json and
# results/arm_b_phase6_model_metrics_<seed>.json -- NEW filenames,
# never touching the committed Phase 4 arm_b_metrics_<seed>.json files.

SEEDS=(7 42 101 2024 31337)
ABLATIONS=(phase6_heuristic phase6_model)

cleanup_registry() {
    echo
    echo "Cleaning persistent registry and transactional state..."
    # Transactional tables (grant_requests/grants/contact_slot_claims/
    # customer_margin_windows/budget_windows/contact_states cache columns)
    # are normally reset per-run by sim/arm_b.py's _cleanup_postgres_run
    # (a `finally` block requiring a live connection). If a run dies
    # before that finally block completes -- e.g. the underlying Postgres
    # connection is dropped -- its rows are orphaned and, since
    # sim/arm_b_cli.py derives deterministic request_id UUIDs from
    # seed+agent+customer, a later rerun of the SAME seed collides on
    # primary key. Clearing them here (same tables/columns
    # _cleanup_postgres_run already documents as safe: never customers,
    # risk_items, agent identity rows, or contact_states' consent/fatigue
    # columns) makes this script's existing full-wipe-before-sweep
    # intent resilient to a crash mid-sweep, not just a clean rerun.
    docker compose exec -T postgres psql -U sampark -d sampark -c "
        DELETE FROM contact_slot_claims;
        DELETE FROM grants;
        DELETE FROM grant_requests;
        DELETE FROM capability_scopes;
        DELETE FROM agents;
        DELETE FROM customer_margin_windows;
        DELETE FROM budget_windows;
        UPDATE contact_states SET contacts_24h = 0, contacts_7d = 0, last_contact_at = NULL;
    "
    echo "Registry cleaned."
}

echo "============================================================"
echo "SAMPARK PHASE 6 EVIDENCE RUN"
echo "============================================================"

cleanup_registry

for ablation in "${ABLATIONS[@]}"; do
    for seed in "${SEEDS[@]}"; do
        echo
        echo "============================================================"
        echo "START: seed=${seed}, ablation=${ablation}"
        echo "============================================================"

        python -m sim.arm_b_cli --seed "${seed}" --ablation "${ablation}"

        echo "Completed: seed=${seed}, ablation=${ablation}"
        cleanup_registry
    done
done

echo
echo "============================================================"
echo "ALL PHASE 6 EVIDENCE RUNS COMPLETE"
echo "============================================================"
echo
echo "Result files:"
ls -lh results/arm_b_phase6_heuristic_metrics_*.json results/arm_b_phase6_model_metrics_*.json 2>/dev/null || true
