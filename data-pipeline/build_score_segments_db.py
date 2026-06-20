#!/usr/bin/env python3
"""Build unified score-segment database.

Priority:
  official/local crawled > trusted crawler output > open-source historical CSV.
Rows are kept source-aware; score_segment_best is the materialized preferred
view for rank/equivalent-score lookup.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "output" / "score_segments.db"
DEFAULT_OPEN_SOURCE_CSV = ROOT / "data-pipeline" / "raw" / "score_segments" / "open_source" / "gaokao_score_distribution_1996_2024.csv"
DEFAULT_GAOKAO_ADVISOR_DB = ROOT / "source-snapshots" / "gaokao-advisor" / "data" / "gaokao.db"
REPORT_MD = ROOT / "data-pipeline" / "output" / "score_segments_report.md"
REPORT_JSON = ROOT / "data-pipeline" / "output" / "score_segments_report.json"

PROVINCE_IDS = {
    "北京": 11, "天津": 12, "河北": 13, "山西": 14, "内蒙古": 15,
    "辽宁": 21, "吉林": 22, "黑龙江": 23, "上海": 31, "江苏": 32,
    "浙江": 33, "安徽": 34, "福建": 35, "江西": 36, "山东": 37,
    "河南": 41, "湖北": 42, "湖南": 43, "广东": 44, "广西": 45,
    "海南": 46, "重庆": 50, "四川": 51, "贵州": 52, "云南": 53,
    "西藏": 54, "陕西": 61, "甘肃": 62, "青海": 63, "宁夏": 64,
    "新疆": 65,
}

SOURCE_PRIORITY = {
    "official": 100,
    "local_crawler": 90,
    "gaokao_advisor": 80,
    "open_source": 40,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--open-source-csv", type=Path, default=DEFAULT_OPEN_SOURCE_CSV)
    parser.add_argument("--gaokao-advisor-db", type=Path, default=DEFAULT_GAOKAO_ADVISOR_DB)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()

    conn = sqlite3.connect(args.output)
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    imports: dict[str, Any] = {}
    imports["open_source"] = import_open_source_csv(conn, args.open_source_csv)
    imports["gaokao_advisor"] = import_gaokao_advisor_db(conn, args.gaokao_advisor_db)

    build_best(conn)
    report = build_report(conn, imports, args.output)
    write_report(report)
    conn.close()

    print(f"Built: {args.output}")
    print(f"Rows: {report['totals']['rows']}")
    print(f"Best rows: {report['totals']['best_rows']}")
    print(f"Coverage groups: {report['totals']['coverage_groups']}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE score_segment_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_dataset TEXT NOT NULL,
            source_file TEXT,
            source_priority INTEGER NOT NULL,
            province_id INTEGER NOT NULL,
            province TEXT NOT NULL,
            year INTEGER NOT NULL,
            category TEXT NOT NULL,
            exam_mode TEXT,
            score_high INTEGER NOT NULL,
            score_low INTEGER NOT NULL,
            same_score_count INTEGER NOT NULL,
            cumulative_rank INTEGER NOT NULL,
            total_score INTEGER,
            quality_flags TEXT NOT NULL,
            record_key TEXT NOT NULL
        );
        """
    )


def import_open_source_csv(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path), "rows": 0}

    inserted = 0
    skipped = 0
    batch = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                province = clean_text(row.get("省级行政区"))
                province_id = PROVINCE_IDS[province]
                year = int(row.get("年份") or 0)
                category = normalize_category(row.get("综合"))
                score_high = int(row.get("最高分") or 0)
                score_low = int(row.get("最低分") or score_high)
                same_count = int(row.get("人数") or 0)
                cumulative = int(row.get("累计") or 0)
                total_score = clean_int(row.get("总分(裸分)"))
            except Exception:
                skipped += 1
                continue
            flags = validate_row(score_high, score_low, same_count, cumulative)
            batch.append(
                make_row(
                    "open_source",
                    "sdgedfegw/Gaokao-score-distribution",
                    str(path),
                    province_id,
                    province,
                    year,
                    category,
                    clean_text(row.get("模式")),
                    score_high,
                    score_low,
                    same_count,
                    cumulative,
                    total_score,
                    flags,
                )
            )
            if len(batch) >= 10000:
                inserted += insert_rows(conn, batch)
                batch.clear()
    if batch:
        inserted += insert_rows(conn, batch)
    return {"status": "ok", "path": str(path), "rows": inserted, "skipped": skipped}


def import_gaokao_advisor_db(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "path": str(path), "rows": 0}
    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    table = src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='score_segments'"
    ).fetchone()
    if not table:
        src.close()
        return {"status": "no_score_segments", "path": str(path), "rows": 0}

    inserted = 0
    batch = []
    for row in src.execute(
        """
        SELECT province_id, province, year, category, exam_mode, score_high,
               score_low, count, cumulative, total_score
        FROM score_segments
        """
    ):
        flags = validate_row(row["score_high"], row["score_low"], row["count"], row["cumulative"])
        batch.append(
            make_row(
                "gaokao_advisor",
                "gaokao-advisor score_segments",
                str(path),
                row["province_id"],
                row["province"],
                row["year"],
                normalize_category(row["category"]),
                row["exam_mode"],
                row["score_high"],
                row["score_low"],
                row["count"],
                row["cumulative"],
                row["total_score"],
                flags,
            )
        )
        if len(batch) >= 10000:
            inserted += insert_rows(conn, batch)
            batch.clear()
    if batch:
        inserted += insert_rows(conn, batch)
    src.close()
    return {"status": "ok", "path": str(path), "rows": inserted}


def make_row(
    source_type: str,
    source_dataset: str,
    source_file: str,
    province_id: int,
    province: str,
    year: int,
    category: str,
    exam_mode: str,
    score_high: int,
    score_low: int,
    same_count: int,
    cumulative: int,
    total_score: int | None,
    flags: list[str],
) -> tuple[Any, ...]:
    record_key = "|".join(map(str, [province_id, year, category, score_high, score_low]))
    return (
        source_type,
        source_dataset,
        source_file,
        SOURCE_PRIORITY[source_type],
        province_id,
        province,
        year,
        category,
        exam_mode,
        score_high,
        score_low,
        same_count,
        cumulative,
        total_score,
        json.dumps(sorted(set(flags)), ensure_ascii=False),
        record_key,
    )


def insert_rows(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> int:
    conn.executemany(
        """
        INSERT INTO score_segment_records (
            source_type, source_dataset, source_file, source_priority,
            province_id, province, year, category, exam_mode, score_high,
            score_low, same_score_count, cumulative_rank, total_score,
            quality_flags, record_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def build_best(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_seg_records_lookup
            ON score_segment_records(province_id, year, category, score_high);
        CREATE INDEX idx_seg_records_key
            ON score_segment_records(record_key, source_priority);

        CREATE TABLE score_segment_best AS
        SELECT * FROM (
            SELECT r.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY record_key
                       ORDER BY source_priority DESC, id ASC
                   ) AS rn
            FROM score_segment_records r
        )
        WHERE rn = 1;

        CREATE INDEX idx_seg_best_score
            ON score_segment_best(province_id, year, category, score_high);
        CREATE INDEX idx_seg_best_rank
            ON score_segment_best(province_id, year, category, cumulative_rank);
        CREATE INDEX idx_seg_best_group
            ON score_segment_best(province, year, category);
        """
    )
    conn.commit()


def build_report(conn: sqlite3.Connection, imports: dict[str, Any], output: Path) -> dict[str, Any]:
    groups = rows_as_dicts(
        conn,
        """
        SELECT province, year, category, source_type, COUNT(*) AS rows,
               MIN(score_high) AS min_score, MAX(score_high) AS max_score,
               MAX(cumulative_rank) AS max_rank
        FROM score_segment_best
        GROUP BY province, year, category, source_type
        ORDER BY year DESC, province, category
        """,
    )
    return {
        "output": str(output),
        "imports": imports,
        "totals": {
            "rows": scalar(conn, "SELECT COUNT(*) FROM score_segment_records"),
            "best_rows": scalar(conn, "SELECT COUNT(*) FROM score_segment_best"),
            "coverage_groups": len(groups),
        },
        "by_source": rows_as_dicts(
            conn,
            "SELECT source_type, COUNT(*) AS rows FROM score_segment_records GROUP BY source_type ORDER BY rows DESC",
        ),
        "coverage": groups,
        "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Score Segments Report",
        "",
        f"- Output: `{report['output']}`",
        f"- Integrity: `{report['integrity_check']}`",
        f"- Rows: {report['totals']['rows']:,}",
        f"- Best rows: {report['totals']['best_rows']:,}",
        f"- Coverage groups: {report['totals']['coverage_groups']:,}",
        "",
        "## By Source",
        "",
    ]
    for row in report["by_source"]:
        lines.append(f"- {row['source_type']}: {row['rows']:,}")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_row(score_high: int, score_low: int, same_count: int, cumulative: int) -> list[str]:
    flags = []
    if score_high <= 0 or score_high > 900 or score_low <= 0 or score_low > 900:
        flags.append("invalid_score")
    if same_count < 0:
        flags.append("invalid_same_score_count")
    if cumulative < 0:
        flags.append("invalid_cumulative_rank")
    if cumulative == 0:
        flags.append("zero_cumulative")
    return flags


def normalize_category(value: Any) -> str:
    text = clean_text(value)
    if text in {"3+3综合", "综合", "普通类", "综合/普通类"}:
        return "综合"
    if "物理" in text:
        return "物理类"
    if "历史" in text:
        return "历史类"
    if "理科" in text:
        return "理科"
    if "文科" in text:
        return "文科"
    return text


def clean_int(value: Any) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0] or 0)


def rows_as_dicts(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql)]


if __name__ == "__main__":
    main()
