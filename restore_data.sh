#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE_DIR="$ROOT_DIR/data-bundles"
FIRST_PART="$BUNDLE_DIR/gaokao-runtime-data.tar.zst.part-aa"
TMP_ARCHIVE="${TMPDIR:-/tmp}/gaokao-runtime-data.tar.zst"

if [[ ! -f "$FIRST_PART" ]]; then
  echo "Missing data bundle parts under: $BUNDLE_DIR" >&2
  exit 1
fi

if ! command -v zstd >/dev/null 2>&1; then
  echo "zstd is required to restore database bundle." >&2
  echo "macOS: brew install zstd" >&2
  echo "Ubuntu/Debian: sudo apt-get install zstd" >&2
  exit 1
fi

echo "Combining database bundle parts..."
cat "$BUNDLE_DIR"/gaokao-runtime-data.tar.zst.part-* > "$TMP_ARCHIVE"

echo "Restoring runtime databases..."
mkdir -p "$ROOT_DIR/data-pipeline/output" "$ROOT_DIR/gaokao-volunteer-app/data"
zstd -dc "$TMP_ARCHIVE" | tar -xf - -C "$ROOT_DIR"

echo "Restored:"
ls -lh \
  "$ROOT_DIR/data-pipeline/output/unified_admission.db" \
  "$ROOT_DIR/data-pipeline/output/score_segments.db" \
  "$ROOT_DIR/data-pipeline/output/batch_control_lines.db" \
  "$ROOT_DIR/gaokao-volunteer-app/data/admission_clean.db.gz"
