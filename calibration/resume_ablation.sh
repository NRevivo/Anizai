#!/usr/bin/env bash
# Resume the 2026-08-18 evidence-ablation run after a reboot or a sleep.
#
# Everything expensive is already done: 15 forecasts were dispatched, answered
# by a local agent worker, and harvested into Postgres on 2026-08-18. What is
# left costs nothing — `resolve` reads the public Polymarket API and the rest
# is arithmetic over rows we already hold.
#
# Safe to run repeatedly. `resolve` is idempotent (UNIQUE on question_id, with
# ON CONFLICT DO NOTHING), and compute-metrics/export overwrite rather than
# accumulate.
#
#   bash calibration/resume_ablation.sh
set -u

REPO="C:/Users/ajrnn/Desktop/Anizai-repo"
cd "$REPO/calibration" || { echo "repo not found at $REPO"; exit 1; }

export PYTHONPATH="$REPO/calibration/src"
# 55433 is the PILOT database — the real measurements. 55432 is the TEST
# database, whose fixtures truncate every calibration_* table. Pointing this
# at 55432 would delete forecasts that cost money to produce.
export CALIBRATION_DATABASE_URL="postgresql://postgres:postgres@localhost:55433/anizai_calibration"
PY="./venv/Scripts/python.exe"

echo "== starting the pilot database =="
docker start anizai-calibration-pilot >/dev/null 2>&1
# Postgres accepts TCP before it accepts queries; without this the first
# command fails with a connection error that looks like the container is broken.
until docker exec anizai-calibration-pilot pg_isready -U postgres >/dev/null 2>&1; do
  sleep 2
done
echo "   ready"

echo
echo "== check-config =="
$PY -m calibration.cli check-config 2>&1 | grep -viE "INFO |WARNING "

echo
echo "== resolve (public Polymarket API, free) =="
$PY -m calibration.cli resolve 2>&1 | grep -viE "INFO |WARNING " | tail -25

echo
echo "== compute-metrics =="
$PY -m calibration.cli compute-metrics 2>&1 | grep -viE "INFO |WARNING "

echo
echo "== export =="
$PY -m calibration.cli export 2>&1 | grep -viE "INFO |WARNING "

echo
echo "== agent vs market, on everything scored so far =="
docker exec anizai-calibration-pilot psql -U postgres -d anizai_calibration -c "
SELECT LEFT(q.question_text, 40)          AS question,
       f.final_probability                AS agent_p,
       q.market_probability_at_pickup      AS market_p,
       r.outcome,
       ROUND(f.brier_score::numeric, 4)                                   AS agent_brier,
       ROUND(POWER(q.market_probability_at_pickup - r.outcome_numeric, 2)::numeric, 4) AS market_brier
FROM calibration_forecasts f
JOIN calibration_questions   q ON q.id = f.question_id
JOIN calibration_resolutions r ON r.question_id = q.id
WHERE f.brier_score IS NOT NULL
ORDER BY q.market_probability_at_pickup DESC NULLS LAST;"

echo
echo "Done. results/ is refreshed; the dashboard reads the same database."
