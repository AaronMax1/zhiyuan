#!/usr/bin/env python3
"""Build independent 2025 Qinghai score segment extract from EOL text tables."""

from __future__ import annotations

import csv
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "data-pipeline" / "output" / "vision_segments" / "vision_worklist.csv"
OUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "agent_extracts" / "agent_qh.csv"

FIELDNAMES = [
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

SOURCES = {
    "历史类": "https://gaokao.eol.cn/qing_hai/dongtai/202506/t20250625_2677002.shtml",
    "物理类": "https://gaokao.eol.cn/qing_hai/dongtai/202506/t20250625_2677001.shtml",
}

SLICE_SCORE_RANGES = {
    "历史类": [
        ("002_2b07da7b89/slice_001.png", 637, 582),
        ("002_2b07da7b89/slice_002.png", 581, 537),
        ("002_2b07da7b89/slice_003.png", 536, 492),
        ("002_2b07da7b89/slice_004.png", 491, 447),
        ("002_2b07da7b89/slice_005.png", 446, 402),
        ("003_d6e2be6969/slice_001.png", 401, 357),
        ("003_d6e2be6969/slice_002.png", 356, 312),
        ("003_d6e2be6969/slice_003.png", 311, 267),
        ("003_d6e2be6969/slice_004.png", 266, 222),
        ("003_d6e2be6969/slice_005.png", 221, 177),
        ("003_d6e2be6969/slice_006.png", 176, 0),
    ],
    "物理类": [
        ("002_0b392f5f9d/slice_001.png", 661, 612),
        ("002_0b392f5f9d/slice_002.png", 611, 567),
        ("002_0b392f5f9d/slice_003.png", 566, 522),
        ("002_0b392f5f9d/slice_004.png", 521, 477),
        ("003_116f24f25d/slice_001.png", 476, 432),
        ("003_116f24f25d/slice_002.png", 431, 387),
        ("003_116f24f25d/slice_003.png", 386, 342),
        ("003_116f24f25d/slice_004.png", 341, 297),
        ("003_116f24f25d/slice_005.png", 296, 252),
        ("003_116f24f25d/slice_006.png", 251, 203),
        ("003_116f24f25d/slice_007.png", 202, 0),
    ],
}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_tr = False
        self.in_cell = False
        self.cell = ""
        self.row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.in_tr = True
            self.row = []
        elif tag in {"td", "th"} and self.in_tr:
            self.in_cell = True
            self.cell = ""

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.in_cell:
            self.row.append(" ".join(self.cell.split()))
            self.in_cell = False
        elif tag == "tr" and self.in_tr:
            if self.row:
                self.rows.append(self.row)
            self.in_tr = False


def fetch(url: str) -> str:
    result = subprocess.run(
        ["curl", "-L", "--compressed", "-A", "Mozilla/5.0", url],
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8", errors="ignore")


def parse_rows(html: str) -> list[dict[str, int | str]]:
    parser = TableParser()
    parser.feed(html)
    rows: list[dict[str, int | str]] = []
    for row in parser.rows:
        if len(row) < 3 or row[:3] == ["分数", "人数", "累计人数"]:
            continue
        score_text, count_text, cumulative_text = row[:3]
        if not re.fullmatch(r"\d+(?:-\d+)?", score_text):
            continue
        if not (count_text.isdigit() and cumulative_text.isdigit()):
            continue
        score = int(score_text.split("-", 1)[0])
        notes = f"顶部官方合并段 {score_text}" if "-" in score_text else ""
        rows.append(
            {
                "score": score,
                "same_score_count": int(count_text),
                "cumulative_rank": int(cumulative_text),
                "notes": notes,
            }
        )
    return rows


def load_slices() -> dict[str, list[str]]:
    by_category: dict[str, list[tuple[str, int, str]]] = {"历史类": [], "物理类": []}
    with WORKLIST.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["province"] != "青海" or row["category"] not in by_category:
                continue
            by_category[row["category"]].append(
                (row["source_image"], int(row["slice_order"]), row["slice_path"])
            )
    return {
        category: [path for _, _, path in sorted(items)]
        for category, items in by_category.items()
    }


def assign_slice(category: str, score: int, slices: list[str]) -> str:
    by_suffix = {"/".join(path.split("/")[-2:]): path for path in slices}
    for suffix, high, low in SLICE_SCORE_RANGES[category]:
        if high >= score >= low:
            return by_suffix.get(suffix, "")
    return ""


def validate(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row["province"], row["category"])
        groups.setdefault(key, []).append(row)

    for (province, category), group in groups.items():
        group = sorted(group, key=lambda r: int(r["score"]), reverse=True)
        scores = [int(r["score"]) for r in group]
        expected = list(range(scores[0], scores[-1] - 1, -1))
        if scores != expected:
            missing = sorted(set(expected) - set(scores), reverse=True)
            errors.append(f"{province}{category}: missing/non-contiguous {missing[:20]}")
        previous = 0
        for row in group:
            count = int(row["same_score_count"])
            cumulative = int(row["cumulative_rank"])
            if cumulative - previous != count:
                errors.append(
                    f"{province}{category} score {row['score']}: cumulative delta "
                    f"{cumulative - previous} != count {count}"
                )
            previous = cumulative
    return errors


def main() -> None:
    slices_by_category = load_slices()
    output_rows: list[dict[str, str]] = []
    for category, url in SOURCES.items():
        parsed = parse_rows(fetch(url))
        if not parsed:
            raise RuntimeError(f"No rows parsed for {category}")
        slices = slices_by_category[category]
        for parsed_row in parsed:
            output_rows.append(
                {
                    "province": "青海",
                    "category": category,
                    "year": "2025",
                    "score": str(parsed_row["score"]),
                    "same_score_count": str(parsed_row["same_score_count"]),
                    "cumulative_rank": str(parsed_row["cumulative_rank"]),
                    "source_slice": assign_slice(category, int(parsed_row["score"]), slices),
                    "confidence": "high",
                    "notes": str(parsed_row["notes"]),
                }
            )

    errors = validate(output_rows)
    if errors:
        raise RuntimeError("\n".join(errors[:30]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Wrote {OUT.relative_to(ROOT)} rows={len(output_rows)}")


if __name__ == "__main__":
    main()
