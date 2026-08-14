#!/usr/bin/env bash
# ==========================================================
# Anizai — Create Artifact Registry repo + Docker auth helper
# (Phase C, Sprint C1.3)
# ==========================================================
# Why:
#   GKE pulls images from Artifact Registry, not from local Docker.
#   The repo lives in us-central1 (matches cluster region) so image
#   pulls cross zero region boundaries (no inter-region egress, no
#   cold-start latency on first pull). Single Docker-format repo
#   per Design Decision D5.
#
# When to (re-)run:
#   - Once at the start of Phase C (after APIs enabled).
#   - Idempotent: skips creation if the repo already exists.
#   - Re-run gcloud auth configure-docker any time a new shell needs
#     to push to the registry (the credential helper is per-shell).
#
# Runtime: ~30 seconds.
#
# Spec: cloud_deployment_implementation.md §C1.3 (D5)
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipehub}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-anizai-images}"
REGISTRY_HOST="${REGION}-docker.pkg.dev"

echo "=== Checking for existing Artifact Registry repo ==="
if gcloud artifacts repositories describe "${REPO}" \
      --location="${REGION}" \
      --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "  [SKIP] Repository '${REPO}' already exists in ${REGION}."
else
  echo "=== Creating Artifact Registry repository '${REPO}' in ${REGION} ==="
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Anizai pipeline + agent container images" \
    --project="${PROJECT_ID}"
  echo "  [OK] Repository created."
fi

echo "=== Configuring local Docker credential helper for ${REGISTRY_HOST} ==="
# This writes/updates ~/.docker/config.json so 'docker push' against
# the AR host uses gcloud's access token automatically.
gcloud auth configure-docker "${REGISTRY_HOST}" --quiet

echo "=== Verifying repo is reachable ==="
gcloud artifacts repositories describe "${REPO}" \
  --location="${REGION}" \
  --project="${PROJECT_ID}" \
  --format="value(name,format)"

echo "=== Done. Push targets:"
echo "      ${REGISTRY_HOST}/${PROJECT_ID}/${REPO}/<image>:<tag>"
