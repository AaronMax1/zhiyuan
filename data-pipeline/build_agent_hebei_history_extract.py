#!/usr/bin/env python3
"""Build 2025 Hebei history score segment extract from cropped table images."""

from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "data-pipeline" / "vision_text_dump.swift"
CROP_DIR = ROOT / "data-pipeline" / "output" / "vision_segments" / "hebei_history_crops" / "stitched"
OUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "agent_extracts" / "agent_hebei_history.csv"

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
    rows_by_score: dict[int, dict[str, str]] = {}
    for image in sorted(CROP_DIR.glob("*_hist_*.png")):
        for score, count, read_cumulative in parse_image(image):
            if score > 672 or score < 140:
                continue
            existing = rows_by_score.get(score)
            if existing and existing["same_score_count"] != str(count):
                existing["confidence"] = "medium"
                existing["notes"] = append_note(existing["notes"], f"conflict_count={count}")
                continue
            rows_by_score[score] = {
                "province": "河北",
                "category": "历史类",
                "year": "2025",
                "score": str(score),
                "same_score_count": str(count),
                "cumulative_rank": str(read_cumulative or 0),
                "source_slice": str(image.relative_to(ROOT)),
                "confidence": "high",
                "notes": "hebeea_pdf_image_ocr",
            }

    missing = [score for score in range(672, 139, -1) if score not in rows_by_score]
    if missing:
        raise RuntimeError(f"missing scores: {missing[:50]}")

    cumulative = 0
    output = []
    for score in range(672, 139, -1):
        row = rows_by_score[score]
        read_cumulative = int(row["cumulative_rank"])
        if score == 672 and read_cumulative:
            cumulative = read_cumulative
            row["notes"] = append_note(row["notes"], "official_top_row=672及以上")
        else:
            cumulative += int(row["same_score_count"])
        if read_cumulative and abs(read_cumulative - cumulative) > 2:
            row["confidence"] = "medium"
            row["notes"] = append_note(row["notes"], f"ocr_cumulative={read_cumulative}")
        row["cumulative_rank"] = str(cumulative)
        output.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output)
    print(f"Wrote {OUT.relative_to(ROOT)} rows={len(output)}")


def parse_image(path: Path) -> list[tuple[int, int, int | None]]:
    cells = dump_tsv(path)
    visual_rows: list[list[tuple[float, float, str]]] = []
    for y, x, text in sorted(cells, key=lambda item: -item[0]):
        if parse_score(text) is None and parse_intish(text) is None:
            continue
        for row in visual_rows:
            if abs(row[0][0] - y) <= 0.018:
                row.append((y, x, text))
                break
        else:
            visual_rows.append([(y, x, text)])

    parsed = []
    for row in visual_rows:
        score_parts, count_parts, cumulative_parts = [], [], []
        for _, x, text in row:
            if x < 0.30:
                score_parts.append(text)
            elif x < 0.78:
                count_parts.append(text)
            else:
                cumulative_parts.append(text)
        score = parse_score("".join(score_parts))
        count = parse_intish("".join(count_parts))
        cumulative = parse_intish("".join(cumulative_parts))
        if score is None or count is None:
            continue
        parsed.append((score, count, cumulative))
    return parsed


def dump_tsv(path: Path) -> list[tuple[float, float, str]]:
    result = subprocess.run(
        ["swift", str(SWIFT), str(path)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        env={**__import__("os").environ, "VISION_TSV": "1"},
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            rows.append((float(parts[0]), float(parts[1]), parts[2].strip()))
    return rows


def parse_score(text: str) -> int | None:
    match = re.search(r"\d{3}", text.replace("及以上", ""))
    if not match:
        return None
    value = int(match.group())
    return value if 100 <= value <= 750 else None


def parse_intish(text: str) -> int | None:
    text = (
        text.replace(",", "")
        .replace("，", "")
        .replace("O", "0")
        .replace("o", "0")
        .replace("]", "1")
        .replace("］", "1")
        .replace("|", "1")
        .replace("l", "1")
        .replace("I", "1")
    )
    digits = re.findall(r"\d+", text)
    if not digits:
        return None
    return int("".join(digits))


def append_note(notes: str, note: str) -> str:
    parts = [part for part in notes.split(";") if part]
    if note not in parts:
        parts.append(note)
    return ";".join(parts)


if __name__ == "__main__":
    main()
