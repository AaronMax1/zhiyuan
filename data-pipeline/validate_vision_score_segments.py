#!/usr/bin/env python3
"""Validate manually/vision-extracted score segment CSV rows."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "extracted_rows.csv"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "validation_report.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = read_rows(args.input)
    checked = validate(rows)
    args.output.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review_csv(args.output.with_name("validation_review.csv"), checked["review_rows"])
    write_clean_csv(args.output.with_name("validated_rows.csv"), checked["valid_rows"])
    print(f"Rows: {len(rows)}")
    print(f"Valid: {len(checked['valid_rows'])}")
    print(f"Review: {len(checked['review_rows'])}")


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = []
        for row in csv.DictReader(f):
            if not row.get("score"):
                continue
            parsed = dict(row)
            for key in ("year", "score", "same_score_count", "cumulative_rank"):
                parsed[key] = int(str(row.get(key, "")).replace(",", "").strip())
            rows.append(parsed)
        return rows


def validate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["province"], row["category"], row["year"])].append(row)

    valid_rows = []
    review_rows = []
    summaries = []
    for (province, category, year), group_rows in sorted(groups.items()):
        group_rows = sorted(group_rows, key=lambda row: row["score"], reverse=True)
        previous = None
        for row in group_rows:
            flags = []
            if not (0 <= row["score"] <= 900):
                flags.append("invalid_score")
            if row["same_score_count"] < 0:
                flags.append("invalid_same_score_count")
            if row["cumulative_rank"] <= 0:
                flags.append("invalid_cumulative_rank")
            if previous:
                expected_delta = row["cumulative_rank"] - previous["cumulative_rank"]
                if row["score"] >= previous["score"]:
                    flags.append("score_not_descending")
                if expected_delta < 0:
                    flags.append("cumulative_not_ascending")
                elif abs(expected_delta - row["same_score_count"]) > max(2, int(row["same_score_count"] * 0.03)):
                    flags.append("same_count_cumulative_mismatch")
            row_with_flags = dict(row)
            row_with_flags["quality_flags"] = "|".join(flags)
            if flags:
                review_rows.append(row_with_flags)
            else:
                valid_rows.append(row_with_flags)
            previous = row
        summaries.append({
            "province": province,
            "category": category,
            "year": year,
            "rows": len(group_rows),
            "min_score": min(row["score"] for row in group_rows),
            "max_score": max(row["score"] for row in group_rows),
        })
    return {"summary": summaries, "valid_rows": valid_rows, "review_rows": review_rows}


def write_clean_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    write_csv(path, rows)


def write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    write_csv(path, rows)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "province", "category", "year", "score", "same_score_count",
        "cumulative_rank", "source_slice", "confidence", "notes", "quality_flags",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
