#!/usr/bin/env bash
#
# Provision the calibration runner on GCP.
#
# NOTHING HERE RUNS BY ACCIDENT. Every step is a separate function, the script
# does nothing when invoked with no arguments, and every step prints what it
# will create and waits for confirmation unless CONFIRM=yes is set.
#
#   ./provision.sh plan          # print what would be created. Touches nothing.
#   ./provision.sh db            # Cloud SQL instance      ~$8-10/month
#   ./provision.sh sa            # service accounts        free
#   ./provision.sh iam           # IAM bindings            free  (needs an admin)
#   ./provision.sh secrets       # Secret Manager entries  ~free
#   ./provision.sh build         # build + push the image  ~free
#   ./provision.sh deploy        # Cloud Run service       pay-per-request
#   ./provision.sh schedulers    # Cloud Scheduler jobs    ~$0.10/month
#
# Read infrastructure/README.md first. In particular: `schedulers` deliberately
# does NOT create a dispatch job. See that file for why.

set -euo pipefail

PROJECT="${CALIBRATION_PROJECT:-anizai-pipeline}"
FIRESTORE_PROJECT="${CALIBRATION_FIRESTORE_PROJECT:-anizai-ai}"
REGION="${CALIBRATION_REGION:-us-central1}"

DB_INSTANCE="anizai-calibration-db"
DB_NAME="anizai_calibration"
DB_USER="calibration_app"
DB_TIER="db-f1-micro"

SERVICE="calibration-runner"
RUNNER_SA="calibration-runner@${PROJECT}.iam.gserviceaccount.com"
SCHEDULER_SA="calibration-scheduler@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/anizai-images/anizai-calibration"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*"; }

confirm() {
  if [[ "${CONFIRM:-}" == "yes" ]]; then return 0; fi
  read -r -p "  proceed? [y/N] " reply
  [[ "$reply" == "y" || "$reply" == "Y" ]]
}

# ----------------------------------------------------------------------------

cmd_plan() {
  say "Would create, in project ${PROJECT} (region ${REGION}):"
  cat <<EOF

  Cloud SQL      ${DB_INSTANCE}  (${DB_TIER}, 10GB, single zone)   ~\$8-10/month
                 database ${DB_NAME}, user ${DB_USER}
  Service acct   ${RUNNER_SA}
  Service acct   ${SCHEDULER_SA}
  Secrets        CALIBRATION_DB_PASSWORD, FIREBASE_AUTH_OPERATOR_EMAILS
  Image          ${IMAGE}
  Cloud Run      ${SERVICE}  (min 0, max 2, CPU only during request)
  Schedulers     discover hourly, harvest 5-min, resolve hourly, snapshot daily
                 NO dispatch job — see infrastructure/README.md

  Cross-project  ${RUNNER_SA} needs roles/datastore.user on ${FIRESTORE_PROJECT}

EOF
  warn "The cross-project binding needs an admin on ${FIRESTORE_PROJECT}."
  warn "Check whether you have it before starting:"
  echo "  gcloud projects get-iam-policy ${FIRESTORE_PROJECT} >/dev/null && echo ok"
}

cmd_db() {
  say "Cloud SQL ${DB_INSTANCE} — this is the step that costs money (~\$8-10/month)"
  confirm || return 0

  gcloud sql instances create "${DB_INSTANCE}" \
    --project="${PROJECT}" \
    --database-version=POSTGRES_16 \
    --tier="${DB_TIER}" \
    --region="${REGION}" \
    --storage-size=10 \
    --storage-type=SSD \
    --availability-type=zonal \
    --no-backup

  # Generated here and never printed. It goes straight into Secret Manager;
  # a password that appears in a terminal is a password in someone's shell
  # history and in this script's Cloud Build log.
  local password
  password="$(openssl rand -base64 32)"

  gcloud sql databases create "${DB_NAME}" \
    --instance="${DB_INSTANCE}" --project="${PROJECT}"
  gcloud sql users create "${DB_USER}" \
    --instance="${DB_INSTANCE}" --project="${PROJECT}" --password="${password}"

  printf '%s' "${password}" | gcloud secrets create CALIBRATION_DB_PASSWORD \
    --project="${PROJECT}" --data-file=- --replication-policy=automatic \
    || printf '%s' "${password}" | gcloud secrets versions add CALIBRATION_DB_PASSWORD \
       --project="${PROJECT}" --data-file=-

  say "Created. Apply the schema before deploying:"
  echo "  cloud-sql-proxy ${PROJECT}:${REGION}:${DB_INSTANCE} &"
  echo "  CALIBRATION_DATABASE_URL=postgresql://${DB_USER}:<pw>@localhost:5432/${DB_NAME} \\"
  echo "    python -m calibration.cli init-db"
}

cmd_sa() {
  say "Service accounts (free)"
  confirm || return 0

  gcloud iam service-accounts create calibration-runner \
    --project="${PROJECT}" --display-name="Calibration runner" || true
  gcloud iam service-accounts create calibration-scheduler \
    --project="${PROJECT}" --display-name="Calibration scheduler" || true
}

cmd_iam() {
  say "IAM bindings (free, but needs project-admin rights)"
  confirm || return 0

  for role in roles/cloudsql.client roles/secretmanager.secretAccessor; do
    gcloud projects add-iam-policy-binding "${PROJECT}" \
      --member="serviceAccount:${RUNNER_SA}" --role="${role}" --condition=None
  done

  warn "The cross-project Firestore binding is the one that usually fails."
  warn "It must be run by someone with setIamPolicy on ${FIRESTORE_PROJECT}:"
  cat <<EOF

  gcloud projects add-iam-policy-binding ${FIRESTORE_PROJECT} \\
    --member="serviceAccount:${RUNNER_SA}" \\
    --role="roles/datastore.user" --condition=None

EOF
  gcloud projects add-iam-policy-binding "${FIRESTORE_PROJECT}" \
    --member="serviceAccount:${RUNNER_SA}" \
    --role="roles/datastore.user" --condition=None \
    || warn "FAILED — hand the command above to a ${FIRESTORE_PROJECT} admin."
}

cmd_secrets() {
  say "Secret Manager — operator allowlist"
  read -r -p "  operator emails (comma-separated): " emails
  confirm || return 0

  printf '%s' "${emails}" | gcloud secrets create FIREBASE_AUTH_OPERATOR_EMAILS \
    --project="${PROJECT}" --data-file=- --replication-policy=automatic \
    || printf '%s' "${emails}" | gcloud secrets versions add FIREBASE_AUTH_OPERATOR_EMAILS \
       --project="${PROJECT}" --data-file=-
}

cmd_build() {
  say "Build and push ${IMAGE}:${VERSION:-0.1.0}"
  confirm || return 0

  gcloud builds submit "$(dirname "$0")/.." \
    --project="${PROJECT}" \
    --tag "${IMAGE}:${VERSION:-0.1.0}"
}

cmd_deploy() {
  say "Cloud Run ${SERVICE} (pay-per-request; min instances 0 so idle is free)"
  warn "Requires the Cloud Run API. Enable it with:"
  warn "  gcloud services enable run.googleapis.com --project=${PROJECT}"
  confirm || return 0

  gcloud run deploy "${SERVICE}" \
    --project="${PROJECT}" --region="${REGION}" \
    --image="${IMAGE}:${VERSION:-0.1.0}" \
    --service-account="${RUNNER_SA}" \
    --add-cloudsql-instances="${PROJECT}:${REGION}:${DB_INSTANCE}" \
    --min-instances=0 --max-instances=2 --concurrency=20 \
    --timeout=540 --cpu-boost --no-allow-unauthenticated \
    --set-env-vars="FIREBASE_PROJECT_ID=${FIRESTORE_PROJECT},CALIBRATION_LOG_JSON=1,CALIBRATION_DISPATCH_TASK_ENABLED=false,CALIBRATION_SCHEDULER_SERVICE_ACCOUNTS=${SCHEDULER_SA}" \
    --set-secrets="CALIBRATION_DB_PASSWORD=CALIBRATION_DB_PASSWORD:latest,FIREBASE_AUTH_OPERATOR_EMAILS=FIREBASE_AUTH_OPERATOR_EMAILS:latest"

  local url
  url="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" \
         --region="${REGION}" --format='value(status.url)')"

  # The audience must equal the service URL, and the URL is only known after
  # the first deploy — hence the second pass.
  gcloud run services update "${SERVICE}" --project="${PROJECT}" --region="${REGION}" \
    --update-env-vars="CALIBRATION_OIDC_AUDIENCE=${url}"

  gcloud run services add-iam-policy-binding "${SERVICE}" \
    --project="${PROJECT}" --region="${REGION}" \
    --member="serviceAccount:${SCHEDULER_SA}" --role=roles/run.invoker

  say "Deployed at ${url}"
}

cmd_schedulers() {
  say "Cloud Scheduler jobs (~\$0.10/month)"
  warn "NO dispatch job is created. Dispatch is the only task that spends"
  warn "tokens, and the agent is brought up by hand per run — an unattended"
  warn "dispatch would queue forecasts nobody collects. See infrastructure/README.md."
  confirm || return 0

  local url
  url="$(gcloud run services describe "${SERVICE}" --project="${PROJECT}" \
         --region="${REGION}" --format='value(status.url)')"

  create_job() {
    gcloud scheduler jobs create http "calibration-$1" \
      --project="${PROJECT}" --location="${REGION}" \
      --schedule="$2" --time-zone=UTC \
      --uri="${url}/tasks/$3" --http-method=POST \
      --oidc-service-account-email="${SCHEDULER_SA}" \
      --oidc-token-audience="${url}" \
      --attempt-deadline=540s \
      || warn "  calibration-$1 already exists — skipped"
  }

  create_job discover-hourly  "0 * * * *"    discover
  create_job harvest-5min     "*/5 * * * *"  harvest
  create_job resolve-hourly   "15 * * * *"   resolve
  create_job snapshot-daily   "30 3 * * *"   snapshot_metrics
}

# ----------------------------------------------------------------------------

case "${1:-}" in
  plan)       cmd_plan ;;
  db)         cmd_db ;;
  sa)         cmd_sa ;;
  iam)        cmd_iam ;;
  secrets)    cmd_secrets ;;
  build)      cmd_build ;;
  deploy)     cmd_deploy ;;
  schedulers) cmd_schedulers ;;
  *)
    sed -n '3,22p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
