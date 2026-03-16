#!/usr/bin/env bash
set -uo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
mkdir -p logs

"$BASE_DIR/merge-mailtm" --config "$BASE_DIR/config.json" --log-dir "$BASE_DIR/logs" "$@"
STATUS=$?

echo
echo "Process exited with code: $STATUS"
if [[ "${MERGE_MAILTM_NO_PAUSE:-0}" != "1" ]]; then
  read -r -p "Press Enter to close this window..." _
fi

exit "$STATUS"

