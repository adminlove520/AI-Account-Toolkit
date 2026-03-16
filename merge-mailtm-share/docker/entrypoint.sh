#!/usr/bin/env sh
set -eu

APP_DIR="${APP_DIR:-/app}"
WORK_DIR="${WORK_DIR:-/data}"
CONFIG_PATH="${CONFIG_PATH:-$WORK_DIR/config.json}"
LOG_DIR="${LOG_DIR:-$WORK_DIR/logs}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-900}"
STARTUP_DELAY_SECONDS="${STARTUP_DELAY_SECONDS:-5}"
JITTER_SECONDS="${JITTER_SECONDS:-30}"
FAILURE_BACKOFF_SECONDS="${FAILURE_BACKOFF_SECONDS:-120}"
CONTINUE_ON_ERROR="${CONTINUE_ON_ERROR:-1}"
RUN_ONCE="${RUN_ONCE:-0}"
MIN_CANDIDATES="${MIN_CANDIDATES:-}"
TIMEOUT_OVERRIDE="${TIMEOUT_OVERRIDE:-}"

mkdir -p "$WORK_DIR" "$LOG_DIR"
cd "$WORK_DIR"

if [ "${STARTUP_DELAY_SECONDS}" -gt 0 ] 2>/dev/null; then
  echo "[entrypoint] startup delay: ${STARTUP_DELAY_SECONDS}s"
  sleep "${STARTUP_DELAY_SECONDS}"
fi

run_once() {
  set -- python "${APP_DIR}/auto_pool_maintainer_mailtm.py" --config "${CONFIG_PATH}" --log-dir "${LOG_DIR}"
  if [ -n "${MIN_CANDIDATES}" ]; then
    set -- "$@" --min-candidates "${MIN_CANDIDATES}"
  fi
  if [ -n "${TIMEOUT_OVERRIDE}" ]; then
    set -- "$@" --timeout "${TIMEOUT_OVERRIDE}"
  fi

  echo "[entrypoint] running: $*"
  "$@"
}

loop_index=0
while true; do
  loop_index=$((loop_index + 1))
  echo "[entrypoint] ===== cycle ${loop_index} started at $(date '+%Y-%m-%d %H:%M:%S') ====="

  exit_code=0
  if run_once; then
    exit_code=0
  else
    exit_code=$?
  fi

  echo "[entrypoint] ===== cycle ${loop_index} finished with code ${exit_code} at $(date '+%Y-%m-%d %H:%M:%S') ====="

  if [ "${RUN_ONCE}" = "1" ]; then
    exit "${exit_code}"
  fi

  if [ "${exit_code}" -ne 0 ] && [ "${CONTINUE_ON_ERROR}" != "1" ]; then
    exit "${exit_code}"
  fi

  sleep_seconds="${INTERVAL_SECONDS}"
  if [ "${exit_code}" -ne 0 ] && [ "${FAILURE_BACKOFF_SECONDS}" -gt 0 ] 2>/dev/null; then
    sleep_seconds="${FAILURE_BACKOFF_SECONDS}"
  fi

  if [ "${JITTER_SECONDS}" -gt 0 ] 2>/dev/null; then
    jitter="$(python - <<'PY'
import os
import random
upper = int(os.environ.get("JITTER_SECONDS", "0") or 0)
print(random.randint(0, upper) if upper > 0 else 0)
PY
)"
    sleep_seconds=$((sleep_seconds + jitter))
    echo "[entrypoint] jitter applied: +${jitter}s"
  fi

  echo "[entrypoint] sleeping ${sleep_seconds}s before next cycle"
  sleep "${sleep_seconds}"
done

