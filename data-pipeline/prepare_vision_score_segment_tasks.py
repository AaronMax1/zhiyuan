#!/usr/bin/env python3
"""Prepare image slices for manual/vision extraction of score-segment tables."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "data-pipeline" / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

from PIL import Image


DEFAULT_MANIFEST = ROOT / "data-pipeline" / "output" / "dxsbb_score_segments_manifest.json"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "output" / "vision_segments"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--province", default="")
    parser.add_argument("--category", default="")
    parser.add_argument("--min-height", type=int, default=700)
    parser.add_argument("--slice-height", type=int, default=1100)
    parser.add_argument("--overlap", type=int, default=80)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    tasks = []
    image_rows = []

    for article in manifest.get("articles", []):
        province = article.get("province", "")
        category = article.get("category", "")
        if args.province and province != args.province:
            continue
        if args.category and category != args.category:
            continue
        if not category or category == "物理类+历史类":
            continue
        for image_index, image in enumerate(article.get("images", []), start=1):
            if image.get("status") != "ok":
                continue
            source = ROOT / image["path"]
            with Image.open(source) as im:
                width, height = im.size
            useful = height >= args.min_height
            image_rows.append({
                "province": province,
                "category": category,
                "year": article.get("year", 2025),
                "title": article.get("title", ""),
                "source_image": image["path"],
                "width": width,
                "height": height,
                "useful": useful,
            })
            if not useful:
                continue
            slice_dir = args.output_dir / "slices" / str(article.get("year", 2025)) / province / safe_name(category) / source.parent.parent.name / source.stem
            slice_dir.mkdir(parents=True, exist_ok=True)
            slice_paths = make_slices(source, slice_dir, args.slice_height, args.overlap, args.force)
            for slice_order, slice_path in enumerate(slice_paths, start=1):
                tasks.append({
                    "province": province,
                    "category": category,
                    "year": article.get("year", 2025),
                    "article_title": article.get("title", ""),
                    "article_url": article.get("url", ""),
                    "source_image": image["path"],
                    "slice_order": slice_order,
                    "slice_path": str(slice_path.relative_to(ROOT)),
                    "status": "pending",
                    "notes": "",
                })

    write_csv(args.output_dir / "image_inventory.csv", image_rows)
    write_csv(args.output_dir / "vision_worklist.csv", tasks)
    write_csv(args.output_dir / "extracted_rows.csv", [])
    write_report(args.output_dir, image_rows, tasks)
    print(f"Useful images: {sum(1 for row in image_rows if row['useful'])}/{len(image_rows)}")
    print(f"Slices: {len(tasks)}")
    print(f"Worklist: {args.output_dir / 'vision_worklist.csv'}")


def make_slices(source: Path, output_dir: Path, slice_height: int, overlap: int, force: bool) -> list[Path]:
    with Image.open(source) as image:
        width, height = image.size
        if height <= slice_height:
            target = output_dir / "slice_001.png"
            if force or not target.exists():
                image.save(target)
            return [target]
        paths = []
        top = 0
        index = 1
        step = max(100, slice_height - overlap)
        while top < height:
            bottom = min(top + slice_height, height)
            target = output_dir / f"slice_{index:03d}.png"
            if force or not target.exists():
                image.crop((0, top, width, bottom)).save(target)
            paths.append(target)
            if bottom >= height:
                break
            top += step
            index += 1
        return paths


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fields = list(rows[0].keys())
    elif path.name == "extracted_rows.csv":
        fields = [
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
    else:
        fields = []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        if not fields:
            return
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_report(output_dir: Path, image_rows: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> None:
    by_province: dict[str, int] = {}
    for task in tasks:
        by_province[task["province"]] = by_province.get(task["province"], 0) + 1
    lines = [
        "# Vision Score Segment Tasks",
        "",
        f"- Useful images: {sum(1 for row in image_rows if row['useful'])}/{len(image_rows)}",
        f"- Slices: {len(tasks)}",
        "",
        "## Slices By Province",
        "",
    ]
    for province, count in sorted(by_province.items()):
        lines.append(f"- {province}: {count}")
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_name(value: str) -> str:
    return value.replace("/", "_").replace("+", "加")


if __name__ == "__main__":
    main()
