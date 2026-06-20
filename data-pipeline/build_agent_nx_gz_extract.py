#!/usr/bin/env python3
import csv
import re
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data-pipeline/output/vision_segments/agent_extracts/agent_nx_gz.csv"
WORKLIST = ROOT / "data-pipeline/output/vision_segments/vision_worklist.csv"

PDFS = {
    ("宁夏", "历史类"): Path("/tmp/ocrtest/nx_pdf/nx_history.pdf"),
    ("宁夏", "物理类"): Path("/tmp/ocrtest/nx_pdf/nx_physics.pdf"),
    ("贵州", "历史类"): Path("/tmp/ocrtest/gz_pdf/gz_history.pdf"),
    ("贵州", "物理类"): Path("/tmp/ocrtest/gz_pdf/gz_physics.pdf"),
}


def read_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def load_slices():
    result = {}
    with WORKLIST.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["province"], row["category"])
            if key in PDFS:
                result.setdefault(key, {})[int(row["slice_order"])] = row["slice_path"]
    return result


def source_slice(province: str, category: str, score: int, slices: dict) -> str:
    paths = slices[(province, category)]
    if len(paths) == 1:
        return paths[1]
    if province == "贵州":
        if category == "物理类":
            return paths[1] if score >= 344 else paths[2]
        return paths[1] if score >= 354 else paths[2]
    if province == "宁夏":
        if category == "历史类":
            bottom = [(562, 548), (493, 479), (424, 410), (355, 341), (286, 272), (217, 203)]
        else:
            bottom = [(587, 573), (516, 502), (445, 431), (374, 360), (303, 289), (232, 218), (161, 150)]
        return paths[2] if any(lo <= score <= hi for hi, lo in bottom) else paths[1]
    return paths[1]


def parse_ningxia(text: str):
    pairs = [(int(score), int(cum)) for score, cum in re.findall(r"(\d{1,3})分以上\s+(\d+)", text)]
    rows = {}
    for score, cum in pairs:
        rows[score] = cum
    sorted_scores = sorted(rows, reverse=True)
    out = []
    prev = 0
    for score in sorted_scores:
        cum = rows[score]
        out.append((score, cum - prev, cum))
        prev = cum
    return out


def parse_guizhou(text: str):
    rows = []
    pos = 0
    while True:
        m = re.search(r"分数\s+(.+?)\n本段人数\n累计人数\n累计比例%", text[pos:], re.S)
        if not m:
            break
        score_line = m.group(1).replace("及以上", "")
        scores = [int(x) for x in re.findall(r"\d{1,3}", score_line)]
        block_start = pos + m.end()
        next_m = re.search(r"\n分数\s+", text[block_start:])
        block_end = block_start + next_m.start() if next_m else len(text)
        nums = re.findall(r"\d+(?:\.\d+)?", text[block_start:block_end])
        if len(nums) < len(scores) * 3:
            raise ValueError(f"not enough numbers for scores {scores[:3]}... got {len(nums)}")
        for idx, score in enumerate(scores):
            same = int(nums[idx * 3])
            cum = int(nums[idx * 3 + 1])
            rows.append((score, same, cum))
        pos = block_end
    dedup = {}
    for score, same, cum in rows:
        dedup[score] = (same, cum)
    return [(s, *dedup[s]) for s in sorted(dedup, reverse=True)]


def validate(label, rows):
    province, _category = label
    problems = []
    for i, (score, same, cum) in enumerate(rows):
        if province == "宁夏" and i and score != rows[i - 1][0] - 1:
            problems.append(f"score gap after {rows[i - 1][0]} before {score}")
        expected = cum if i == 0 else cum - rows[i - 1][2]
        if same != expected:
            problems.append(f"{score}: same={same}, diff={expected}")
    return problems


def main():
    slices = load_slices()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_rows = []
    reports = []
    for key, pdf in PDFS.items():
        province, category = key
        text = read_text(pdf)
        parsed = parse_ningxia(text) if province == "宁夏" else parse_guizhou(text)
        problems = validate(key, parsed)
        if problems:
            raise SystemExit(f"{key} validation failed: {problems[:20]}")
        reports.append((province, category, parsed[-1][0], parsed[0][0], len(parsed)))
        for score, same, cum in parsed:
            all_rows.append({
                "province": province,
                "category": category,
                "year": "2025",
                "score": score,
                "same_score_count": same,
                "cumulative_rank": cum,
                "source_slice": source_slice(province, category, score, slices),
                "confidence": "high",
                "notes": "",
            })
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "province", "category", "year", "score", "same_score_count",
            "cumulative_rank", "source_slice", "confidence", "notes",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    for r in reports:
        print(",".join(map(str, r)))
    print("wrote", OUT, len(all_rows))


if __name__ == "__main__":
    main()
