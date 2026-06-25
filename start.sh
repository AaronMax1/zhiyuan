#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/gaokao-volunteer-app"
PORT="${PORT:-8000}"

if [[ ! -f "$ROOT_DIR/data-pipeline/output/hebei_lnwc_loggedin.db" || ! -f "$ROOT_DIR/data-pipeline/output/hebei_score_segments.db" || ! -f "$ROOT_DIR/data-pipeline/output/hebei_2026_plan.db" || ! -f "$ROOT_DIR/data-pipeline/output/batch_control_lines.db" ]]; then
  echo "Runtime databases are missing. Restoring from data-bundles..."
  "$ROOT_DIR/restore_data.sh"
fi

echo "Starting Gaokao volunteer app on http://localhost:$PORT"
cd "$APP_DIR"
PORT="$PORT" python3 app.py
