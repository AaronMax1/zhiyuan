#!/usr/bin/env python3
"""Report extraction coverage and validation status for vision score segments."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VISION_DIR = ROOT / "data-pipeline" / "output" / "vision_segments"
DEFAULT_EXTRACTED = VISION_DIR / "extracted_rows.csv"
DEFAULT_WORKLIST = VISION_DIR / "vision_worklist.csv"
DEFAULT_VALIDATION = VISION_DIR / "validation_report.json"
DEFAULT_REPORT = VISION_DIR / "status_report.md"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extracted", type=Path, default=DEFAULT_EXTRACTED)
    parser.add_argument("--worklist", type=Path, default=DEFAULT_WORKLIST)
    parser.add_argument("--validation", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    work_counts = read_work_counts(args.worklist)
    extracted = read_extracted(args.extracted)
    review_counts = read_review_counts(args.validation)
    lines = build_report(work_counts, extracted, review_counts)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.output.relative_to(ROOT))


def read_work_counts(path: Path) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            counts[(row["province"], row["category"])] += 1
    return dict(counts)


def read_extracted(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    if not path.exists():
        return groups
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not row.get("score"):
                continue
            parsed = dict(row)
            parsed["score"] = int(str(row["score"]).replace(",", ""))
            groups[(row["province"], row["category"])].append(parsed)
    return groups


def read_review_counts(path: Path) -> dict[tuple[str, str], int]:
    if not path.exists():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in report.get("review_rows", []):
        counts[(row["province"], row["category"])] += 1
    return dict(counts)


def build_report(
    work_counts: dict[tuple[str, str], int],
    extracted: dict[tuple[str, str], list[dict[str, Any]]],
    review_counts: dict[tuple[str, str], int],
) -> list[str]:
    lines = [
        "# 2025 一分一段视觉抽取状态",
        "",
        "| 省份 | 科类 | 切片数 | 行数 | 分数范围 | 省略分数 | 校验问题 | 状态 |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for key in sorted(work_counts):
        rows = extracted.get(key, [])
        scores = sorted({row["score"] for row in rows}, reverse=True)
        if scores:
            min_score, max_score = min(scores), max(scores)
            missing = [
                score for score in range(max_score, min_score - 1, -1)
                if score not in set(scores)
            ]
            score_range = f"{max_score}-{min_score}"
            status = "待复核" if review_counts.get(key, 0) else "已抽取"
        else:
            missing = []
            score_range = "-"
            status = "待抽取"
        lines.append(
            "| "
            + " | ".join([
                key[0],
                key[1],
                str(work_counts[key]),
                str(len(scores)),
                score_range,
                str(len(missing)),
                str(review_counts.get(key, 0)),
                status,
            ])
            + " |"
        )
    return lines


if __name__ == "__main__":
    main()
