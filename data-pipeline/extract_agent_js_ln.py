import csv
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "data-pipeline/output/vision_segments/vision_worklist.csv"
OUT = ROOT / "data-pipeline/output/vision_segments/agent_extracts/agent_js_ln.csv"
SWIFT = ROOT / "data-pipeline/vision_text_dump.swift"
VENDOR = ROOT / "gaokao-volunteer-app/.vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

TARGETS = {
    ("江苏", "历史类"),
    ("江苏", "物理类"),
    ("辽宁", "历史类"),
    ("辽宁", "物理类"),
}

MANUAL_ROWS = [
    {
        "province": "江苏",
        "category": "历史类",
        "year": "2025",
        "score": "600",
        "same_score_count": "238",
        "cumulative_rank": "5796",
        "source_slice": "data-pipeline/output/vision_segments/slices/2025/江苏/历史类/148855_历史类/002_4e022a3355/slice_002.png",
        "confidence": "high",
        "notes": "manual_vision_fill",
    },
    {
        "province": "江苏",
        "category": "物理类",
        "year": "2025",
        "score": "665",
        "same_score_count": "110",
        "cumulative_rank": "1257",
        "source_slice": "data-pipeline/output/vision_segments/slices/2025/江苏/物理类/148854_物理类/002_0dc119e103/slice_002.png",
        "confidence": "high",
        "notes": "manual_vision_fill",
    },
    *[
        {
            "province": "辽宁",
            "category": "物理类",
            "year": "2025",
            "score": str(score),
            "same_score_count": str(same),
            "cumulative_rank": str(cumulative),
            "source_slice": source_slice,
            "confidence": "high",
            "notes": "manual_vision_fill",
        }
        for score, same, cumulative, source_slice in [
            (389, 407, 109662, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
            (371, 379, 116642, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
            (349, 315, 123913, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
            (300, 152, 135097, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
            (269, 98, 139336, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
            (260, 103, 140237, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
            (246, 48, 141303, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_004.png"),
            (237, 55, 141814, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_004.png"),
            (219, 30, 142583, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_005.png"),
            (206, 18, 142937, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_004.png"),
            (197, 22, 143076, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_004.png"),
            (157, 2, 143354, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_004.png"),
        ]
    ],
]

LIAONING_PDFS = {
    "历史类": ROOT / "data-pipeline/raw/score_segments/official_pdf/2025/辽宁/历史类.pdf",
    "物理类": ROOT / "data-pipeline/raw/score_segments/official_pdf/2025/辽宁/物理类.pdf",
}

LIAONING_SLICE_BY_SCORE = {
    "历史类": [
        (517, "data-pipeline/output/vision_segments/slices/2025/辽宁/历史类/148776_历史类/002_d34be4885f/slice_001.png"),
        (365, "data-pipeline/output/vision_segments/slices/2025/辽宁/历史类/148776_历史类/002_d34be4885f/slice_002.png"),
        (213, "data-pipeline/output/vision_segments/slices/2025/辽宁/历史类/148776_历史类/002_d34be4885f/slice_003.png"),
        (150, "data-pipeline/output/vision_segments/slices/2025/辽宁/历史类/148776_历史类/002_d34be4885f/slice_004.png"),
    ],
    "物理类": [
        (555, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_001.png"),
        (403, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_002.png"),
        (251, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_003.png"),
        (150, "data-pipeline/output/vision_segments/slices/2025/辽宁/物理类/148775_物理类/002_52e6f83027/slice_004.png"),
    ],
}


def clean_token(text: str) -> str:
    text = text.strip()
    text = text.replace(",", "").replace("，", "")
    text = text.replace("O", "0").replace("o", "0")
    return text


def clean_pdf_cell(text: str) -> list[str]:
    text = (text or "").replace(",", "")
    text = re.sub(r"[^\d\s]", "", text)
    return [part for part in text.split() if part]


def parse_score(text: str) -> Optional[int]:
    text = clean_token(text)
    if re.fullmatch(r"\d{2,3}(?:及以上)?", text) is None:
        return None
    value = int(re.match(r"\d{2,3}", text).group())
    if 100 <= value <= 750:
        return value
    return None


def parse_int(text: str) -> Optional[int]:
    text = clean_token(text)
    if not re.fullmatch(r"\d{1,7}", text):
        return None
    return int(text)


def source_slice_for_liaoning(category: str, score: int) -> str:
    for lower, path in LIAONING_SLICE_BY_SCORE[category]:
        if score >= lower:
            return path
    return LIAONING_SLICE_BY_SCORE[category][-1][1]


def parse_liaoning_pdf(category: str) -> list[dict]:
    import pdfplumber

    rows: dict[int, dict] = {}
    pdf_path = LIAONING_PDFS[category]
    if not pdf_path.exists():
        return []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for table_row in table[1:]:
                    for cell in table_row:
                        nums = clean_pdf_cell(cell)
                        if len(nums) < 3:
                            continue
                        score = int(nums[0])
                        same = int(nums[1])
                        cumulative = int("".join(nums[2:]))
                        if not (100 <= score <= 750 and same >= 0 and cumulative >= same):
                            continue
                        rows[score] = {
                            "province": "辽宁",
                            "category": category,
                            "year": "2025",
                            "score": str(score),
                            "same_score_count": str(same),
                            "cumulative_rank": str(cumulative),
                            "source_slice": source_slice_for_liaoning(category, score),
                            "confidence": "high",
                            "notes": "pdf_text_crosscheck",
                        }
    return list(rows.values())


FIELD_CENTERS_BY_PROVINCE = {
    "江苏": [0.076, 0.168, 0.250, 0.403, 0.489, 0.567, 0.723, 0.810, 0.884],
    "辽宁": [0.082, 0.160, 0.236, 0.323, 0.398, 0.472, 0.562, 0.636, 0.710, 0.799, 0.875, 0.949],
}


def dump_tsv(slice_path: Path) -> list[tuple[float, float, str]]:
    result = subprocess.run(
        ["swift", str(SWIFT), str(slice_path)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
        env={**__import__("os").environ, "VISION_TSV": "1"},
    )
    out = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            out.append((float(parts[0]), float(parts[1]), parts[2].strip()))
    return out


def nearest_field(x: float, centers: list[float]) -> Optional[int]:
    indexed = min(enumerate(centers), key=lambda item: abs(item[1] - x))
    if abs(indexed[1] - x) <= 0.035:
        return indexed[0]
    return None


def group_cells(cells: list[tuple[float, float, str]]) -> list[list[tuple[float, float, str]]]:
    rows: list[list[tuple[float, float, str]]] = []
    for cell in sorted(cells, key=lambda item: -item[0]):
        y, _, text = cell
        if any(word in text for word in ["分数段", "同分人数", "累计人数", "统计表", "本次公布", "第", "页", "共"]):
            continue
        if parse_score(text) is None and parse_int(text) is None:
            continue
        for row in rows:
            if abs(row[0][0] - y) <= 0.016:
                row.append(cell)
                break
        else:
            rows.append([cell])
    return rows


def extract_rows(cells: list[tuple[float, float, str]], province: str, category: str, slice_path: str) -> list[dict]:
    centers = FIELD_CENTERS_BY_PROVINCE[province]
    rows: list[dict] = []
    for visual_row in group_cells(cells):
        fields: dict[int, str] = {}
        for _, x, text in visual_row:
            field = nearest_field(x, centers)
            if field is None:
                continue
            # Keep the text closest to the expected field center if OCR split one row.
            old = fields.get(field)
            if old is None or abs(centers[field] - x) < 0.02:
                fields[field] = text
        for base in range(0, len(centers), 3):
            score = parse_score(fields.get(base, ""))
            same = parse_int(fields.get(base + 1, ""))
            cumulative = parse_int(fields.get(base + 2, ""))
            if score is None or same is None or cumulative is None:
                continue
            rows.append(
                    {
                        "province": province,
                        "category": category,
                        "year": "2025",
                        "score": str(score),
                        "same_score_count": str(same),
                        "cumulative_rank": str(cumulative),
                        "source_slice": slice_path,
                        "confidence": "high",
                        "notes": "",
                    }
                )
    return rows


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
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
    by_key: dict[tuple[str, str, str], dict] = {}
    with WORKLIST.open(encoding="utf-8-sig", newline="") as f:
        for item in csv.DictReader(f):
            province = item["province"]
            category = item["category"]
            if (province, category) not in TARGETS:
                continue
            slice_path = item["slice_path"]
            cells = dump_tsv(ROOT / slice_path)
            for row in extract_rows(cells, province, category, slice_path):
                key = (province, category, row["score"])
                existing = by_key.get(key)
                if existing and (
                    existing["same_score_count"] != row["same_score_count"]
                    or existing["cumulative_rank"] != row["cumulative_rank"]
                ):
                    existing["confidence"] = "medium"
                    existing["notes"] = "duplicate_slice_conflict"
                    continue
                by_key[key] = row

    for row in MANUAL_ROWS:
        by_key[(row["province"], row["category"], row["score"])] = row

    for category in ("历史类", "物理类"):
        for row in parse_liaoning_pdf(category):
            by_key[(row["province"], row["category"], row["score"])] = row

    rows = sorted(
        by_key.values(),
        key=lambda r: (r["province"], r["category"], -int(r["score"])),
    )
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
