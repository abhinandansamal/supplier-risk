#!/usr/bin/env bash
# Run one headless load-test step against an isolated app instance and the fake upstreams.
#
# usage: loadtests/run_step.sh <label> <users> <spawn_rate> <seconds> [KEY=VALUE ...]
#   KEY=VALUE pairs are extra app settings for this step (e.g. QUEUE_MAX_LENGTH=200).
# Environment: LOADTEST_SCRATCH (work dir, default ./.loadtest-scratch), APP_PORT (8100),
#   FAKE_*_PORT (18088 / 18888 / 17070), FAKE_* latency and failure settings (fake_upstreams.py).
#
# Isolation: the app runs from the scratch dir, so pydantic-settings never finds the repo's .env,
# and every required setting is given here with fake values pointing at the fakes. Logs, traces
# and the SQLite database live in the scratch dir. Nothing touches port 8000 or real services.
set -euo pipefail
if [[ $# -lt 4 ]]; then
  sed -n '2,10p' "$0"; exit 2
fi
LABEL=$1 USERS=$2 SPAWN=$3 SECONDS_RUN=$4; shift 4
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY="$REPO/.venv/bin/python"
SCRATCH=${LOADTEST_SCRATCH:-$REPO/.loadtest-scratch}
APP_PORT=${APP_PORT:-8100}
export FAKE_LLM_PORT=${FAKE_LLM_PORT:-18088} FAKE_ZETARIS_PORT=${FAKE_ZETARIS_PORT:-18888}
export FAKE_CONTEXT_PORT=${FAKE_CONTEXT_PORT:-17070}
OUT="$REPO/docs/load-tests"
STEP="$SCRATCH/$LABEL"

for port in "$APP_PORT" "$FAKE_LLM_PORT" "$FAKE_ZETARIS_PORT" "$FAKE_CONTEXT_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port is taken; pick another" >&2; exit 1
  fi
done
rm -rf "$STEP"; mkdir -p "$STEP" "$OUT"
[[ -e "$STEP/.env" ]] && { echo "unexpected .env in scratch" >&2; exit 1; }

# Fake values only (same style as backend/tests/unit/conftest.py FAKE_ENV).
export LOADTEST_PASSWORD_BUYER="buyer-pass-LOAD-9991"
export LOADTEST_PASSWORD_RISK_MANAGER="risk-pass-LOAD-9992"
export LOADTEST_PASSWORD_DIRECTOR="director-pass-LOAD-9993"
APP_ENV_VARS=(
  "ZETARIS_REST_URL=http://127.0.0.1:$FAKE_ZETARIS_PORT/api/v1.0"
  "ZETARIS_ORG_ID=org-load"
  "ZETARIS_KEY_RISK=zk-secret-VALUE-1111"
  "ZETARIS_KEY_BUYER=zk-buyer-VALUE-5555"
  "ZETARIS_USER_AGENT=supplier-risk loadtest@example.com"
  "METERLESS_BASE_URL=http://127.0.0.1:$FAKE_LLM_PORT/v1"
  "METERLESS_RUNTIME_TOKEN=mt-load-VALUE-2222"
  "METERLESS_APP_ID=supplier-risk"
  "METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b"
  "METERLESS_DEEP_MODEL=nvidia/nemotron-3.5-ultra"
  "METERLESS_FAST_MODEL=nvidia/nemotron-3.5-bolt-e4b"
  "METERLESS_EMBED_MODEL=nvidia/nemotron-3.5-embed-1b"
  "CONTEXT_SERVICE_URL=http://127.0.0.1:$FAKE_CONTEXT_PORT"
  "CONTEXT_SERVICE_TOKEN=cs-load-VALUE-7777-0123456789abcdef"
  "ERP_DATABASE_URL=postgresql://erp:erp-load@127.0.0.1:1/erp"
  "APP_DATABASE_URL=sqlite:///$STEP/app.db"
  "JWT_SECRET=jwt-load-VALUE-8888-0123456789abcdef"
  "DEMO_BUYER_PASSWORD=$LOADTEST_PASSWORD_BUYER"
  "DEMO_RISK_PASSWORD=$LOADTEST_PASSWORD_RISK_MANAGER"
  "DEMO_DIRECTOR_PASSWORD=$LOADTEST_PASSWORD_DIRECTOR"
  "LOG_DIR=$STEP/logs" "TRACE_DIR=$STEP/runs"
  "LANGCHAIN_TRACING_V2=false" "LANGSMITH_TRACING=false" "APP_ENV=prod"
  "$@"
)

PIDS=()
cleanup() { for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done; wait 2>/dev/null || true; }
trap cleanup EXIT

(cd "$STEP" && exec "$PY" "$REPO/loadtests/fake_upstreams.py") >"$STEP/fakes.log" 2>&1 &
PIDS+=($!)
(cd "$STEP" && exec env "${APP_ENV_VARS[@]}" "$PY" -m uvicorn --factory --app-dir "$REPO/backend" \
  app.main:create --host 127.0.0.1 --port "$APP_PORT" --workers 1 --no-access-log) \
  >"$STEP/app.stdout" 2>&1 &
APP_PID=$!; PIDS+=("$APP_PID")

for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$APP_PORT/ready" 2>/dev/null | grep -q '"cache":"warm"' && break
  sleep 1
done
echo "ready: $(curl -sS "http://127.0.0.1:$APP_PORT/ready")"

(cd "$STEP" && env LOADTEST_APP_PID="$APP_PID" LOADTEST_MONITOR_CSV="$OUT/${LABEL}_monitor.csv" \
  "$PY" -m locust -f "$REPO/loadtests/locustfile.py" --headless -H "http://127.0.0.1:$APP_PORT" \
  -u "$USERS" -r "$SPAWN" -t "${SECONDS_RUN}s" --stop-timeout 5 --only-summary \
  --csv "$OUT/$LABEL" --exit-code-on-error 0) >"$STEP/locust.log" 2>&1 || true

kill -0 "$APP_PID" 2>/dev/null && echo "app alive" || echo "APP DIED"
for port in "$FAKE_LLM_PORT" "$FAKE_ZETARIS_PORT" "$FAKE_CONTEXT_PORT"; do
  echo "fake $port: $(curl -sS "http://127.0.0.1:$port/_stats" || true)"
done
"$PY" "$REPO/loadtests/summarize.py" "$LABEL" "$OUT" "$STEP" | tee "$OUT/${LABEL}_summary.md"
