#!/usr/bin/env python3
"""Normalize low-risk metadata in the vision extraction CSV."""

from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "extracted_rows.csv"

FIELDS = [
    "province",
    "category",
    "year",
    "score",
    "same_score_count",
    "cumulative_rank",
    "source_slice",
    "confidence",
    "notes",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    rows = read_rows(args.input)
    changed = 0
    for row in rows:
        before = row.get("notes", "")
        row["notes"] = normalize_notes(before)
        if row["notes"] != before:
            changed += 1

    if not args.no_backup:
        backup = args.input.with_suffix(
            args.input.suffix + "." + datetime.now().strftime("%Y%m%d%H%M%S") + ".bak"
        )
        shutil.copy2(args.input, backup)
        print(f"Backup: {backup.relative_to(ROOT)}")

    write_rows(args.input, rows)
    print(f"Rows: {len(rows)}")
    print(f"Metadata rows changed: {changed}")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f)]


def normalize_notes(value: str) -> str:
    tokens = []
    seen = set()
    for token in (part.strip() for part in value.split(";")):
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return ";".join(tokens)


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
