#!/usr/bin/env python3
"""Merge per-agent vision extraction CSV files into the master extraction CSV."""

from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VISION_DIR = ROOT / "data-pipeline" / "output" / "vision_segments"
DEFAULT_MASTER = VISION_DIR / "extracted_rows.csv"
DEFAULT_AGENT_DIR = VISION_DIR / "agent_extracts"

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
    parser.add_argument("--master", type=Path, default=DEFAULT_MASTER)
    parser.add_argument("--agent-dir", type=Path, default=DEFAULT_AGENT_DIR)
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Specific agent CSV files to merge. Defaults to *.complete.csv in --agent-dir.",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    rows = read_csv(args.master) if args.master.exists() else []
    before = len(rows)
    seen = row_keys(rows)

    agent_files = args.files or sorted(args.agent_dir.glob("*.complete.csv"))
    imported = 0
    skipped = 0
    for path in agent_files:
        for row in read_csv(path):
            normalized = normalize_row(row, path)
            key = (
                normalized["province"],
                normalized["category"],
                normalized["year"],
                normalized["score"],
            )
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            rows.append(normalized)
            imported += 1

    rows.sort(key=lambda r: (r["province"], r["category"], int(r["year"]), -int(r["score"])))
    if not args.no_backup and args.master.exists():
        backup = args.master.with_suffix(
            args.master.suffix + "." + datetime.now().strftime("%Y%m%d%H%M%S") + ".bak"
        )
        shutil.copy2(args.master, backup)
        print(f"Backup: {backup.relative_to(ROOT)}")

    write_csv(args.master, rows)
    print(f"Agent files: {len(agent_files)}")
    print(f"Before: {before}")
    print(f"Imported: {imported}")
    print(f"Skipped duplicates: {skipped}")
    print(f"After: {len(rows)}")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("score")]


def row_keys(rows: list[dict[str, str]]) -> set[tuple[str, str, str, str]]:
    return {
        (row["province"], row["category"], str(row["year"]), str(row["score"]))
        for row in rows
        if row.get("province") and row.get("category") and row.get("year") and row.get("score")
    }


def normalize_row(row: dict[str, str], source_file: Path) -> dict[str, str]:
    normalized = {field: str(row.get(field, "")).strip() for field in FIELDS}
    normalized["year"] = digits(normalized["year"])
    normalized["score"] = digits(normalized["score"])
    normalized["same_score_count"] = digits(normalized["same_score_count"])
    normalized["cumulative_rank"] = digits(normalized["cumulative_rank"])
    if not normalized["source_slice"]:
        normalized["source_slice"] = source_file.name
    missing = [field for field in FIELDS[:6] if not normalized[field]]
    if missing:
        raise ValueError(f"{source_file}: missing {missing} in row {row}")
    return normalized


def digits(value: str) -> str:
    return value.replace(",", "").replace("，", "").strip()


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
