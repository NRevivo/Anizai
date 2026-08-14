#!/usr/bin/env bash
# ==========================================================
# Anizai — Migrate .env secrets to Secret Manager
# (Phase C, Sprint C1.7)
# ==========================================================
# Why:
#   Per Design Decision D3 (no JSON keys, no committed secrets), all
#   sensitive values must live in Secret Manager and reach pods via
#   Workload Identity + the Secret Manager CSI driver. This script
#   one-shots the local .env into Secret Manager so .env can stop
#   being authoritative — pods read from SM after Sprint C1.
#
#   The 17 keys below are the explicit allowlist of *secrets* in the
#   project's .env. Non-secret config (POSTGRES_USER, POSTGRES_DB,
#   POSTGRES_HOST, POSTGRES_PORT, KAFKA_BOOTSTRAP_SERVERS,
#   OPENAI_MODEL_NAME, AIRFLOW_ADMIN_USERNAME, ARXIV_MAX_RESULTS,
#   HACKERNEWS_API_BASE_URL) is intentionally NOT migrated — those
#   stay in pod env directly.
#
# Security:
#   Each secret value is piped via stdin to `gcloud secrets create
#   --data-file=-` so values never appear in the shell's command
#   line and never enter shell history.
#
# When to (re-)run:
#   - Once at Sprint C1.
#   - Idempotent: if a secret already exists, a new VERSION is added
#     (current behavior is "skip" — flip ADD_NEW_VERSION=true to
#     update values when .env rotates).
#
# Required:
#   - gcloud authenticated as a project Owner / Secret Manager Admin.
#   - .env file at $ENV_FILE (default: data-pipeline/infrastructure/.env).
#
# Spec: cloud_deployment_implementation.md §C1.7 (D3)
# ==========================================================

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-anizai-pipehub}"
ENV_FILE="${ENV_FILE:-data-pipeline/infrastructure/.env}"
ADD_NEW_VERSION="${ADD_NEW_VERSION:-false}"

# Allowlist — explicit, audited list of sensitive keys in .env.
# DO NOT add non-secret config to this list (see header comment).
#
# Why 17 and not 15 (cloud project migration, 2026-08-14):
#   This script ran exactly once, in Sprint C1. Every secret added after
#   that — GMAIL_APP_PASSWORD (Phase 9.5-C) and TELEGRAM_SESSION_FILE
#   (C4.12) — was created directly in Secret Manager against a project
#   that already existed, bypassing .env and this allowlist. The drift is
#   invisible while a project exists and fatal when one is built from
#   scratch: NEWSAI_API_KEY missing strands airflow-scheduler and
#   airflow-webserver in ContainerCreating (airflow-secrets-spc mount
#   failure), and GMAIL_APP_PASSWORD missing stops the alertmanager pod
#   ever starting (alertmanager-secrets-spc).
#
#   TELEGRAM_SESSION_FILE is the third such secret but is NOT listed here:
#   it is binary and cannot live in .env, so it stays a separate
#   `gcloud secrets create --data-file=` upload.
#
#   NEWSAI_API_KEY vs THE_NEWS_API_KEY: both appear below, and that is
#   deliberate. .env and airflow-secrets-spc use NEWSAI_API_KEY; the
#   rename to THE_NEWS_API_KEY is KG-C-5 and is deferred until the
#   anizai-airflow rebuild so the rename lands in one coordinated pass.
#   Until then THE_NEWS_API_KEY is absent from .env and reports
#   "absent or empty" — expected, not a fault. POLYMARKET_API_KEY and
#   POLYMARKET_API_SECRET report the same, because Polymarket uses public
#   endpoints only and both are intentionally empty.
SECRET_KEYS=(
  POSTGRES_PASSWORD
  AIRFLOW_POSTGRES_PASSWORD
  AIRFLOW_FERNET_KEY
  AIRFLOW_ADMIN_PASSWORD
  GRAFANA_ADMIN_PASSWORD
  GMAIL_APP_PASSWORD
  OPENAI_API_KEY
  NEWSAI_API_KEY
  THE_NEWS_API_KEY
  FRED_API_KEY
  OPENWEATHER_API_KEY
  OPENSKY_CLIENT_ID
  OPENSKY_CLIENT_SECRET
  POLYMARKET_API_KEY
  POLYMARKET_API_SECRET
  TELEGRAM_API_ID
  TELEGRAM_API_HASH
)

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: env file not found at '${ENV_FILE}'." >&2
  echo "Set ENV_FILE=<path> or copy .env.example to .env first." >&2
  exit 1
fi

# ----------------------------------------------------------
# Helper: extract a value for KEY from .env, stripping quotes
# and inline trailing comments. Returns empty string if KEY
# is missing or empty.
# ----------------------------------------------------------
read_env_value() {
  local KEY="$1"
  # Match KEY= at start of line (allow leading whitespace), capture rest.
  # Strip surrounding single/double quotes if present.
  local LINE
  LINE="$(grep -E "^[[:space:]]*${KEY}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${LINE}" ]]; then
    return 0
  fi
  # Strip "KEY=" prefix.
  local VAL="${LINE#*=}"
  # Strip inline comments (only if value isn't quoted — keep simple).
  # Strip surrounding quotes.
  if [[ "${VAL}" =~ ^\"(.*)\"$ ]]; then
    VAL="${BASH_REMATCH[1]}"
  elif [[ "${VAL}" =~ ^\'(.*)\'$ ]]; then
    VAL="${BASH_REMATCH[1]}"
  fi
  printf '%s' "${VAL}"
}

# ----------------------------------------------------------
# Helper: ensure a secret exists with the given value.
# ----------------------------------------------------------
ensure_secret() {
  local KEY="$1"
  local VALUE="$2"

  if [[ -z "${VALUE}" ]]; then
    echo "  [SKIP] ${KEY} is empty in ${ENV_FILE} — not migrating."
    return 0
  fi

  if gcloud secrets describe "${KEY}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    if [[ "${ADD_NEW_VERSION}" == "true" ]]; then
      printf '%s' "${VALUE}" | gcloud secrets versions add "${KEY}" \
        --project="${PROJECT_ID}" \
        --data-file=- >/dev/null
      echo "  [VERSIONED] ${KEY} (added new version)"
    else
      echo "  [SKIP] ${KEY} already exists (set ADD_NEW_VERSION=true to update)."
    fi
    return 0
  fi

  printf '%s' "${VALUE}" | gcloud secrets create "${KEY}" \
    --project="${PROJECT_ID}" \
    --replication-policy=automatic \
    --data-file=- >/dev/null
  echo "  [CREATED] ${KEY}"
}

echo "=== Migrating ${#SECRET_KEYS[@]} secrets from ${ENV_FILE} to Secret Manager (${PROJECT_ID}) ==="

MISSING=()
for KEY in "${SECRET_KEYS[@]}"; do
  VAL="$(read_env_value "${KEY}")"
  if [[ -z "${VAL}" ]]; then
    MISSING+=("${KEY}")
  fi
  ensure_secret "${KEY}" "${VAL}"
done

echo "=== Verifying ==="
gcloud secrets list --project="${PROJECT_ID}" --format="table(name)"

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo
  echo "WARNING: the following keys are absent or empty in ${ENV_FILE}:"
  for K in "${MISSING[@]}"; do echo "  - ${K}"; done
  echo "Set them in .env and re-run before Sprint C2."
fi

echo "=== Done. ==="
