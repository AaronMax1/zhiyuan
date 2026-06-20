#!/usr/bin/env python3
"""Import validated 2025 vision score segment rows into score_segments.db."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data-pipeline" / "output" / "vision_segments" / "validated_rows.csv"
DEFAULT_DB = ROOT / "data-pipeline" / "output" / "score_segments.db"

PROVINCE_IDS = {
    "北京": 11, "天津": 12, "河北": 13, "山西": 14, "内蒙古": 15,
    "辽宁": 21, "吉林": 22, "黑龙江": 23, "上海": 31, "江苏": 32,
    "浙江": 33, "安徽": 34, "福建": 35, "江西": 36, "山东": 37,
    "河南": 41, "湖北": 42, "湖南": 43, "广东": 44, "广西": 45,
    "海南": 46, "重庆": 50, "四川": 51, "贵州": 52, "云南": 53,
    "西藏": 54, "陕西": 61, "甘肃": 62, "青海": 63, "宁夏": 64,
    "新疆": 65,
}

SOURCE_TYPE = "local_vision_dxsbb_2025"
SOURCE_DATASET = "dxsbb_2025_score_segments_cleaned"
SOURCE_PRIORITY = 90


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--replace-source", action="store_true", default=True)
    args = parser.parse_args()

    rows = read_rows(args.input)
    if not rows:
        raise SystemExit(f"No rows to import: {args.input}")

    with sqlite3.connect(args.db) as conn:
        if args.replace_source:
            conn.execute("DELETE FROM score_segment_records WHERE source_type=?", (SOURCE_TYPE,))
        insert_rows(conn, rows, args.input)
        rebuild_best(conn)
        conn.commit()

        imported = conn.execute(
            "SELECT COUNT(*) FROM score_segment_records WHERE source_type=?", (SOURCE_TYPE,)
        ).fetchone()[0]
        best_2025 = conn.execute(
            "SELECT COUNT(*) FROM score_segment_best WHERE year=2025"
        ).fetchone()[0]
    print(f"Imported source rows: {imported}")
    print(f"Best 2025 rows: {best_2025}")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [row for row in csv.DictReader(f) if row.get("score")]


def insert_rows(conn: sqlite3.Connection, rows: list[dict[str, str]], input_path: Path) -> None:
    payload = []
    for row in rows:
        province = row["province"]
        province_id = PROVINCE_IDS[province]
        year = int(row["year"])
        category = row["category"]
        score = int(str(row["score"]).replace(",", ""))
        same = int(str(row["same_score_count"]).replace(",", ""))
        cumulative = int(str(row["cumulative_rank"]).replace(",", ""))
        exam_mode = exam_mode_for(category)
        quality_flags = row.get("quality_flags") or "[]"
        record_key = "|".join(map(str, [province_id, year, category, score, score]))
        payload.append(
            (
                SOURCE_TYPE,
                SOURCE_DATASET,
                str(input_path.relative_to(ROOT)),
                SOURCE_PRIORITY,
                province_id,
                province,
                year,
                category,
                exam_mode,
                score,
                score,
                same,
                cumulative,
                750,
                quality_flags,
                record_key,
            )
        )
    conn.executemany(
        """
        INSERT INTO score_segment_records (
            source_type, source_dataset, source_file, source_priority,
            province_id, province, year, category, exam_mode,
            score_high, score_low, same_score_count, cumulative_rank,
            total_score, quality_flags, record_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )


def exam_mode_for(category: str) -> str:
    if category in {"物理类", "历史类"}:
        return "3+1+2"
    if category == "综合":
        return "综合改革"
    return "传统文理"


def rebuild_best(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS score_segment_best")
    conn.execute(
        """
        CREATE TABLE score_segment_best AS
        SELECT *
        FROM (
            SELECT r.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY record_key
                       ORDER BY source_priority DESC, id ASC
                   ) AS rn
            FROM score_segment_records r
        )
        WHERE rn = 1
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_seg_best_score
        ON score_segment_best(province_id, year, category, score_high)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_seg_best_rank
        ON score_segment_best(province_id, year, category, cumulative_rank)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_seg_best_group
        ON score_segment_best(province, year, category)
        """
    )


if __name__ == "__main__":
    main()
