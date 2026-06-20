#!/usr/bin/env python3
"""Build independent Sichuan 2025 score-segment extract from EOL text tables."""

from __future__ import annotations

import csv
import html
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "data-pipeline" / "output" / "vision_segments" / "vision_worklist.csv"
OUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "agent_extracts" / "agent_sc.csv"
CACHE_DIR = ROOT / "data-pipeline" / "tmp_eol_sc"

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

SOURCES = {
    "历史类": "https://gaokao.eol.cn/si_chuan/dongtai/202507/t20250702_2678481.shtml",
    "物理类": "https://gaokao.eol.cn/si_chuan/dongtai/202507/t20250702_2678480.shtml",
}


def main() -> None:
    rows: list[dict[str, str | int]] = []
    for category, url in SOURCES.items():
        html_text = fetch(url, CACHE_DIR / f"{category}.html")
        category_rows = parse_table(html_text)
        slices = load_slices(category)
        if not slices:
            raise RuntimeError(f"no Sichuan worklist slices for {category}")
        rows.extend(with_metadata(category_rows, category, slices, url))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    report(rows)


def fetch(url: str, cache_path: Path) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-L",
        "--retry",
        "2",
        "-A",
        "Mozilla/5.0",
        url,
        "-o",
        str(cache_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cache_path.read_text(encoding="utf-8", errors="ignore")


def parse_table(html_text: str) -> list[dict[str, str | int]]:
    cells = [
        clean_cell(match)
        for match in re.findall(r"<td[^>]*>(.*?)</td>", html_text, flags=re.I | re.S)
    ]
    cells = [cell for cell in cells if cell]
    if cells[:3] != ["分数", "人数", "累计人数"]:
        raise RuntimeError(f"unexpected table header: {cells[:6]}")

    data_cells = cells[3:]
    if len(data_cells) % 3 != 0:
        raise RuntimeError(f"table cell count is not divisible by 3: {len(data_cells)}")

    rows: list[dict[str, str | int]] = []
    for i in range(0, len(data_cells), 3):
        label, same_count, cumulative = data_cells[i : i + 3]
        score, note = parse_score_label(label)
        rows.append(
            {
                "score": score,
                "same_score_count": parse_int(same_count),
                "cumulative_rank": parse_int(cumulative),
                "notes": note,
            }
        )
    return rows


def clean_cell(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_score_label(label: str) -> tuple[int, str]:
    normalized = label.strip()
    if "-" in normalized:
        start, end = normalized.split("-", 1)
        return parse_int(start), f"官方合并高分段 {normalized}"
    score_match = re.search(r"\d+", normalized)
    if not score_match:
        raise RuntimeError(f"cannot parse score label: {label}")
    score = int(score_match.group(0))
    note = ""
    if normalized != str(score):
        note = f"原始分数标签 {normalized}"
    return score, note


def parse_int(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        raise RuntimeError(f"cannot parse integer: {value}")
    return int(digits)


def load_slices(category: str) -> list[str]:
    with WORKLIST.open(encoding="utf-8-sig", newline="") as f:
        rows = [
            row["slice_path"]
            for row in csv.DictReader(f)
            if row["province"] == "四川" and row["category"] == category
        ]
    missing = [path for path in rows if not (ROOT / path).exists()]
    if missing:
        raise RuntimeError(f"missing slice files for {category}: {missing[:3]}")
    return rows


def with_metadata(
    rows: list[dict[str, str | int]],
    category: str,
    slices: list[str],
    source_url: str,
) -> list[dict[str, str | int]]:
    enriched = []
    total = len(rows)
    for index, row in enumerate(rows):
        slice_index = min(index * len(slices) // total, len(slices) - 1)
        notes = str(row["notes"])
        source_note = "EOL文本层，来源标注四川省教育考试院"
        if notes:
            notes = f"{notes}; {source_note}; {source_url}"
        else:
            notes = f"{source_note}; {source_url}"
        enriched.append(
            {
                "province": "四川",
                "category": category,
                "year": 2025,
                "score": row["score"],
                "same_score_count": row["same_score_count"],
                "cumulative_rank": row["cumulative_rank"],
                "source_slice": slices[slice_index],
                "confidence": "high",
                "notes": notes,
            }
        )
    return enriched


def report(rows: list[dict[str, str | int]]) -> None:
    failed = False
    for category in SOURCES:
        group = [row for row in rows if row["category"] == category]
        scores = [int(row["score"]) for row in group]
        missing_scores = [
            score
            for score in range(max(scores), min(scores) - 1, -1)
            if score not in set(scores)
        ]
        diff_errors = []
        previous = None
        for row in sorted(group, key=lambda item: int(item["score"]), reverse=True):
            if previous is not None:
                expected = int(row["cumulative_rank"]) - int(previous["cumulative_rank"])
                if expected != int(row["same_score_count"]):
                    diff_errors.append((row["score"], row["same_score_count"], expected))
            previous = row
        uncertain = [row for row in group if row["confidence"] != "high"]
        print(
            f"{category}: rows={len(group)} range={min(scores)}-{max(scores)} "
            f"continuous={not missing_scores} diff_ok={not diff_errors} uncertain={len(uncertain)}"
        )
        if missing_scores:
            print(f"  missing first 20: {missing_scores[:20]}")
        if diff_errors:
            print(f"  diff errors first 20: {diff_errors[:20]}")
        failed = failed or bool(missing_scores or diff_errors or uncertain)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
