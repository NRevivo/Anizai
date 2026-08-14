#!/usr/bin/env bash
# ==========================================================
# Anizai — Cloud Monitoring setup (Phase 9.5 Stage C Item 5)
# ==========================================================
# Creates the Cloud Logging-based metric + Cloud Monitoring email
# notification channel + 2 Cloud Monitoring alerting policies that
# together implement the OpenAI rate-limit proxy alerts:
#   - O-OpenAI-1 (WARNING): any RateLimitError in past 5m.
#   - O-OpenAI-2 (CRITICAL): >50 RateLimitErrors in past 1h.
#
# Why a separate path from Prometheus + Alertmanager (Items 1-4):
#   The agent worker doesn't expose a Prometheus `agent_openai_*`
#   metric (Sprint 18 /metrics stub). Adding Prometheus instrumentation
#   would require application code, which is out of Stage C scope.
#   The next-best proxy is a log-line count: every time the OpenAI
#   SDK raises a 429, the agent / Flink TM logs it, and those logs
#   already flow through fluentbit-gke to Cloud Logging. A
#   Cloud Logging-based metric counts those lines; a Cloud Monitoring
#   policy alerts on the metric. No new in-cluster infrastructure.
#
# Idempotency: safe to re-run. We use existence checks before each
# create, and delete-then-create for the log metric (lets filter
# updates take effect on re-run).
#
# Spec: phase95_cluster_robustness_implementation.md Stage C Item 5.
# ==========================================================
set -euo pipefail

PROJECT="anizai-pipehub"
EMAIL="ron.mintz21@gmail.com"
METRIC_NAME="openai_rate_limit_errors"

# ----------------------------------------------------------
# 1. Cloud Logging-based metric: openai_rate_limit_errors
# ----------------------------------------------------------
echo ">>> Creating/updating log-based metric: $METRIC_NAME"
if gcloud logging metrics describe "$METRIC_NAME" --project="$PROJECT" >/dev/null 2>&1; then
  gcloud logging metrics delete "$METRIC_NAME" --project="$PROJECT" --quiet
fi

gcloud logging metrics create "$METRIC_NAME" \
  --project="$PROJECT" \
  --description="OpenAI rate-limit (HTTP 429) occurrences in agent + Flink TM logs. PROXY for KG-PHASE-9.5-1 RPD ceiling." \
  --log-filter='resource.type="k8s_container"
AND resource.labels.namespace_name="anizai"
AND (resource.labels.container_name="agent-worker"
     OR resource.labels.container_name="flink-taskmanager")
AND (textPayload=~"RateLimitError"
     OR jsonPayload.message=~"rate limit"
     OR textPayload=~"rate limit")'

# ----------------------------------------------------------
# 2. Email notification channel
# ----------------------------------------------------------
echo ">>> Creating email notification channel"
CHANNEL_ID=$(gcloud beta monitoring channels list \
  --project="$PROJECT" \
  --filter="displayName='Anizai pipeline ops'" \
  --format="value(name)" 2>/dev/null | head -n1 || true)

if [ -z "$CHANNEL_ID" ]; then
  CHANNEL_ID=$(gcloud beta monitoring channels create \
    --project="$PROJECT" \
    --display-name="Anizai pipeline ops" \
    --type=email \
    --channel-labels="email_address=$EMAIL" \
    --format="value(name)")
  echo "    Created: $CHANNEL_ID"
else
  echo "    Existing: $CHANNEL_ID"
fi

# ----------------------------------------------------------
# 3. Cloud Monitoring alerting policies
# ----------------------------------------------------------

create_or_update_policy() {
  local display_name="$1"
  local severity="$2"
  local threshold="$3"
  local duration="$4"
  local description="$5"

  local subject="[anizai-pipehub] [${severity}] OpenAI rate-limit alert"
  local existing
  existing=$(gcloud alpha monitoring policies list \
    --project="$PROJECT" \
    --filter="displayName='$display_name'" \
    --format="value(name)" 2>/dev/null | head -n1 || true)

  cat > /tmp/policy.json <<EOF
{
  "displayName": "$display_name",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "openai_rate_limit_errors threshold",
      "conditionThreshold": {
        "filter": "metric.type=\"logging.googleapis.com/user/$METRIC_NAME\" AND resource.type=\"k8s_container\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": $threshold,
        "duration": "${duration}s",
        "aggregations": [
          {
            "alignmentPeriod": "${duration}s",
            "perSeriesAligner": "ALIGN_DELTA",
            "crossSeriesReducer": "REDUCE_SUM"
          }
        ]
      }
    }
  ],
  "documentation": {
    "content": "$description",
    "mimeType": "text/markdown",
    "subject": "$subject"
  },
  "notificationChannels": ["$CHANNEL_ID"],
  "alertStrategy": {
    "autoClose": "604800s"
  }
}
EOF

  if [ -z "$existing" ]; then
    gcloud alpha monitoring policies create \
      --project="$PROJECT" \
      --policy-from-file=/tmp/policy.json
  else
    gcloud alpha monitoring policies update "$existing" \
      --project="$PROJECT" \
      --policy-from-file=/tmp/policy.json
  fi
  rm /tmp/policy.json
}

echo ">>> Creating/updating alerting policy: OpenAI rate-limit WARNING"
create_or_update_policy \
  "Anizai OpenAI rate-limit (WARNING)" \
  "WARNING" \
  0 \
  300 \
  "Any OpenAI 429 detected in the past 5 minutes. KG-PHASE-9.5-1 RPD ceiling PROXY (log-line-derived). Verify directly at https://platform.openai.com/usage. Cost-analysis triage runs in its parallel session (KG-PHASE-9.5-9)."

echo ">>> Creating/updating alerting policy: OpenAI rate-limit CRITICAL"
create_or_update_policy \
  "Anizai OpenAI rate-limit storm (CRITICAL)" \
  "CRITICAL" \
  50 \
  3600 \
  "Sustained >50 OpenAI 429 errors over the past 1 hour. Either pipeline is exceeding RPD ceiling or quota is exhausted. Consider pausing Gold (cancel job) until cost-analysis concludes (KG-PHASE-9.5-9)."

echo ""
echo "=== Cloud Monitoring setup complete ==="
echo "  Log-based metric : projects/$PROJECT/metrics/$METRIC_NAME"
echo "  Email channel    : $CHANNEL_ID"
echo "  Alerting policies: 2 (warning, critical)"
