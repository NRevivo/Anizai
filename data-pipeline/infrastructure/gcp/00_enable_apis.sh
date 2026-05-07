#!/usr/bin/env bash
# ==========================================================
# Anizai — Enable required GCP APIs (Phase C, Sprint C1.1)
# ==========================================================
# Why:
#   GKE, Artifact Registry, Secret Manager, Cloud Logging, and
#   Cloud Monitoring all require their service APIs enabled on
#   the host project before any other gcloud command can act on
#   them (cluster create / image push / secret create all fail
#   otherwise).
#
# When to (re-)run:
#   - Once at the very start of Phase C.
#   - Idempotent: enabling an already-enabled API is a no-op.
#
# Runtime: ~2 minutes (compute + container API enables are slow).
#
# Spec: cloud_deployment_implementation.md §C1.1
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipeline}"

echo "=== Verifying gcloud is authenticated and targeting ${PROJECT_ID} ==="
ACTIVE_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ "${ACTIVE_PROJECT}" != "${PROJECT_ID}" ]]; then
  echo "ERROR: gcloud active project is '${ACTIVE_PROJECT}', expected '${PROJECT_ID}'." >&2
  echo "Run: gcloud config set project ${PROJECT_ID}" >&2
  exit 1
fi

echo "=== Enabling Phase C APIs on ${PROJECT_ID} ==="
# billingbudgets.googleapis.com is required by 01_billing_alerts.sh (gcloud
# billing budgets create/list); it's not in the spec's original 7-API list
# but is a hard dependency for Sprint C1.2. Added here so all API enables
# are co-located in one idempotent script.
gcloud services enable \
  compute.googleapis.com \
  container.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  iam.googleapis.com \
  billingbudgets.googleapis.com \
  --project "${PROJECT_ID}"

echo "=== Verifying enabled APIs ==="
gcloud services list --enabled --project "${PROJECT_ID}" \
  --filter="config.name:(compute.googleapis.com OR container.googleapis.com OR artifactregistry.googleapis.com OR secretmanager.googleapis.com OR logging.googleapis.com OR monitoring.googleapis.com OR iam.googleapis.com OR billingbudgets.googleapis.com)" \
  --format="value(config.name)"

echo "=== Done. ==="
