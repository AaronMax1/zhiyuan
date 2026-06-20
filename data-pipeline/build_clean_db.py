#!/usr/bin/env python3
"""Build a cleaned gaokao admissions database from local source snapshots."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XUEFENG = ROOT / "source-snapshots" / "xuefeng-agent" / "admission_clean.db.gz"
DEFAULT_QIMING = ROOT / "source-snapshots" / "qiming-zhiyuan" / "admission_clean.db.gz"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "output" / "gaokao_clean.db"

PROVINCES = {
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
}

BAD_SOURCE_PATTERNS = (
    "排名", "QS", "软科", "满意度", "就业", "职业方向", "专业基本", "专业介绍",
    "院校介绍", "招生计划", "计划", "学科评估",
)

OFFICIAL_SOURCE_PATTERNS = (
    "教育考试院", "招生考试院", "考试院", "投档线", "投档分数", "投档情况",
    "平行志愿投档", "录取分数", "专业录取分数", "专业分数线",
)

MAJOR_SCORE_PATTERNS = ("专业分数", "专业录取", "专业线")

BAD_SCHOOL_TOKENS = (
    "就业前景", "就业方向", "专业在专业", "排名", "满意度", "培养目标",
    "主要课程", "毕业生", "薪资", "城市：", "行业：",
)

WEAK_MAJOR_VALUES = {
    "", "不限", "文科", "理科", "综合", "物理类", "历史类", "本科", "专科",
    "本科批", "专科批", "高职(专科)", "本一", "本二", "一本", "二本",
}


@dataclass
class CleanRecord:
    source_dataset: str
    source_id: int | None
    province: str
    year: int | None
    category: str
    batch: str
    school_name: str
    major_name: str
    score: int | None
    rank: int | None
    quota: int | None
    source_file: str
    source_type: str
    trust_level: str
    is_usable: int
    score_reliable: int
    rank_reliable: int
    quality_flags: list[str]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xuefeng", type=Path, default=DEFAULT_XUEFENG)
    parser.add_argument("--qiming", type=Path, default=DEFAULT_QIMING)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-qiming", action="store_true")
    parser.add_argument("--skip-xuefeng", action="store_true")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()

    conn = sqlite3.connect(args.output)
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    report: dict[str, Any] = {"sources": {}, "post_rank_audit": {}, "output": str(args.output)}

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        if not args.skip_xuefeng and args.xuefeng.exists():
            xdb = decompress_if_needed(args.xuefeng, tmpdir / "xuefeng.db")
            stats = import_xuefeng(conn, xdb)
            report["sources"]["xuefeng-agent"] = stats
        if not args.skip_qiming and args.qiming.exists():
            qdb = decompress_if_needed(args.qiming, tmpdir / "qiming.db")
            stats = import_qiming(conn, qdb)
            report["sources"]["qiming-zhiyuan"] = stats

    report["post_rank_audit"] = audit_rank_reliability(conn)
    create_indexes(conn)
    conn.commit()

    summary = build_summary(conn, report)
    write_reports(args.output.parent, summary)
    conn.close()

    print(f"Built: {args.output}")
    print(f"Rows total: {summary['totals']['rows_total']}")
    print(f"Rows usable: {summary['totals']['rows_usable']}")
    print(f"Report: {args.output.parent / 'data_quality_report.md'}")


def decompress_if_needed(path: Path, target: Path) -> Path:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return target
    return path


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE admission_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_dataset TEXT NOT NULL,
            source_id INTEGER,
            province TEXT NOT NULL,
            year INTEGER,
            category TEXT,
            batch TEXT,
            school_name TEXT NOT NULL,
            major_name TEXT,
            score INTEGER,
            rank INTEGER,
            quota INTEGER,
            source_file TEXT,
            source_type TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            is_usable INTEGER NOT NULL,
            score_reliable INTEGER NOT NULL,
            rank_reliable INTEGER NOT NULL,
            quality_flags TEXT NOT NULL
        );

        CREATE TABLE source_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_dataset TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_type TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            rows_total INTEGER NOT NULL DEFAULT 0,
            rows_usable INTEGER NOT NULL DEFAULT 0,
            flags TEXT NOT NULL
        );
        """
    )


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_adm_lookup_rank
            ON admission_records(province, year, rank)
            WHERE is_usable = 1 AND rank_reliable = 1;
        CREATE INDEX idx_adm_lookup_score
            ON admission_records(province, year, score)
            WHERE is_usable = 1 AND score_reliable = 1;
        CREATE INDEX idx_adm_school
            ON admission_records(school_name);
        CREATE INDEX idx_adm_major
            ON admission_records(major_name);
        CREATE INDEX idx_adm_source
            ON admission_records(source_dataset, source_file);
        """
    )


def import_xuefeng(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        """
        SELECT id, province, year, category, batch, school_name, major_name,
               score, rank, quota, source_file
        FROM admission
        """
    )
    return import_rows(conn, "xuefeng-agent", rows, normalize_xuefeng)


def import_qiming(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        """
        SELECT id, province, year, school, major, score, rank, source
        FROM admission
        """
    )
    return import_rows(conn, "qiming-zhiyuan", rows, normalize_qiming)


def import_rows(
    conn: sqlite3.Connection,
    dataset: str,
    rows: Iterable[sqlite3.Row],
    normalizer,
) -> dict[str, Any]:
    stats = Counter()
    source_file_stats: dict[str, Counter] = defaultdict(Counter)
    batch: list[tuple[Any, ...]] = []

    for row in rows:
        stats["rows_seen"] += 1
        rec = normalizer(row)
        stats["rows_inserted"] += 1
        if rec.is_usable:
            stats["rows_usable_initial"] += 1
        for flag in rec.quality_flags:
            stats[f"flag:{flag}"] += 1

        sf = rec.source_file or ""
        source_file_stats[sf]["rows_total"] += 1
        if rec.is_usable:
            source_file_stats[sf]["rows_usable"] += 1
        source_file_stats[sf][f"type:{rec.source_type}"] += 1
        source_file_stats[sf][f"trust:{rec.trust_level}"] += 1

        batch.append(record_tuple(rec))
        if len(batch) >= 5000:
            insert_records(conn, batch)
            batch.clear()

    if batch:
        insert_records(conn, batch)

    for source_file, c in source_file_stats.items():
        source_type = most_common_prefixed(c, "type:") or "unknown"
        trust_level = most_common_prefixed(c, "trust:") or "mixed"
        conn.execute(
            """
            INSERT INTO source_files(source_dataset, source_file, source_type, trust_level,
                                     rows_total, rows_usable, flags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset,
                source_file,
                source_type,
                trust_level,
                c["rows_total"],
                c["rows_usable"],
                json.dumps([], ensure_ascii=False),
            ),
        )

    conn.commit()
    return dict(stats)


def normalize_xuefeng(row: sqlite3.Row) -> CleanRecord:
    source_file = clean_text(row["source_file"])
    source_type, trust_level = classify_source(source_file)
    flags = base_quality_flags(
        province=clean_text(row["province"]),
        year=to_int(row["year"]),
        school=clean_text(row["school_name"]),
        major=clean_text(row["major_name"]),
        score=to_int(row["score"]),
        rank=to_int(row["rank"]),
        source_file=source_file,
        source_type=source_type,
    )
    is_usable, score_reliable, rank_reliable = usability_from_flags(flags, source_type, trust_level)
    return CleanRecord(
        source_dataset="xuefeng-agent",
        source_id=to_int(row["id"]),
        province=clean_text(row["province"]),
        year=to_int(row["year"]),
        category=clean_text(row["category"]),
        batch=clean_text(row["batch"]),
        school_name=clean_text(row["school_name"]),
        major_name=clean_text(row["major_name"]),
        score=to_int(row["score"]),
        rank=to_int(row["rank"]),
        quota=to_int(row["quota"]),
        source_file=source_file,
        source_type=source_type,
        trust_level=trust_level,
        is_usable=is_usable,
        score_reliable=score_reliable,
        rank_reliable=rank_reliable,
        quality_flags=flags,
    )


def normalize_qiming(row: sqlite3.Row) -> CleanRecord:
    source_file = clean_text(row["source"])
    source_type, trust_level = classify_source(source_file)
    province = clean_text(row["province"])
    year = to_int(row["year"])
    school = clean_text(row["school"])
    major = clean_text(row["major"])
    flags = base_quality_flags(
        province=province,
        year=year,
        school=school,
        major=major,
        score=to_int(row["score"]),
        rank=to_int(row["rank"]),
        source_file=source_file,
        source_type=source_type,
    )
    inferred_years = years_in_text(source_file)
    if inferred_years and year and year not in inferred_years:
        flags.append("source_year_mismatch")
    category, batch = infer_category_batch(major, source_file)
    is_usable, score_reliable, rank_reliable = usability_from_flags(flags, source_type, trust_level)
    return CleanRecord(
        source_dataset="qiming-zhiyuan",
        source_id=to_int(row["id"]),
        province=province,
        year=year,
        category=category,
        batch=batch,
        school_name=school,
        major_name=major,
        score=to_int(row["score"]),
        rank=to_int(row["rank"]),
        quota=None,
        source_file=source_file,
        source_type=source_type,
        trust_level=trust_level,
        is_usable=is_usable,
        score_reliable=score_reliable,
        rank_reliable=rank_reliable,
        quality_flags=flags,
    )


def base_quality_flags(
    *,
    province: str,
    year: int | None,
    school: str,
    major: str,
    score: int | None,
    rank: int | None,
    source_file: str,
    source_type: str,
) -> list[str]:
    flags: list[str] = []
    if province not in PROVINCES:
        flags.append("invalid_province")
    if year is None or year < 2017 or year > 2026:
        flags.append("invalid_year")
    if not is_school_like(school):
        flags.append("invalid_school_name")
    if len(school) > 40:
        flags.append("long_school_name")
    if any(token in school for token in BAD_SCHOOL_TOKENS):
        flags.append("school_text_blob")
    if not major:
        flags.append("missing_major")
    elif major in WEAK_MAJOR_VALUES or len(major) <= 2:
        flags.append("weak_major_name")
    if score is None:
        flags.append("missing_score")
    elif score < 100 or score > 750:
        flags.append("invalid_score")
    if rank is None:
        flags.append("missing_rank")
    elif rank <= 0 or rank > 800000:
        flags.append("invalid_rank")
    if source_type in {"ranking", "employment", "plan", "bad"}:
        flags.append(f"bad_source_type:{source_type}")
    if any(p in source_file for p in BAD_SOURCE_PATTERNS):
        flags.append("bad_source_pattern")
    return flags


def classify_source(source_file: str) -> tuple[str, str]:
    if not source_file:
        return "unknown", "mixed"
    if any(p in source_file for p in ("排名", "QS", "软科")):
        return "ranking", "bad"
    if any(p in source_file for p in ("满意度", "就业", "职业方向", "专业基本", "介绍")):
        return "employment", "bad"
    if "招生计划" in source_file or re.search(r"(^|[^录])计划", source_file):
        return "plan", "bad"
    if any(p in source_file for p in OFFICIAL_SOURCE_PATTERNS):
        trust = "official" if any(p in source_file for p in ("考试院", "教育考试院", "招生考试院")) else "third_party"
        if any(p in source_file for p in MAJOR_SCORE_PATTERNS):
            return "major_score", trust
        return "official_admission", trust
    return "unknown", "mixed"


def usability_from_flags(flags: list[str], source_type: str, trust_level: str) -> tuple[int, int, int]:
    fatal = {
        "invalid_province",
        "invalid_year",
        "invalid_school_name",
        "long_school_name",
        "school_text_blob",
        "invalid_score",
        "source_year_mismatch",
        "bad_source_pattern",
        "bad_source_type:ranking",
        "bad_source_type:employment",
        "bad_source_type:plan",
        "bad_source_type:bad",
    }
    is_usable = 0 if any(flag in fatal for flag in flags) else 1
    score_reliable = 1 if is_usable and "missing_score" not in flags and "invalid_score" not in flags else 0
    rank_reliable = 1 if is_usable and "missing_rank" not in flags and "invalid_rank" not in flags else 0
    if source_type == "unknown" and trust_level == "mixed":
        # Unknown sources may still be useful for exploratory fallback, but not rank-safe.
        rank_reliable = 0
    return is_usable, score_reliable, rank_reliable


def audit_rank_reliability(conn: sqlite3.Connection) -> dict[str, Any]:
    """Disable rank use for groups whose rank distribution is structurally implausible."""
    audited = []
    rows = conn.execute(
        """
        SELECT source_dataset, province, year, COALESCE(category, '') AS category,
               COUNT(*) AS n,
               SUM(CASE WHEN rank IS NOT NULL AND rank > 0 THEN 1 ELSE 0 END) AS rank_n,
               MIN(rank) AS min_rank,
               MAX(rank) AS max_rank,
               COUNT(DISTINCT rank) AS distinct_rank
        FROM admission_records
        WHERE is_usable = 1
        GROUP BY source_dataset, province, year, COALESCE(category, '')
        """
    ).fetchall()
    disabled_total = 0
    for r in rows:
        n = int(r["n"])
        rank_n = int(r["rank_n"] or 0)
        max_rank = r["max_rank"]
        distinct_rank = int(r["distinct_rank"] or 0)
        reasons = []
        if rank_n >= 500 and max_rank is not None and max_rank < 1000:
            reasons.append("rank_max_too_low_for_large_group")
        if rank_n >= 500 and distinct_rank / max(rank_n, 1) < 0.20:
            reasons.append("rank_distinct_ratio_too_low")
        if reasons:
            cur = conn.execute(
                """
                UPDATE admission_records
                SET rank_reliable = 0,
                    quality_flags = json_insert(quality_flags, '$[#]', ?)
                WHERE is_usable = 1
                  AND source_dataset = ?
                  AND province = ?
                  AND year IS ?
                  AND COALESCE(category, '') = ?
                  AND rank IS NOT NULL
                  AND rank > 0
                """,
                (
                    "group_rank_unreliable:" + ",".join(reasons),
                    r["source_dataset"],
                    r["province"],
                    r["year"],
                    r["category"],
                ),
            )
            disabled_total += cur.rowcount
            audited.append({
                "source_dataset": r["source_dataset"],
                "province": r["province"],
                "year": r["year"],
                "category": r["category"],
                "rows": n,
                "rank_rows_disabled": cur.rowcount,
                "min_rank": r["min_rank"],
                "max_rank": max_rank,
                "distinct_rank": distinct_rank,
                "reasons": reasons,
            })
    conn.commit()
    return {"groups_disabled": audited, "rank_rows_disabled": disabled_total}


def build_summary(conn: sqlite3.Connection, report: dict[str, Any]) -> dict[str, Any]:
    totals = one_row(conn, """
        SELECT COUNT(*) rows_total,
               SUM(is_usable) rows_usable,
               SUM(score_reliable) score_reliable,
               SUM(rank_reliable) rank_reliable,
               COUNT(DISTINCT province) provinces,
               COUNT(DISTINCT school_name) schools
        FROM admission_records
    """)
    by_source = rows_as_dicts(conn, """
        SELECT source_dataset,
               COUNT(*) rows_total,
               SUM(is_usable) rows_usable,
               SUM(score_reliable) score_reliable,
               SUM(rank_reliable) rank_reliable,
               COUNT(DISTINCT province) provinces,
               MIN(year) min_year,
               MAX(year) max_year
        FROM admission_records
        GROUP BY source_dataset
        ORDER BY source_dataset
    """)
    by_province = rows_as_dicts(conn, """
        SELECT province,
               COUNT(*) rows_total,
               SUM(is_usable) rows_usable,
               SUM(score_reliable) score_reliable,
               SUM(rank_reliable) rank_reliable,
               MIN(year) min_year,
               MAX(year) max_year
        FROM admission_records
        GROUP BY province
        ORDER BY province
    """)
    top_flags = rows_as_dicts(conn, """
        WITH flags AS (
            SELECT value AS flag
            FROM admission_records, json_each(admission_records.quality_flags)
        )
        SELECT flag, COUNT(*) count
        FROM flags
        GROUP BY flag
        ORDER BY count DESC
        LIMIT 40
    """)
    return {
        "totals": totals,
        "by_source": by_source,
        "by_province": by_province,
        "top_flags": top_flags,
        "import_report": report,
    }


def write_reports(out_dir: Path, summary: dict[str, Any]) -> None:
    json_path = out_dir / "data_quality_report.json"
    md_path = out_dir / "data_quality_report.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 数据质量报告", ""]
    totals = summary["totals"]
    lines.extend([
        "## 总览",
        "",
        f"- 总记录：{totals['rows_total']}",
        f"- 可用于推荐：{totals['rows_usable']}",
        f"- 分数可靠：{totals['score_reliable']}",
        f"- 位次可靠：{totals['rank_reliable']}",
        f"- 省份数：{totals['provinces']}",
        f"- 学校数：{totals['schools']}",
        "",
        "## 来源统计",
        "",
        "| 来源 | 总记录 | 可用 | 分数可靠 | 位次可靠 | 省份 | 年份 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ])
    for row in summary["by_source"]:
        lines.append(
            f"| {row['source_dataset']} | {row['rows_total']} | {row['rows_usable']} | "
            f"{row['score_reliable']} | {row['rank_reliable']} | {row['provinces']} | "
            f"{row['min_year']}-{row['max_year']} |"
        )
    lines.extend(["", "## 省份统计", "", "| 省份 | 总记录 | 可用 | 分数可靠 | 位次可靠 | 年份 |", "|---|---:|---:|---:|---:|---|"])
    for row in summary["by_province"]:
        lines.append(
            f"| {row['province']} | {row['rows_total']} | {row['rows_usable']} | "
            f"{row['score_reliable']} | {row['rank_reliable']} | {row['min_year']}-{row['max_year']} |"
        )
    lines.extend(["", "## 高频质量标记", "", "| 标记 | 数量 |", "|---|---:|"])
    for row in summary["top_flags"]:
        lines.append(f"| `{row['flag']}` | {row['count']} |")
    disabled = summary["import_report"]["post_rank_audit"].get("groups_disabled", [])
    lines.extend(["", "## 位次禁用分组", "", "| 来源 | 省份 | 年份 | 科类 | 禁用位次行 | 原因 |", "|---|---|---:|---|---:|---|"])
    for row in disabled[:80]:
        lines.append(
            f"| {row['source_dataset']} | {row['province']} | {row['year']} | {row['category']} | "
            f"{row['rank_rows_disabled']} | {','.join(row['reasons'])} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_tuple(rec: CleanRecord) -> tuple[Any, ...]:
    return (
        rec.source_dataset,
        rec.source_id,
        rec.province,
        rec.year,
        rec.category,
        rec.batch,
        rec.school_name,
        rec.major_name,
        rec.score,
        rec.rank,
        rec.quota,
        rec.source_file,
        rec.source_type,
        rec.trust_level,
        rec.is_usable,
        rec.score_reliable,
        rec.rank_reliable,
        json.dumps(rec.quality_flags, ensure_ascii=False),
    )


def insert_records(conn: sqlite3.Connection, rows: list[tuple[Any, ...]]) -> None:
    conn.executemany(
        """
        INSERT INTO admission_records(
            source_dataset, source_id, province, year, category, batch,
            school_name, major_name, score, rank, quota, source_file,
            source_type, trust_level, is_usable, score_reliable,
            rank_reliable, quality_flags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def is_school_like(school: str) -> bool:
    if not school or len(school) < 2:
        return False
    if len(school) > 80:
        return False
    if any(token in school for token in BAD_SCHOOL_TOKENS):
        return False
    return bool(re.search(r"(大学|学院|学校|职业|专科|高等|校区|分校)", school))


def infer_category_batch(major: str, source_file: str) -> tuple[str, str]:
    text = f"{major} {source_file}"
    category = ""
    batch = ""
    if "物理" in text:
        category = "物理类"
    elif "历史" in text:
        category = "历史类"
    elif "理科" in text or major == "理科":
        category = "理科"
    elif "文科" in text or major == "文科":
        category = "文科"
    elif "综合" in text:
        category = "综合"
    if "本科" in text:
        batch = "本科批"
    elif "专科" in text or "高职" in text:
        batch = "专科批"
    return category, batch


def years_in_text(text: str) -> set[int]:
    return {int(y) for y in re.findall(r"20(?:1[7-9]|2[0-6])", text or "")}


def most_common_prefixed(counter: Counter, prefix: str) -> str:
    items = [(k[len(prefix):], v) for k, v in counter.items() if k.startswith(prefix)]
    if not items:
        return ""
    return sorted(items, key=lambda x: (-x[1], x[0]))[0][0]


def rows_as_dicts(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql)]


def one_row(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    return dict(conn.execute(sql).fetchone())


if __name__ == "__main__":
    main()
