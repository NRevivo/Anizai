#!/usr/bin/env bash
# ==========================================================
# Anizai — Create the GKE cluster with a single node pool
# (Phase C, Sprint C1.8)
# ==========================================================
# Why (Design Decision D2 — revised twice):
#   The cluster runs ONE node pool:
#
#     main-pool — e2-standard-8 × 1 — every workload. Manually
#                 scaled to 0 during data-collection breaks to cut
#                 compute spend (~$200/month at on-demand 24/7; $0
#                 while at zero nodes).
#
#   The second pool is gone — do not re-add it:
#     Phase C originally created polymarket-pool (e2-micro × 1,
#     always-on) so Polymarket's WebSocket would survive main-pool
#     scaling to zero, on the reasoning that push-only price data
#     cannot be backfilled. That rationale is OBSOLETE, and the pool
#     was deleted on purpose in Phase 9.5-A/F0.
#
#     Why it failed: Kafka only exists while main-pool is up, so with
#     main-pool at zero the Polymarket pod had a live WebSocket and
#     nowhere to write. It crash-looped on NoBrokersAvailable roughly
#     14 h/day, undetected, for days. An always-on pool bought no data
#     and cost real money — the data gap it was meant to prevent
#     happened anyway, with a crash-loop on top.
#
#     Polymarket now schedules on main-pool and stops with everything
#     else. producers/polymarket-deployment.yaml carries NO
#     nodeSelector, so re-creating a pool named polymarket-pool would
#     not even attract the pod to it.
#
#   Automatic schedule-based scaling (Cloud Scheduler toggling
#   main-pool size) exists but both jobs are PAUSED; Ron resizes
#   manually via:
#     gcloud container clusters resize anizai-cluster \
#       --node-pool=main-pool --zone=us-central1-a --num-nodes=0
#
#   Cluster type: GKE Standard (not Autopilot — Autopilot blocks
#   pod-level Workload Identity scoping needed for cross-project
#   Firestore in Sprint C5).
#
# When to (re-)run:
#   - Once at Sprint C1.
#   - Idempotent: skips cluster create if it exists; skips the
#     pool create if it exists; skips default-pool delete if it's
#     already gone.
#
# Runtime: ~7-10 minutes (cluster + 2 pool operations).
#
# Spec: cloud_deployment_implementation.md §C1.8 (D2)
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipehub}"
CLUSTER_NAME="${CLUSTER_NAME:-anizai-cluster}"
ZONE="${ZONE:-us-central1-a}"
RELEASE_CHANNEL="${RELEASE_CHANNEL:-regular}"

MAIN_POOL_NAME="${MAIN_POOL_NAME:-main-pool}"
MAIN_POOL_MACHINE="${MAIN_POOL_MACHINE:-e2-standard-8}"
MAIN_POOL_NODES="${MAIN_POOL_NODES:-1}"

# ----------------------------------------------------------
# Helper: returns 0 if a node pool exists in the cluster.
# ----------------------------------------------------------
pool_exists() {
  local POOL="$1"
  gcloud container node-pools describe "${POOL}" \
    --cluster="${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1
}

# ----------------------------------------------------------
# Step 1/3 — Create the cluster (with the GKE-default `default-pool`,
# 1 e2-standard-8 node). We will replace default-pool with a
# correctly-named main-pool in the next step. This dance is
# necessary because gcloud container clusters create does not
# expose a flag to name the initial pool in SDK 549.
# ----------------------------------------------------------
if gcloud container clusters describe "${CLUSTER_NAME}" \
      --zone "${ZONE}" \
      --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "  [SKIP] Cluster '${CLUSTER_NAME}' already exists in ${ZONE}."
else
  echo "=== Step 1/3: Creating GKE cluster '${CLUSTER_NAME}' in ${ZONE} ==="
  echo "    (initial default-pool will be replaced by ${MAIN_POOL_NAME})"
  echo "    Workload pool: ${PROJECT_ID}.svc.id.goog"
  echo "    This will take ~5-7 minutes."

  gcloud container clusters create "${CLUSTER_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --num-nodes=1 \
    --machine-type="${MAIN_POOL_MACHINE}" \
    --release-channel="${RELEASE_CHANNEL}" \
    --workload-pool="${PROJECT_ID}.svc.id.goog" \
    --addons=GcePersistentDiskCsiDriver \
    --no-enable-master-authorized-networks \
    --enable-ip-alias \
    --logging=SYSTEM,WORKLOAD \
    --monitoring=SYSTEM
fi

# ----------------------------------------------------------
# Step 2/3 — Add main-pool (e2-standard-8 × 1).
# ----------------------------------------------------------
if pool_exists "${MAIN_POOL_NAME}"; then
  echo "  [SKIP] Node pool '${MAIN_POOL_NAME}' already exists."
else
  echo "=== Step 2/3: Adding node pool '${MAIN_POOL_NAME}' (${MAIN_POOL_MACHINE} × ${MAIN_POOL_NODES}) ==="
  gcloud container node-pools create "${MAIN_POOL_NAME}" \
    --cluster="${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --num-nodes="${MAIN_POOL_NODES}" \
    --machine-type="${MAIN_POOL_MACHINE}"
fi

# ----------------------------------------------------------
# Step 3/3 — Delete the GKE-default `default-pool` so all general
# workloads naturally land on main-pool. (Workloads without a
# nodeSelector schedule to whichever non-tainted node has room;
# leaving default-pool around would split scheduling unpredictably.)
# ----------------------------------------------------------
if pool_exists "default-pool"; then
  echo "=== Step 3/3: Deleting transient default-pool ==="
  gcloud container node-pools delete default-pool \
    --cluster="${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --quiet
else
  echo "  [SKIP] default-pool already removed."
fi

echo "=== Verifying cluster + pools ==="
gcloud container clusters describe "${CLUSTER_NAME}" \
  --zone "${ZONE}" \
  --project "${PROJECT_ID}" \
  --format="value(name,status,workloadIdentityConfig.workloadPool)"

gcloud container node-pools list \
  --cluster="${CLUSTER_NAME}" \
  --zone="${ZONE}" \
  --project="${PROJECT_ID}" \
  --format="table(name,config.machineType,initialNodeCount,status)"

echo "=== Done. Next: run 05_kubectl_config.sh to point local kubectl at this cluster. ==="
echo
echo "Manual scale-down recipe (when not collecting data):"
echo "  gcloud container clusters resize ${CLUSTER_NAME} \\"
echo "    --node-pool=${MAIN_POOL_NAME} --zone=${ZONE} --num-nodes=0 --quiet"
echo "Bring back up with --num-nodes=1."
