#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
mkdir -p logs

"$BASE_DIR/merge-mailtm" --config "$BASE_DIR/config.json" --log-dir "$BASE_DIR/logs" "$@"

