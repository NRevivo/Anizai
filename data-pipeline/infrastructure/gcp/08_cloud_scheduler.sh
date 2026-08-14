#!/usr/bin/env bash
# ==========================================================
# 08_cloud_scheduler.sh — Cloud Scheduler: main-pool scale up/down
# ==========================================================
# Creates two Cloud Scheduler HTTP jobs that resize the main-pool
# node pool on weekdays (Israel time, Asia/Jerusalem):
#
#   scale-up-main-pool   — Mon-Fri 08:00 IL (cron: 0 5 * * 1-5 UTC)
#   scale-down-main-pool — Mon-Fri 18:00 IL (cron: 0 15 * * 1-5 UTC)
#
# Both jobs call the Container API setSize endpoint with OAuth auth.
# A dedicated GSA (scheduler-scaler) is created with
# roles/container.nodePoolAdmin so it can resize but nothing else.
#
# Why a dedicated GSA (not pipeline-runtime):
#   Least-privilege — pipeline-runtime already has secretmanager,
#   storage, and datastore roles; adding container admin there widens
#   its blast radius unnecessarily.
#
# OAuth target audience:
#   https://container.googleapis.com/ — required for Google Cloud API
#   calls (OIDC is for Cloud Run/Functions; OAuth is for GCP REST APIs).
#
# Idempotent: safe to re-run. GSA creation is idempotent via || true.
# Scheduler job creation uses --quiet; update-or-create pattern.
#
# Prerequisites:
#   - cloudscheduler.googleapis.com enabled (added in 00_enable_apis.sh
#     or needs adding — check with gcloud services list)
#   - gcloud authenticated, project = anizai-pipehub
#
# Usage:
#   bash infrastructure/gcp/08_cloud_scheduler.sh
#
# Spec: cloud_deployment_implementation.md §C5 (Cloud Scheduler),
#       Phase C Appendix (GCP Resource Inventory)
# ==========================================================
set -euo pipefail

PROJECT="anizai-pipehub"
CLUSTER="anizai-cluster"
ZONE="us-central1-a"
NODE_POOL="main-pool"
SCHEDULER_SA="scheduler-scaler@${PROJECT}.iam.gserviceaccount.com"
REGION="us-central1"

SETSIZE_URL="https://container.googleapis.com/v1/projects/${PROJECT}/zones/${ZONE}/clusters/${CLUSTER}/nodePools/${NODE_POOL}/setSize"

echo "=== C5 — Cloud Scheduler: main-pool scale up/down ==="

# --- Enable Cloud Scheduler API if not already enabled ---
echo "[1/4] Enabling cloudscheduler API..."
gcloud services enable cloudscheduler.googleapis.com --project="${PROJECT}"
echo "  [OK] cloudscheduler.googleapis.com enabled"

# --- Create scheduler-scaler GSA ---
echo "[2/4] Creating scheduler-scaler service account..."
gcloud iam service-accounts create scheduler-scaler \
  --display-name="Cloud Scheduler — GKE node pool scaler" \
  --project="${PROJECT}" 2>/dev/null || echo "  (already exists, skipping)"

gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SCHEDULER_SA}" \
  --role=roles/container.admin \
  --condition=None \
  2>&1 | grep -E "(Updated|container.admin)" || true
echo "  [OK] roles/container.admin bound to ${SCHEDULER_SA}"

# --- Create or update scale-up job ---
echo "[3/4] Creating scale-up-main-pool job (Mon-Fri 08:00 IL = 05:00 UTC)..."
gcloud scheduler jobs create http scale-up-main-pool \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 5 * * 1-5" \
  --time-zone="Asia/Jerusalem" \
  --uri="${SETSIZE_URL}" \
  --message-body='{"nodeCount":1}' \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --oauth-service-account-email="${SCHEDULER_SA}" \
  2>/dev/null || \
gcloud scheduler jobs update http scale-up-main-pool \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 5 * * 1-5" \
  --time-zone="Asia/Jerusalem" \
  --uri="${SETSIZE_URL}" \
  --message-body='{"nodeCount":1}' \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --oauth-service-account-email="${SCHEDULER_SA}"
echo "  [OK] scale-up-main-pool created/updated"

# --- Create or update scale-down job ---
echo "[4/4] Creating scale-down-main-pool job (Mon-Fri 18:00 IL = 15:00 UTC)..."
gcloud scheduler jobs create http scale-down-main-pool \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 15 * * 1-5" \
  --time-zone="Asia/Jerusalem" \
  --uri="${SETSIZE_URL}" \
  --message-body='{"nodeCount":0}' \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --oauth-service-account-email="${SCHEDULER_SA}" \
  2>/dev/null || \
gcloud scheduler jobs update http scale-down-main-pool \
  --project="${PROJECT}" \
  --location="${REGION}" \
  --schedule="0 15 * * 1-5" \
  --time-zone="Asia/Jerusalem" \
  --uri="${SETSIZE_URL}" \
  --message-body='{"nodeCount":0}' \
  --http-method=POST \
  --headers="Content-Type=application/json" \
  --oauth-service-account-email="${SCHEDULER_SA}"
echo "  [OK] scale-down-main-pool created/updated"

echo ""
echo "=== Done. Verify: ==="
echo "  gcloud scheduler jobs list --project=${PROJECT} --location=${REGION}"
echo ""
echo "  Manual test (scale down — only run when all pods are ready to lose main-pool):"
echo "  gcloud scheduler jobs run scale-down-main-pool --project=${PROJECT} --location=${REGION}"
echo "  Manual test (scale up):"
echo "  gcloud scheduler jobs run scale-up-main-pool --project=${PROJECT} --location=${REGION}"
