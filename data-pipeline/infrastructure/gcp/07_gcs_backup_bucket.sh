#!/usr/bin/env bash
# ==========================================================
# 07_gcs_backup_bucket.sh — GCS backup bucket + lifecycle + IAM
# ==========================================================
# Creates gs://anizai-pipeline-backups/ with a 30-day object
# lifecycle on the postgres/ prefix, then grants the pipeline-runtime
# GSA roles/storage.objectCreator so the pg_dump CronJob can write
# dumps without a JSON key file (Workload Identity).
#
# Idempotent: safe to re-run. Bucket creation fails silently if the
# bucket already exists (--quiet flag). Lifecycle policy is set on
# every run (idempotent by gsutil semantics). IAM binding is additive.
#
# Prerequisites:
#   - gcloud authenticated: gcloud config get-value project = anizai-pipeline
#   - storage.googleapis.com API enabled (enabled in 00_enable_apis.sh)
#   - pipeline-runtime GSA exists (created in C1 04_create_cluster.sh)
#
# Usage:
#   bash infrastructure/gcp/07_gcs_backup_bucket.sh
#
# Spec: cloud_deployment_implementation.md §C5.11, §C5 D2
# ==========================================================
set -euo pipefail

PROJECT="anizai-pipeline"
BUCKET="gs://anizai-pipeline-backups"
GSA="pipeline-runtime@${PROJECT}.iam.gserviceaccount.com"

echo "=== C5.11 — GCS backup bucket setup ==="

# --- Create bucket (us-central1, single-region, standard class) ---
echo "[1/3] Creating bucket ${BUCKET} (no-op if exists)..."
gsutil mb -p "${PROJECT}" -l us-central1 -c standard "${BUCKET}" 2>/dev/null || true
echo "  [OK] Bucket ready"

# --- 30-day lifecycle on postgres/ prefix ---
echo "[2/3] Applying 30-day lifecycle rule on postgres/ prefix..."
cat > /tmp/anizai_lifecycle.json << 'EOF'
{
  "rule": [
    {
      "action": { "type": "Delete" },
      "condition": {
        "age": 30,
        "matchesPrefix": ["postgres/"]
      }
    }
  ]
}
EOF
gsutil lifecycle set /tmp/anizai_lifecycle.json "${BUCKET}"
rm -f /tmp/anizai_lifecycle.json
echo "  [OK] Lifecycle rule applied (30 days, postgres/ prefix)"

# --- Grant pipeline-runtime objectCreator on the bucket ---
echo "[3/3] Granting roles/storage.objectCreator to ${GSA}..."
gsutil iam ch "serviceAccount:${GSA}:roles/storage.objectAdmin" "${BUCKET}"
echo "  [OK] IAM binding set (objectAdmin — gsutil cp from stdin requires storage.objects.list)"

echo ""
echo "=== Done. Verify: ==="
echo "  gsutil lifecycle get ${BUCKET}"
echo "  gsutil iam get ${BUCKET}"
