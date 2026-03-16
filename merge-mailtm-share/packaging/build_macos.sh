#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_NAME="${DIST_NAME:-merge-mailtm}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-build-macos}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_CMD="${PYTHON_BIN}"
elif command -v python3.13 >/dev/null 2>&1; then
  PYTHON_CMD="python3.13"
else
  PYTHON_CMD="python3"
fi

cd "$ROOT_DIR"

"$PYTHON_CMD" -m venv "$VENV_DIR"
VENV_PYTHON="$VENV_DIR/bin/python"

"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r requirements.txt "pyinstaller>=6.0"

"$VENV_PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --onedir \
  --console \
  --name "$DIST_NAME" \
  --hidden-import chatgpt_register_old \
  --collect-all curl_cffi \
  auto_pool_maintainer_mailtm.py

mkdir -p "dist/$DIST_NAME/logs"
cp "packaging/run_macos.sh" "dist/$DIST_NAME/run.sh"
cp "packaging/run_macos.command" "dist/$DIST_NAME/merge-mailtm.command"
chmod +x "dist/$DIST_NAME/run.sh"
chmod +x "dist/$DIST_NAME/merge-mailtm.command"

if [[ -f "config.json" ]]; then
  cp "config.json" "dist/$DIST_NAME/config.json"
fi

echo "macOS 打包完成: $ROOT_DIR/dist/$DIST_NAME"
