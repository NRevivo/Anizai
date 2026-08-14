#!/usr/bin/env bash
# ==========================================================
# Anizai — Arm billing alerts (Phase C, Sprint C1.2)
# ==========================================================
# Why:
#   Solo-dev cost ceiling. The cluster (e2-standard-8 main-pool +
#   e2-micro polymarket-pool) is the largest line item; cumulative
#   drift on Artifact Registry storage, GCS backups, and egress
#   can creep up unnoticed.
#   Two budgets per Design Decision D4: ₪200 warning (~$54) + ₪400
#   critical (~$108), each at 50% / 90% / 100% of threshold.
#   Currency is ILS because the linked billing account
#   (01C603-6D345F-105BC9) is denominated in ILS — gcloud requires
#   the budget currency to match the billing account currency.
#
# When to (re-)run:
#   - Once at the start of Phase C (after APIs enabled).
#   - Idempotent: skips creation if a budget with the same display
#     name already exists.
#
# Runtime: ~30 seconds.
#
# Required env vars (must be exported in the shell before running):
#   BILLING_ACCOUNT_ID   — get with: gcloud billing accounts list
#   BILLING_ALERT_EMAIL  — defaults to ron.mintz21@gmail.com if unset
#
# Spec: cloud_deployment_implementation.md §C1.2 (D4)
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipehub}"
BILLING_ALERT_EMAIL="${BILLING_ALERT_EMAIL:-ron.mintz21@gmail.com}"

if [[ -z "${BILLING_ACCOUNT_ID:-}" ]]; then
  echo "ERROR: BILLING_ACCOUNT_ID is not set." >&2
  echo "Find it with: gcloud billing accounts list" >&2
  echo "Then: export BILLING_ACCOUNT_ID=XXXXXX-XXXXXX-XXXXXX" >&2
  exit 1
fi

echo "=== Verifying billing account is linked to ${PROJECT_ID} ==="
LINKED="$(gcloud billing projects describe "${PROJECT_ID}" --format='value(billingAccountName)' 2>/dev/null || true)"
if [[ -z "${LINKED}" ]]; then
  echo "ERROR: No billing account is linked to ${PROJECT_ID}." >&2
  echo "Link it: gcloud billing projects link ${PROJECT_ID} --billing-account=${BILLING_ACCOUNT_ID}" >&2
  exit 1
fi
echo "  Linked billing account: ${LINKED}"

# ----------------------------------------------------------
# Helper: create a budget with 50/90/100% thresholds + default
# IAM-recipient notification, idempotent on display name.
#
# Idempotency uses client-side grep because `gcloud billing budgets list`
# does not support `displayName` as a server-side filter key.
# ----------------------------------------------------------
create_budget() {
  local DISPLAY_NAME="$1"
  local AMOUNT_ILS="$2"

  if gcloud billing budgets list \
        --billing-account="${BILLING_ACCOUNT_ID}" \
        --format="value(displayName)" \
      | grep -Fxq "${DISPLAY_NAME}"; then
    echo "  [SKIP] Budget '${DISPLAY_NAME}' already exists."
    return 0
  fi

  # --filter-projects accepts projects/{project_id}; project_id here is the
  # alphanumeric project ID (anizai-pipehub), NOT the project number.
  gcloud billing budgets create \
    --billing-account="${BILLING_ACCOUNT_ID}" \
    --display-name="${DISPLAY_NAME}" \
    --budget-amount="${AMOUNT_ILS}ILS" \
    --threshold-rule=percent=0.5 \
    --threshold-rule=percent=0.9 \
    --threshold-rule=percent=1.0 \
    --filter-projects="projects/${PROJECT_ID}" \
    --calendar-period=month
  echo "  [OK] Budget '${DISPLAY_NAME}' (₪${AMOUNT_ILS}) created."
}

# Note on email delivery:
#   By default, gcloud routes budget alerts to billing-account default IAM
#   recipients (billing admins). To also notify a specific monitoring
#   channel (e.g. ${BILLING_ALERT_EMAIL}), use --monitoring-notification-channels
#   on a separate `gcloud billing budgets update` call after the channel is
#   created in Cloud Monitoring. See the README for the full recipe — kept
#   out of this script to avoid making notification-channel creation a
#   prerequisite of the script run.

echo "=== Creating warning budget (₪200, ~\$54) ==="
create_budget "Anizai pipeline warning" 200

echo "=== Creating critical budget (₪400, ~\$108) ==="
create_budget "Anizai pipeline critical" 400

echo "=== Verifying budgets ==="
gcloud billing budgets list --billing-account="${BILLING_ACCOUNT_ID}" \
  --format="table(displayName,amount.specifiedAmount.units)"

echo "=== Done. Default IAM recipients of billing account ${BILLING_ACCOUNT_ID} will receive alert emails. ==="
echo "    To route alerts to ${BILLING_ALERT_EMAIL} specifically, see infrastructure/gcp/README.md (\"Custom email channel\")."
