#!/usr/bin/env bash
# ==========================================================
# Anizai — Create the GKE cluster with dual node pools
# (Phase C, Sprint C1.8)
# ==========================================================
# Why (Design Decision D2 — revised):
#   The cluster runs TWO separately-scalable node pools:
#
#     main-pool       — e2-standard-8 × 1 — all workloads except
#                       Polymarket. Manually scaled to 0 during data
#                       collection breaks to cut compute spend.
#     polymarket-pool — e2-micro × 1 — Polymarket producer only,
#                       always-on 24/7.
#
#   Rationale:
#     Polymarket emits real-time price changes via WebSocket. Gaps
#     in the WebSocket connection cannot be backfilled (the protocol
#     is push-only, no historical replay). Every other producer in
#     the pipeline either supports backfill (Telegram via MTProto
#     channel-history catch-up; FRED/NewsAPI/etc. via scheduled DAG
#     re-runs) or is itself a scheduled poller. Polymarket alone
#     must stay connected continuously to avoid permanent data
#     loss, so it gets its own tiny dedicated pool that survives
#     when main-pool is scaled to zero.
#
#     Cost shape:
#       main-pool       ~$200/month at on-demand pricing (24/7).
#                       Scaled to 0 between collection windows: $0.
#       polymarket-pool ~$7/month (always-on, fits 1 GB / 2 vCPU).
#
#   Automatic schedule-based scaling (Cloud Scheduler + Cloud Run
#   trigger that toggles main-pool size) is deferred until after
#   Sprint C5. V1 is manual via:
#     gcloud container clusters resize anizai-cluster \
#       --node-pool=main-pool --zone=us-central1-a --num-nodes=0
#
#   Cluster type: GKE Standard (not Autopilot — Autopilot blocks
#   pod-level Workload Identity scoping needed for cross-project
#   Firestore in Sprint C5).
#
# When to (re-)run:
#   - Once at Sprint C1.
#   - Idempotent: skips cluster create if it exists; skips each
#     pool create if it exists; skips default-pool delete if it's
#     already gone.
#
# Runtime: ~7-10 minutes (cluster + 3 pool operations).
#
# Spec: cloud_deployment_implementation.md §C1.8 (D2)
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipeline}"
CLUSTER_NAME="${CLUSTER_NAME:-anizai-cluster}"
ZONE="${ZONE:-us-central1-a}"
RELEASE_CHANNEL="${RELEASE_CHANNEL:-regular}"

MAIN_POOL_NAME="${MAIN_POOL_NAME:-main-pool}"
MAIN_POOL_MACHINE="${MAIN_POOL_MACHINE:-e2-standard-8}"
MAIN_POOL_NODES="${MAIN_POOL_NODES:-1}"

POLYMARKET_POOL_NAME="${POLYMARKET_POOL_NAME:-polymarket-pool}"
POLYMARKET_POOL_MACHINE="${POLYMARKET_POOL_MACHINE:-e2-micro}"
POLYMARKET_POOL_NODES="${POLYMARKET_POOL_NODES:-1}"

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
# Step 1 — Create the cluster (with the GKE-default `default-pool`,
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
  echo "=== Step 1/4: Creating GKE cluster '${CLUSTER_NAME}' in ${ZONE} ==="
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
# Step 2 — Add main-pool (e2-standard-8 × 1).
# ----------------------------------------------------------
if pool_exists "${MAIN_POOL_NAME}"; then
  echo "  [SKIP] Node pool '${MAIN_POOL_NAME}' already exists."
else
  echo "=== Step 2/4: Adding node pool '${MAIN_POOL_NAME}' (${MAIN_POOL_MACHINE} × ${MAIN_POOL_NODES}) ==="
  gcloud container node-pools create "${MAIN_POOL_NAME}" \
    --cluster="${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --num-nodes="${MAIN_POOL_NODES}" \
    --machine-type="${MAIN_POOL_MACHINE}"
fi

# ----------------------------------------------------------
# Step 3 — Delete the GKE-default `default-pool` so all general
# workloads naturally land on main-pool. (Workloads without a
# nodeSelector schedule to whichever non-tainted node has room;
# leaving default-pool around would split scheduling unpredictably.)
# ----------------------------------------------------------
if pool_exists "default-pool"; then
  echo "=== Step 3/4: Deleting transient default-pool ==="
  gcloud container node-pools delete default-pool \
    --cluster="${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --quiet
else
  echo "  [SKIP] default-pool already removed."
fi

# ----------------------------------------------------------
# Step 4 — Add polymarket-pool (e2-micro × 1).
# Always-on. Polymarket pod will pin to this pool via
# nodeSelector: cloud.google.com/gke-nodepool=polymarket-pool
# (GKE auto-applies that label to every node in the pool).
# ----------------------------------------------------------
if pool_exists "${POLYMARKET_POOL_NAME}"; then
  echo "  [SKIP] Node pool '${POLYMARKET_POOL_NAME}' already exists."
else
  echo "=== Step 4/4: Adding node pool '${POLYMARKET_POOL_NAME}' (${POLYMARKET_POOL_MACHINE} × ${POLYMARKET_POOL_NODES}) ==="
  gcloud container node-pools create "${POLYMARKET_POOL_NAME}" \
    --cluster="${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}" \
    --num-nodes="${POLYMARKET_POOL_NODES}" \
    --machine-type="${POLYMARKET_POOL_MACHINE}"
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
echo "polymarket-pool is intentionally NOT included in scale-down — leave it running."
