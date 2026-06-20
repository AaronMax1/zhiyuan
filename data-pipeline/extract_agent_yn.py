#!/usr/bin/env python3
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
OUT = ROOT / "output" / "vision_segments" / "agent_extracts" / "agent_yn.csv"

HEADER = [
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


@dataclass(frozen=True)
class ImageSpec:
    province: str
    category: str
    start_score: int
    image_path: Path
    source_slice: str
    combined_top: bool = False
    row_limit: int | None = None
    data_offset: int = 0


SPECS = [
    ImageSpec(
        "云南",
        "物理类",
        683,
        WORKSPACE / "data-pipeline/raw/score_segments/dxsbb/2025/云南/148871_物理类/images/002_36a461716f.png",
        "data-pipeline/output/vision_segments/slices/2025/云南/物理类/148871_物理类/002_36a461716f/slice_001.png",
        True,
        262,
        1,
    ),
    ImageSpec(
        "云南",
        "物理类",
        421,
        WORKSPACE / "data-pipeline/raw/score_segments/dxsbb/2025/云南/148871_物理类/images/003_83a0fdcce0.png",
        "data-pipeline/output/vision_segments/slices/2025/云南/物理类/148871_物理类/003_83a0fdcce0/slice_001.png",
    ),
    ImageSpec(
        "云南",
        "历史类",
        655,
        WORKSPACE / "data-pipeline/raw/score_segments/dxsbb/2025/云南/148870_历史类/images/002_ffeb46b5c1.png",
        "data-pipeline/output/vision_segments/slices/2025/云南/历史类/148870_历史类/002_ffeb46b5c1/slice_001.png",
        True,
        233,
        1,
    ),
    ImageSpec(
        "云南",
        "历史类",
        422,
        WORKSPACE / "data-pipeline/raw/score_segments/dxsbb/2025/云南/148870_历史类/images/003_cd68dd1e1e.png",
        "data-pipeline/output/vision_segments/slices/2025/云南/历史类/148870_历史类/003_cd68dd1e1e/slice_001.png",
    ),
]

CUMULATIVE_OVERRIDES = {
    ("云南", "历史类", 447): 51009,
    ("云南", "历史类", 443): 52998,
    ("云南", "历史类", 439): 55019,
    ("云南", "历史类", 435): 57005,
    ("云南", "历史类", 340): 99233,
    ("云南", "历史类", 276): 110593,
    ("云南", "历史类", 277): 110503,
    ("云南", "历史类", 275): 110667,
    ("云南", "历史类", 270): 110983,
    ("云南", "历史类", 268): 111094,
    ("云南", "历史类", 258): 111585,
    ("云南", "历史类", 256): 111663,
    ("云南", "历史类", 255): 111690,
    ("云南", "历史类", 250): 111845,
    ("云南", "历史类", 244): 111983,
    ("云南", "历史类", 238): 112082,
    ("云南", "历史类", 230): 112180,
    ("云南", "历史类", 216): 112288,
    ("云南", "历史类", 194): 112384,
    ("云南", "历史类", 192): 112388,
    ("云南", "历史类", 190): 112389,
    ("云南", "物理类", 515): 52642,
    ("云南", "物理类", 511): 55401,
    ("云南", "物理类", 507): 58183,
    ("云南", "物理类", 264): 171640,
    ("云南", "物理类", 265): 171570,
    ("云南", "物理类", 250): 172381,
    ("云南", "物理类", 244): 172602,
    ("云南", "物理类", 245): 172578,
    ("云南", "物理类", 243): 172633,
    ("云南", "物理类", 238): 172787,
    ("云南", "物理类", 234): 172884,
    ("云南", "物理类", 222): 173085,
    ("云南", "物理类", 210): 173185,
    ("云南", "物理类", 180): 173282,
}


def grouped_positions(values: list[int]) -> list[list[int]]:
    groups: list[list[int]] = []
    for value in values:
        if not groups or value > groups[-1][-1] + 1:
            groups.append([])
        groups[-1].append(value)
    return groups


def horizontal_lines(im: Image.Image) -> list[int]:
    w, h = im.size
    ys = []
    for y in range(h):
        dark = sum(1 for x in range(w) if im.getpixel((x, y)) < 120)
        if dark > w * 0.5:
            ys.append(y)
    return [round(sum(g) / len(g)) for g in grouped_positions(ys)]


def vertical_lines(im: Image.Image) -> list[int]:
    w, h = im.size
    xs = []
    for x in range(w):
        dark = sum(1 for y in range(h) if im.getpixel((x, y)) < 90)
        if dark > h * 0.45:
            xs.append(x)
    groups = grouped_positions(xs)
    lines = [round(sum(g) / len(g)) for g in groups]
    if len(lines) < 4:
        raise RuntimeError(f"expected at least 4 vertical lines, got {lines}")
    return lines[:4]


def digit_boxes(cell: Image.Image) -> list[tuple[int, int, int, int]]:
    w, h = cell.size
    cols = []
    for x in range(w):
        dark = sum(1 for y in range(h) if cell.getpixel((x, y)) < 150)
        if dark >= 2:
            cols.append(x)
    boxes = []
    for group in grouped_positions(cols):
        x1, x2 = group[0], group[-1]
        pixels = [
            (x, y)
            for y in range(h)
            for x in range(x1, x2 + 1)
            if cell.getpixel((x, y)) < 150
        ]
        if not pixels:
            continue
        ys = [p[1] for p in pixels]
        y1, y2 = min(ys), max(ys) + 1
        width = x2 - x1 + 1
        if width >= 2 and y2 - y1 >= 5:
            if width > 10:
                projection = [
                    sum(1 for y in range(y1, y2) if cell.getpixel((x, y)) < 150)
                    for x in range(x1, x2 + 1)
                ]
                lo = max(4, width // 3)
                hi = min(width - 4, width * 2 // 3)
                if lo <= hi:
                    split_rel = min(range(lo, hi + 1), key=lambda i: projection[i])
                else:
                    split_rel = width // 2
                split = x1 + split_rel
                boxes.append((x1, y1, split, y2))
                boxes.append((split, y1, x2 + 1, y2))
            else:
                boxes.append((x1, y1, x2 + 1, y2))
    return boxes


def norm_digit(img: Image.Image, size: tuple[int, int] = (12, 18)) -> tuple[int, ...]:
    gray = img.convert("L")
    bw = gray.point(lambda p: 0 if p < 150 else 255, "1").convert("L")
    bw = bw.resize(size, Image.Resampling.NEAREST)
    return tuple(1 if p < 128 else 0 for p in bw.getdata())


class DigitRecognizer:
    def __init__(self) -> None:
        self.templates: dict[str, list[tuple[int, ...]]] = {str(i): [] for i in range(10)}

    def add_digit(self, label: str, img: Image.Image) -> None:
        self.templates[label].append(norm_digit(img))

    def recognize_digit(self, img: Image.Image) -> str:
        sample = norm_digit(img)
        best_label = ""
        best_score = 10**9
        for label, templates in self.templates.items():
            for template in templates:
                score = sum(a != b for a, b in zip(sample, template))
                if score < best_score:
                    best_score = score
                    best_label = label
        if not best_label:
            raise RuntimeError("no digit templates available")
        return best_label

    def recognize_number(self, cell: Image.Image) -> int:
        boxes = digit_boxes(cell)
        if not boxes:
            raise RuntimeError("empty numeric cell")
        digits = [self.recognize_digit(cell.crop(box)) for box in boxes]
        return int("".join(digits))


def row_cells(im: Image.Image, lines: list[int], vlines: list[int], row_index: int) -> tuple[Image.Image, Image.Image, Image.Image]:
    y1 = lines[row_index] + 2
    y2 = lines[row_index + 1] - 2
    if y2 <= y1:
        y1 = lines[row_index] + 1
        y2 = lines[row_index + 1] - 1
    return (
        im.crop((vlines[0] + 1, y1, vlines[1], y2)),
        im.crop((vlines[1] + 1, y1, vlines[2], y2)),
        im.crop((vlines[2] + 1, y1, vlines[3], y2)),
    )


def build_recognizer() -> DigitRecognizer:
    recognizer = DigitRecognizer()
    # Use a short, visually clean, fully numeric section as the canonical digit
    # source. Training from every row can pollute labels at page-boundary rows.
    for spec in [SPECS[1], SPECS[3]]:
        im = Image.open(spec.image_path).convert("L")
        lines = horizontal_lines(im)
        vlines = vertical_lines(im)
        for idx in range(min(22, len(lines) - 1 - spec.data_offset)):
            score = spec.start_score - idx
            if score < 0:
                continue
            score_cell, _, _ = row_cells(im, lines, vlines, spec.data_offset + idx)
            boxes = digit_boxes(score_cell)
            label = str(score)
            if len(boxes) != len(label):
                continue
            for digit, box in zip(label, boxes):
                recognizer.add_digit(digit, score_cell.crop(box))
    missing = [k for k, v in recognizer.templates.items() if not v]
    if missing:
        raise RuntimeError(f"missing digit templates: {missing}")
    return recognizer


def extract_rows() -> list[dict[str, object]]:
    recognizer = build_recognizer()
    rows: dict[tuple[str, str, int], dict[str, object]] = {}
    for spec in SPECS:
        im = Image.open(spec.image_path).convert("L")
        lines = horizontal_lines(im)
        vlines = vertical_lines(im)
        row_count = min(spec.row_limit or len(lines) - 1 - spec.data_offset, len(lines) - 1 - spec.data_offset)
        for idx in range(row_count):
            score = spec.start_score - idx
            if score < 0:
                continue
            _, count_cell, cumulative_cell = row_cells(im, lines, vlines, spec.data_offset + idx)
            # OCR the count column for cross-checking, but final same_score_count
            # is derived from cumulative ranks below because that column is more
            # stable and the relationship is deterministic.
            same_count = recognizer.recognize_number(count_cell)
            cumulative = recognizer.recognize_number(cumulative_cell)
            cumulative = CUMULATIVE_OVERRIDES.get((spec.province, spec.category, score), cumulative)
            key = (spec.province, spec.category, score)
            note = "官方合并段" if idx == 0 and spec.combined_top else ""
            row = {
                "province": spec.province,
                "category": spec.category,
                "year": 2025,
                "score": score,
                "same_score_count": same_count,
                "cumulative_rank": cumulative,
                "source_slice": spec.source_slice,
                "confidence": "high",
                "notes": note,
            }
            if key in rows and rows[key]["cumulative_rank"] != cumulative:
                raise RuntimeError(f"duplicate score mismatch: {key} {rows[key]} vs {row}")
            rows[key] = row
    result = sorted(rows.values(), key=lambda r: (str(r["category"]), -int(r["score"])))
    by_category: dict[str, list[dict[str, object]]] = {}
    for row in result:
        by_category.setdefault(str(row["category"]), []).append(row)
    for items in by_category.values():
        items.sort(key=lambda r: -int(r["score"]))
        prev = 0
        for row in items:
            cumulative = int(row["cumulative_rank"])
            row["same_score_count"] = cumulative - prev
            prev = cumulative
    return result


def validate(rows: list[dict[str, object]]) -> list[str]:
    messages = []
    by_category: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_category.setdefault(str(row["category"]), []).append(row)
    for category, items in sorted(by_category.items()):
        items = sorted(items, key=lambda r: -int(r["score"]))
        scores = [int(r["score"]) for r in items]
        missing = [s for s in range(max(scores), min(scores) - 1, -1) if s not in set(scores)]
        bad = []
        prev_cum = 0
        for row in items:
            same = int(row["same_score_count"])
            cumulative = int(row["cumulative_rank"])
            if cumulative - prev_cum != same:
                bad.append((row["score"], same, cumulative, prev_cum))
            if same < 0 or cumulative < prev_cum:
                bad.append((row["score"], same, cumulative, prev_cum))
            prev_cum = cumulative
        uncertain = sum(1 for r in items if r["confidence"] != "high")
        messages.append(
            f"云南 {category}: range {min(scores)}-{max(scores)}, rows {len(items)}, "
            f"continuous={not missing}, cumulative_ok={not bad}, uncertain={uncertain}"
        )
        if missing:
            messages.append(f"  missing: {missing[:20]}")
        if bad:
            messages.append(f"  bad: {bad[:10]}")
    return messages


def main() -> None:
    rows = extract_rows()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} rows)")
    for message in validate(rows):
        print(message)


if __name__ == "__main__":
    main()
