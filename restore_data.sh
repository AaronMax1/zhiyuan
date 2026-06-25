#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$ROOT_DIR/data-bundles"
ARCHIVE="$BUNDLE_DIR/hebei-runtime-data.tar.zst"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Missing Hebei runtime data bundle: $ARCHIVE" >&2
  exit 1
fi

if ! command -v zstd >/dev/null 2>&1; then
  echo "zstd is required to restore database bundle." >&2
  echo "macOS: brew install zstd" >&2
  echo "Ubuntu/Debian: sudo apt-get install zstd" >&2
  exit 1
fi

echo "Restoring runtime databases..."
mkdir -p "$ROOT_DIR/data-pipeline/output" "$ROOT_DIR/gaokao-volunteer-app/data"
zstd -dc "$ARCHIVE" | tar -xf - -C "$ROOT_DIR"

echo "Restored:"
ls -lh \
  "$ROOT_DIR/data-pipeline/output/hebei_lnwc_loggedin.db" \
  "$ROOT_DIR/data-pipeline/output/hebei_score_segments.db" \
  "$ROOT_DIR/data-pipeline/output/hebei_2026_plan.db" \
  "$ROOT_DIR/data-pipeline/output/batch_control_lines.db"
