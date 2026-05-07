#!/usr/bin/env bash
# ==========================================================
# Anizai — Point local kubectl at the cloud cluster
# (Phase C, Sprint C1.9)
# ==========================================================
# Why:
#   `gcloud container clusters get-credentials` writes a kubeconfig
#   entry so subsequent `kubectl` commands target the cloud cluster.
#   Required after cluster creation; also required on any new dev
#   machine before Sprint C2+ commands.
#
#   The gke-gcloud-auth-plugin is required for current GKE auth;
#   the script verifies it is installed before fetching credentials.
#
# When to (re-)run:
#   - Once after C1.8.
#   - Re-run on any new shell or new machine that needs kubectl
#     access to anizai-cluster.
#
# Runtime: ~30 seconds.
#
# Spec: cloud_deployment_implementation.md §C1.9
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipeline}"
CLUSTER_NAME="${CLUSTER_NAME:-anizai-cluster}"
ZONE="${ZONE:-us-central1-a}"

# ----------------------------------------------------------
# Verify the auth plugin is installed. Without it, kubectl
# returns "Unable to connect to the server: getting credentials"
# on first call. Install: gcloud components install gke-gcloud-auth-plugin
# (Cloud SDK 405+) — required since GKE deprecated the in-tree
# auth provider.
# ----------------------------------------------------------
if ! command -v gke-gcloud-auth-plugin >/dev/null 2>&1; then
  echo "ERROR: gke-gcloud-auth-plugin is not installed." >&2
  echo "Install: gcloud components install gke-gcloud-auth-plugin" >&2
  exit 1
fi

echo "=== Fetching credentials for cluster '${CLUSTER_NAME}' (${ZONE}) ==="
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
  --zone "${ZONE}" \
  --project "${PROJECT_ID}"

echo "=== Verifying kubectl can reach the cluster ==="
kubectl config current-context
kubectl get nodes -o wide

echo "=== Done. kubectl is now targeting ${CLUSTER_NAME} in ${PROJECT_ID}. ==="
