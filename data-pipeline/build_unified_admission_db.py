#!/usr/bin/env python3
"""Build a unified, source-aware admissions database.

The unified database keeps source provenance instead of blending all rows into
one trusted set. Query code can use admission_best_records for the preferred
row per normalized key, while normalized_admission_records preserves all usable
and flagged source rows for audit and fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OFFICIAL_DB = ROOT / "data-pipeline" / "output" / "official_admission.db"
DEFAULT_FALLBACK_DB = ROOT / "data-pipeline" / "output" / "fallback_admission.db"
DEFAULT_OPEN_SOURCE_DB = ROOT / "data-pipeline" / "output" / "gaokao_clean.db"
DEFAULT_OUTPUT_DB = ROOT / "data-pipeline" / "output" / "unified_admission.db"
DEFAULT_REPORT_MD = ROOT / "data-pipeline" / "output" / "unified_admission_report.md"
DEFAULT_REPORT_JSON = ROOT / "data-pipeline" / "output" / "unified_admission_report.json"

SOURCE_PRIORITIES = {
    "official": 100,
    "aggregate": 60,
    "open_source": 30,
}

PROVINCES = {
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
}

SPECIAL_SCHOOL_SUFFIXES = (
    "学院", "大学", "学校", "分校", "校区", "职业技术大学", "职业学院",
    "高等专科学校", "专科学校", "师范专科学校",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_DB)
    parser.add_argument("--fallback-db", type=Path, default=DEFAULT_FALLBACK_DB)
    parser.add_argument("--open-source-db", type=Path, default=DEFAULT_OPEN_SOURCE_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--include-open-source-unusable", action="store_true")
    parser.add_argument("--skip-open-source", action="store_true")
    args = parser.parse_args()

    args.output_db.parent.mkdir(parents=True, exist_ok=True)
    if args.output_db.exists():
        args.output_db.unlink()

    conn = sqlite3.connect(args.output_db)
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    report: dict[str, Any] = {
        "output_db": str(args.output_db),
        "inputs": {},
        "imports": {},
    }

    if args.official_db.exists():
        report["imports"]["official"] = import_official(conn, args.official_db)
        report["inputs"]["official_db"] = str(args.official_db)
    else:
        report["imports"]["official"] = {"missing": True, "path": str(args.official_db)}

    if args.fallback_db.exists():
        report["imports"]["aggregate"] = import_fallback(conn, args.fallback_db)
        report["inputs"]["fallback_db"] = str(args.fallback_db)
    else:
        report["imports"]["aggregate"] = {"missing": True, "path": str(args.fallback_db)}

    if not args.skip_open_source and args.open_source_db.exists():
        report["imports"]["open_source"] = import_open_source(
            conn,
            args.open_source_db,
            include_unusable=args.include_open_source_unusable,
        )
        report["inputs"]["open_source_db"] = str(args.open_source_db)
    elif args.skip_open_source:
        report["imports"]["open_source"] = {"skipped": True}
    else:
        report["imports"]["open_source"] = {"missing": True, "path": str(args.open_source_db)}

    finalize(conn)
    summary = build_summary(conn, report)
    write_reports(args.report_md, args.report_json, summary)
    conn.close()

    print(f"Built: {args.output_db}")
    print(f"Records: {summary['totals']['normalized_records']}")
    print(f"Best records: {summary['totals']['best_records']}")
    print(f"School profiles: {summary['totals']['school_profiles']}")
    print(f"Report: {args.report_md}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE normalized_admission_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_dataset TEXT NOT NULL,
            source_id TEXT,
            source_file TEXT,
            source_trust_level TEXT NOT NULL,
            source_priority INTEGER NOT NULL,
            province TEXT NOT NULL,
            year INTEGER,
            category_raw TEXT,
            category TEXT,
            batch_raw TEXT,
            batch TEXT,
            education_level TEXT NOT NULL,
            school_code TEXT,
            school_id TEXT,
            school_name TEXT NOT NULL,
            school_key TEXT NOT NULL,
            major_code TEXT,
            major_name TEXT,
            score INTEGER,
            rank INTEGER,
            quota INTEGER,
            plan_count INTEGER,
            score_reliable INTEGER NOT NULL,
            rank_reliable INTEGER NOT NULL,
            is_usable INTEGER NOT NULL,
            quality_flags TEXT NOT NULL,
            raw_payload TEXT,
            dedupe_key TEXT NOT NULL,
            record_hash TEXT NOT NULL
        );

        CREATE TABLE school_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_key TEXT NOT NULL UNIQUE,
            school_name TEXT NOT NULL,
            school_id TEXT,
            province TEXT,
            city TEXT,
            school_type TEXT,
            school_level TEXT,
            school_nature TEXT,
            inferred_education_level TEXT NOT NULL,
            source_types TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            record_count INTEGER NOT NULL,
            quality_flags TEXT NOT NULL
        );

        CREATE TABLE data_quality_summary (
            metric_group TEXT NOT NULL,
            metric_key TEXT NOT NULL,
            metric_value INTEGER NOT NULL,
            extra_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(metric_group, metric_key)
        );
        """
    )


def import_official(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        """
        SELECT id, source_id, source_file, province, year, category, batch,
               school_code, school_name, major_code, major_name, score, rank,
               raw_row, quality_flags
        FROM official_admission_records
        """
    )
    stats = import_rows(conn, rows, normalize_official_row)
    src.close()
    return stats


def import_fallback(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any]:
    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row
    rows = src.execute(
        """
        SELECT id, source_dataset, source_url, source_file, trust_level,
               province, province_id, year, category, batch, school_id,
               school_name, school_province, school_city, school_type,
               school_level, school_nature, special_group, special_group_name,
               select_subjects, score, rank, plan_count, zslx_name, raw_json,
               quality_flags, record_hash
        FROM fallback_admission_records
        """
    )
    stats = import_rows(conn, rows, normalize_fallback_row)
    src.close()
    return stats


def import_open_source(
    conn: sqlite3.Connection,
    db_path: Path,
    include_unusable: bool = False,
) -> dict[str, Any]:
    src = sqlite3.connect(db_path)
    src.row_factory = sqlite3.Row
    where = "" if include_unusable else "WHERE is_usable = 1"
    rows = src.execute(
        f"""
        SELECT id, source_dataset, source_id, province, year, category, batch,
               school_name, major_name, score, rank, quota, source_file,
               source_type, trust_level, is_usable, score_reliable,
               rank_reliable, quality_flags
        FROM admission_records
        {where}
        """
    )
    stats = import_rows(conn, rows, normalize_open_source_row)
    src.close()
    return stats


def import_rows(conn: sqlite3.Connection, rows: Iterable[sqlite3.Row], normalizer) -> dict[str, Any]:
    stats = Counter()
    by_province = Counter()
    by_level = Counter()
    by_year = Counter()
    flag_counts = Counter()
    batch: list[tuple[Any, ...]] = []

    for row in rows:
        stats["read"] += 1
        record = normalizer(row)
        if record is None:
            stats["dropped"] += 1
            continue
        stats["inserted"] += 1
        by_province[record["province"]] += 1
        by_level[record["education_level"]] += 1
        if record["year"] is not None:
            by_year[str(record["year"])] += 1
        for flag in parse_flags(record["quality_flags"]):
            flag_counts[flag] += 1
        batch.append(to_insert_tuple(record))
        if len(batch) >= 5000:
            conn.executemany(INSERT_SQL, batch)
            batch.clear()

    if batch:
        conn.executemany(INSERT_SQL, batch)
    conn.commit()

    return {
        "read": stats["read"],
        "inserted": stats["inserted"],
        "dropped": stats["dropped"],
        "by_province": dict(sorted(by_province.items())),
        "by_year": dict(sorted(by_year.items())),
        "by_education_level": dict(sorted(by_level.items())),
        "quality_flags_top": dict(flag_counts.most_common(30)),
    }


INSERT_SQL = """
    INSERT INTO normalized_admission_records (
        source_type, source_dataset, source_id, source_file, source_trust_level,
        source_priority, province, year, category_raw, category, batch_raw,
        batch, education_level, school_code, school_id, school_name, school_key,
        major_code, major_name, score, rank, quota, plan_count, score_reliable,
        rank_reliable, is_usable, quality_flags, raw_payload, dedupe_key,
        record_hash
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?)
"""


def normalize_official_row(row: sqlite3.Row) -> dict[str, Any] | None:
    school_name = clean_text(row["school_name"])
    if not school_name:
        return None
    province = clean_province(row["province"])
    category_raw = clean_text(row["category"])
    batch_raw = clean_text(row["batch"])
    major_name = clean_major(row["major_name"])
    score = clean_int(row["score"])
    rank = clean_int(row["rank"])
    flags = parse_flags(row["quality_flags"])
    flags.extend(validate_core(province, row["year"], score, rank, school_name, batch_raw, major_name))
    education_level = infer_education_level(batch_raw=batch_raw, school_level="", major_name=major_name)
    if education_level == "未知":
        flags.append("unknown_education_level")
    return build_record(
        source_type="official",
        source_dataset="official_admission",
        source_id=row["source_id"],
        source_file=row["source_file"],
        source_trust_level="official_high",
        province=province,
        year=clean_year(row["year"]),
        category_raw=category_raw,
        category=normalize_category(category_raw),
        batch_raw=batch_raw,
        batch=normalize_batch(batch_raw),
        education_level=education_level,
        school_code=clean_text(row["school_code"]),
        school_id="",
        school_name=school_name,
        major_code=clean_text(row["major_code"]),
        major_name=major_name,
        score=score,
        rank=rank,
        quota=None,
        plan_count=None,
        score_reliable=1 if score is not None else 0,
        rank_reliable=1 if rank is not None else 0,
        is_usable=1,
        quality_flags=flags,
        raw_payload=row["raw_row"],
    )


def normalize_fallback_row(row: sqlite3.Row) -> dict[str, Any] | None:
    school_name = clean_text(row["school_name"])
    if not school_name:
        return None
    province = clean_province(row["province"])
    category_raw = clean_text(row["category"])
    if not category_raw:
        category_raw = clean_text(row["special_group_name"]) or clean_text(row["select_subjects"])
    batch_raw = clean_text(row["batch"])
    major_name = ""
    score = clean_int(row["score"])
    rank = clean_int(row["rank"])
    flags = parse_flags(row["quality_flags"])
    flags.extend(validate_core(province, row["year"], score, rank, school_name, batch_raw, major_name))
    school_level = clean_text(row["school_level"])
    education_level = infer_education_level(batch_raw=batch_raw, school_level=school_level, major_name=major_name)
    if education_level == "未知":
        flags.append("unknown_education_level")
    flags.append("third_party_aggregate_source")
    return build_record(
        source_type="aggregate",
        source_dataset=clean_text(row["source_dataset"]) or "gaokao_api",
        source_id=row["id"],
        source_file=row["source_file"],
        source_trust_level=clean_text(row["trust_level"]) or "aggregate_medium",
        province=province,
        year=clean_year(row["year"]),
        category_raw=category_raw,
        category=normalize_category(category_raw),
        batch_raw=batch_raw,
        batch=normalize_batch(batch_raw),
        education_level=education_level,
        school_code="",
        school_id=clean_text(row["school_id"]),
        school_name=school_name,
        major_code="",
        major_name=major_name,
        score=score,
        rank=rank,
        quota=None,
        plan_count=clean_int(row["plan_count"]),
        score_reliable=1 if score is not None else 0,
        rank_reliable=1 if rank is not None else 0,
        is_usable=1,
        quality_flags=flags,
        raw_payload=row["raw_json"],
        profile={
            "school_id": clean_text(row["school_id"]),
            "province": clean_text(row["school_province"]),
            "city": clean_text(row["school_city"]),
            "school_type": clean_text(row["school_type"]),
            "school_level": school_level,
            "school_nature": clean_text(row["school_nature"]),
        },
    )


def normalize_open_source_row(row: sqlite3.Row) -> dict[str, Any] | None:
    school_name = clean_text(row["school_name"])
    if not school_name:
        return None
    province = clean_province(row["province"])
    category_raw = clean_text(row["category"])
    batch_raw = clean_text(row["batch"])
    major_name = clean_major(row["major_name"])
    score = clean_int(row["score"])
    rank = clean_int(row["rank"])
    flags = parse_flags(row["quality_flags"])
    flags.extend(validate_core(province, row["year"], score, rank, school_name, batch_raw, major_name))
    flags.append("low_trust_source")
    education_level = infer_education_level(batch_raw=batch_raw, school_level="", major_name=major_name)
    if education_level == "未知":
        flags.append("unknown_education_level")
    return build_record(
        source_type="open_source",
        source_dataset=clean_text(row["source_dataset"]) or "gaokao_clean",
        source_id=row["source_id"] if row["source_id"] is not None else row["id"],
        source_file=row["source_file"],
        source_trust_level=clean_text(row["trust_level"]) or "open_source_low",
        province=province,
        year=clean_year(row["year"]),
        category_raw=category_raw,
        category=normalize_category(category_raw),
        batch_raw=batch_raw,
        batch=normalize_batch(batch_raw),
        education_level=education_level,
        school_code="",
        school_id="",
        school_name=school_name,
        major_code="",
        major_name=major_name,
        score=score,
        rank=rank,
        quota=clean_int(row["quota"]),
        plan_count=None,
        score_reliable=1 if row["score_reliable"] and score is not None else 0,
        rank_reliable=1 if row["rank_reliable"] and rank is not None else 0,
        is_usable=1 if row["is_usable"] else 0,
        quality_flags=flags,
        raw_payload=None,
    )


def build_record(**kwargs: Any) -> dict[str, Any]:
    source_type = kwargs["source_type"]
    school_key = normalize_school_key(kwargs["school_name"])
    kwargs["source_priority"] = SOURCE_PRIORITIES[source_type]
    kwargs["school_key"] = school_key
    kwargs["quality_flags"] = json.dumps(sorted(set(kwargs["quality_flags"])), ensure_ascii=False)
    kwargs["dedupe_key"] = make_dedupe_key(kwargs)
    kwargs["record_hash"] = make_hash(
        kwargs["source_type"],
        kwargs["source_dataset"],
        kwargs["source_id"],
        kwargs["province"],
        kwargs["year"],
        kwargs["school_key"],
        kwargs["major_name"],
        kwargs["score"],
        kwargs["rank"],
    )
    return kwargs


def to_insert_tuple(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record["source_type"],
        record["source_dataset"],
        none_to_empty(record.get("source_id")),
        none_to_empty(record.get("source_file")),
        record["source_trust_level"],
        record["source_priority"],
        record["province"],
        record["year"],
        record["category_raw"],
        record["category"],
        record["batch_raw"],
        record["batch"],
        record["education_level"],
        none_to_empty(record.get("school_code")),
        none_to_empty(record.get("school_id")),
        record["school_name"],
        record["school_key"],
        none_to_empty(record.get("major_code")),
        none_to_empty(record.get("major_name")),
        record["score"],
        record["rank"],
        record["quota"],
        record["plan_count"],
        record["score_reliable"],
        record["rank_reliable"],
        record["is_usable"],
        record["quality_flags"],
        record.get("raw_payload"),
        record["dedupe_key"],
        record["record_hash"],
    )


def finalize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_norm_lookup_rank
            ON normalized_admission_records(province, year, rank)
            WHERE is_usable = 1 AND rank_reliable = 1;
        CREATE INDEX idx_norm_lookup_score
            ON normalized_admission_records(province, year, score)
            WHERE is_usable = 1 AND score_reliable = 1;
        CREATE INDEX idx_norm_school
            ON normalized_admission_records(school_key, school_name);
        CREATE INDEX idx_norm_source
            ON normalized_admission_records(source_type, source_dataset);
        CREATE INDEX idx_norm_dedupe
            ON normalized_admission_records(dedupe_key, source_priority);
        """
    )
    build_school_profiles(conn)
    infer_record_levels_from_profiles(conn)
    build_best_records(conn)
    build_quality_summary(conn)
    conn.commit()


def build_school_profiles(conn: sqlite3.Connection) -> None:
    fallback_profiles = load_fallback_profiles(conn)
    rows = conn.execute(
        """
        SELECT school_key, school_name,
               GROUP_CONCAT(DISTINCT source_type) AS source_types,
               COUNT(*) AS record_count,
               SUM(CASE WHEN education_level = '本科' THEN 1 ELSE 0 END) AS benke_count,
               SUM(CASE WHEN education_level = '专科' THEN 1 ELSE 0 END) AS zhuanke_count,
               SUM(CASE WHEN source_type = 'official' THEN 1 ELSE 0 END) AS official_count,
               SUM(CASE WHEN source_type = 'aggregate' THEN 1 ELSE 0 END) AS aggregate_count
        FROM normalized_admission_records
        WHERE school_key <> ''
        GROUP BY school_key
        """
    )
    inserts = []
    for row in rows:
        profile = fallback_profiles.get(row["school_key"], {})
        flags = []
        source_types = sorted((row["source_types"] or "").split(","))
        if not profile:
            flags.append("missing_school_metadata")
        trust_level = "official_or_aggregate" if row["official_count"] or row["aggregate_count"] else "open_source_only"
        inferred_level = infer_profile_level(
            profile.get("school_level", ""),
            row["benke_count"] or 0,
            row["zhuanke_count"] or 0,
        )
        inserts.append(
            (
                row["school_key"],
                choose_school_name(row["school_key"], row["school_name"], profile.get("school_name", "")),
                profile.get("school_id", ""),
                profile.get("province", ""),
                profile.get("city", ""),
                profile.get("school_type", ""),
                profile.get("school_level", ""),
                profile.get("school_nature", ""),
                inferred_level,
                json.dumps(source_types, ensure_ascii=False),
                trust_level,
                row["record_count"],
                json.dumps(flags, ensure_ascii=False),
            )
        )
    conn.executemany(
        """
        INSERT INTO school_profiles (
            school_key, school_name, school_id, province, city, school_type,
            school_level, school_nature, inferred_education_level, source_types,
            trust_level, record_count, quality_flags
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        inserts,
    )
    conn.execute("CREATE INDEX idx_school_profiles_name ON school_profiles(school_name)")
    conn.execute("CREATE INDEX idx_school_profiles_region ON school_profiles(province, city)")


def infer_record_levels_from_profiles(conn: sqlite3.Connection) -> None:
    """Fill unknown record levels only when the school profile is unambiguous."""
    profile_levels = {
        row["school_key"]: row["inferred_education_level"]
        for row in conn.execute(
            """
            SELECT school_key, inferred_education_level
            FROM school_profiles
            WHERE inferred_education_level IN ('本科', '专科')
            """
        )
    }
    updates: list[tuple[str, str, str, int]] = []
    rows = conn.execute(
        """
        SELECT id, province, year, category, batch, school_key, major_name,
               score, rank, quality_flags
        FROM normalized_admission_records
        WHERE education_level = '未知'
        """
    )
    for row in rows:
        level = profile_levels.get(row["school_key"])
        if not level:
            continue
        flags = set(parse_flags(row["quality_flags"]))
        flags.discard("unknown_education_level")
        flags.add("education_level_inferred_from_school_profile")
        record = {
            "province": row["province"],
            "year": row["year"],
            "category": row["category"],
            "batch": row["batch"],
            "education_level": level,
            "school_key": row["school_key"],
            "major_name": row["major_name"],
            "score": row["score"],
            "rank": row["rank"],
        }
        updates.append(
            (
                level,
                json.dumps(sorted(flags), ensure_ascii=False),
                make_dedupe_key(record),
                row["id"],
            )
        )
        if len(updates) >= 5000:
            conn.executemany(
                """
                UPDATE normalized_admission_records
                SET education_level = ?, quality_flags = ?, dedupe_key = ?
                WHERE id = ?
                """,
                updates,
            )
            updates.clear()
    if updates:
        conn.executemany(
            """
            UPDATE normalized_admission_records
            SET education_level = ?, quality_flags = ?, dedupe_key = ?
            WHERE id = ?
            """,
            updates,
        )


def load_fallback_profiles(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    rows = conn.execute(
        """
        SELECT school_key, school_name, school_id, raw_payload
        FROM normalized_admission_records
        WHERE source_type = 'aggregate' AND raw_payload IS NOT NULL
        ORDER BY school_key, id
        """
    )
    profiles: dict[str, dict[str, str]] = {}
    for row in rows:
        key = row["school_key"]
        if key in profiles:
            continue
        payload = safe_json(row["raw_payload"])
        profiles[key] = {
            "school_name": row["school_name"],
            "school_id": clean_text(row["school_id"]),
            "province": first_text(payload, "province_name", "school_province", "province"),
            "city": first_text(payload, "city_name", "city", "school_city"),
            "school_type": first_text(payload, "type_name", "school_type"),
            "school_level": first_text(payload, "level_name", "school_level"),
            "school_nature": first_text(payload, "nature_name", "school_nature"),
        }
    return profiles


def build_best_records(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE admission_best_records AS
        SELECT
            id AS normalized_record_id,
            source_type,
            source_dataset,
            source_id,
            source_file,
            source_trust_level,
            source_priority,
            province,
            year,
            category,
            batch,
            education_level,
            school_code,
            school_id,
            school_name,
            school_key,
            major_code,
            major_name,
            score,
            rank,
            quota,
            plan_count,
            score_reliable,
            rank_reliable,
            quality_flags,
            dedupe_key,
            record_hash
        FROM (
            SELECT n.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY dedupe_key
                       ORDER BY source_priority DESC,
                                rank_reliable DESC,
                                score_reliable DESC,
                                CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
                                CASE WHEN score IS NULL THEN 1 ELSE 0 END,
                                id ASC
                   ) AS rn
            FROM normalized_admission_records n
            WHERE is_usable = 1
        )
        WHERE rn = 1;

        CREATE INDEX idx_best_lookup_rank
            ON admission_best_records(province, year, rank)
            WHERE rank_reliable = 1;
        CREATE INDEX idx_best_lookup_score
            ON admission_best_records(province, year, score)
            WHERE score_reliable = 1;
        CREATE INDEX idx_best_school
            ON admission_best_records(school_key, school_name);
        CREATE INDEX idx_best_level
            ON admission_best_records(education_level, province, year);
        """
    )


def build_quality_summary(conn: sqlite3.Connection) -> None:
    inserts: list[tuple[str, str, int, str]] = []
    for group, sql in {
        "records_by_source": "SELECT source_type AS k, COUNT(*) AS v FROM normalized_admission_records GROUP BY source_type",
        "records_by_level": "SELECT education_level AS k, COUNT(*) AS v FROM normalized_admission_records GROUP BY education_level",
        "best_by_source": "SELECT source_type AS k, COUNT(*) AS v FROM admission_best_records GROUP BY source_type",
        "best_by_level": "SELECT education_level AS k, COUNT(*) AS v FROM admission_best_records GROUP BY education_level",
        "schools_by_trust": "SELECT trust_level AS k, COUNT(*) AS v FROM school_profiles GROUP BY trust_level",
    }.items():
        for row in conn.execute(sql):
            inserts.append((group, row["k"] or "", int(row["v"]), "{}"))

    flag_counter = Counter()
    for row in conn.execute("SELECT quality_flags FROM normalized_admission_records"):
        for flag in parse_flags(row["quality_flags"]):
            flag_counter[flag] += 1
    for flag, count in flag_counter.most_common():
        inserts.append(("quality_flags", flag, count, "{}"))

    conn.executemany(
        """
        INSERT INTO data_quality_summary(metric_group, metric_key, metric_value, extra_json)
        VALUES (?, ?, ?, ?)
        """,
        inserts,
    )


def build_summary(conn: sqlite3.Connection, report: dict[str, Any]) -> dict[str, Any]:
    summary = dict(report)
    summary["totals"] = {
        "normalized_records": scalar(conn, "SELECT COUNT(*) FROM normalized_admission_records"),
        "best_records": scalar(conn, "SELECT COUNT(*) FROM admission_best_records"),
        "school_profiles": scalar(conn, "SELECT COUNT(*) FROM school_profiles"),
        "official_records": scalar(conn, "SELECT COUNT(*) FROM normalized_admission_records WHERE source_type='official'"),
        "aggregate_records": scalar(conn, "SELECT COUNT(*) FROM normalized_admission_records WHERE source_type='aggregate'"),
        "open_source_records": scalar(conn, "SELECT COUNT(*) FROM normalized_admission_records WHERE source_type='open_source'"),
    }
    summary["records_by_source"] = fetch_key_counts(conn, "normalized_admission_records", "source_type")
    summary["records_by_level"] = fetch_key_counts(conn, "normalized_admission_records", "education_level")
    summary["best_by_source"] = fetch_key_counts(conn, "admission_best_records", "source_type")
    summary["best_by_level"] = fetch_key_counts(conn, "admission_best_records", "education_level")
    summary["school_profiles_by_trust"] = fetch_key_counts(conn, "school_profiles", "trust_level")
    summary["top_quality_flags"] = fetch_quality_flags(conn, limit=50)
    summary["province_year_coverage"] = fetch_province_year_coverage(conn)
    summary["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
    return summary


def write_reports(md_path: Path, json_path: Path, summary: dict[str, Any]) -> None:
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Unified Admission Data Report",
        "",
        f"- Output DB: `{summary['output_db']}`",
        f"- Integrity check: `{summary['integrity_check']}`",
        f"- Normalized records: {summary['totals']['normalized_records']:,}",
        f"- Best records: {summary['totals']['best_records']:,}",
        f"- School profiles: {summary['totals']['school_profiles']:,}",
        "",
        "## Records by Source",
        "",
        *format_counts(summary["records_by_source"]),
        "",
        "## Records by Education Level",
        "",
        *format_counts(summary["records_by_level"]),
        "",
        "## Best Records by Source",
        "",
        *format_counts(summary["best_by_source"]),
        "",
        "## School Profiles by Trust",
        "",
        *format_counts(summary["school_profiles_by_trust"]),
        "",
        "## Top Quality Flags",
        "",
        *format_counts(summary["top_quality_flags"]),
        "",
        "## Notes",
        "",
        "- `official` rows come from provincial/official imported files and have highest query priority.",
        "- `aggregate` rows come from the third-party 掌上高考 API fallback and are labelled as third-party aggregate data.",
        "- `open_source` rows come from cleaned open-source snapshots and are low-trust fallback records.",
        "- `admission_best_records` keeps one preferred row per normalized key; `normalized_admission_records` keeps all rows for audit.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def fetch_key_counts(conn: sqlite3.Connection, table: str, column: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT {column} AS k, COUNT(*) AS v FROM {table} GROUP BY {column} ORDER BY v DESC"
    )
    return {row["k"] or "": row["v"] for row in rows}


def fetch_quality_flags(conn: sqlite3.Connection, limit: int) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT metric_key, metric_value
        FROM data_quality_summary
        WHERE metric_group = 'quality_flags'
        ORDER BY metric_value DESC
        LIMIT ?
        """,
        (limit,),
    )
    return {row["metric_key"]: row["metric_value"] for row in rows}


def fetch_province_year_coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT province, year, source_type, COUNT(*) AS records,
               COUNT(DISTINCT school_key) AS schools
        FROM normalized_admission_records
        GROUP BY province, year, source_type
        ORDER BY province, year, source_type
        """
    )
    return [dict(row) for row in rows]


def format_counts(counts: dict[str, int]) -> list[str]:
    if not counts:
        return ["- None"]
    return [f"- {key or '空'}: {value:,}" for key, value in counts.items()]


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0] or 0)


def validate_core(
    province: str,
    year: Any,
    score: int | None,
    rank: int | None,
    school_name: str,
    batch: str,
    major_name: str,
) -> list[str]:
    flags = []
    if province not in PROVINCES:
        flags.append("unknown_province")
    clean_year_value = clean_year(year)
    if clean_year_value is None:
        flags.append("missing_year")
    elif clean_year_value < 2014 or clean_year_value > 2026:
        flags.append("suspicious_year")
    if not batch:
        flags.append("missing_batch")
    if not major_name:
        flags.append("missing_major")
    if score is None:
        flags.append("missing_score")
    elif score < 80 or score > 750:
        flags.append("invalid_score")
    if rank is None:
        flags.append("missing_rank")
    elif rank < 1 or rank > 2_000_000:
        flags.append("invalid_rank")
    if len(school_name) < 4:
        flags.append("suspicious_school_name")
    if not school_name.endswith(SPECIAL_SCHOOL_SUFFIXES) and len(school_name) < 8:
        flags.append("weak_school_name")
    return flags


def infer_education_level(batch_raw: str, school_level: str, major_name: str) -> str:
    text = f"{batch_raw} {school_level} {major_name}"
    if re.search(r"专科|高职|高专", text):
        return "专科"
    if re.search(r"本科|本一|本二|本三|一本|二本|三本|一批|二批|三批|本科批|普通本科", text):
        return "本科"
    if re.search(r"双一流|985|211", school_level):
        return "本科"
    return "未知"


def infer_profile_level(school_level: str, benke_count: int, zhuanke_count: int) -> str:
    if "专科" in school_level or "高职" in school_level:
        return "专科"
    if "本科" in school_level or "985" in school_level or "211" in school_level or "双一流" in school_level:
        return "本科"
    if benke_count and zhuanke_count:
        return "混合"
    if benke_count:
        return "本科"
    if zhuanke_count:
        return "专科"
    return "未知"


def normalize_category(value: str) -> str:
    text = clean_text(value)
    if not text:
        return "未知"
    if "物理" in text:
        return "物理类"
    if "历史" in text:
        return "历史类"
    if "理科" in text or text == "理":
        return "理科"
    if "文科" in text or text == "文":
        return "文科"
    if "综合" in text or "普通类" in text or "普通" == text:
        return "综合/普通类"
    if "艺术" in text:
        return "艺术类"
    if "体育" in text:
        return "体育类"
    return text


def normalize_batch(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    replacements = {
        "本科第一批": "本科一批",
        "第一批本科": "本科一批",
        "本科第二批": "本科二批",
        "第二批本科": "本科二批",
        "高职高专批": "专科批",
        "高职(专科)": "专科批",
        "高职专科": "专科批",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def clean_major(value: Any) -> str:
    text = clean_text(value)
    if text in {"不限", "无", "未知", "--", "-"}:
        return ""
    return text


def clean_province(value: Any) -> str:
    text = clean_text(value)
    replacements = {
        "广西壮族自治区": "广西",
        "内蒙古自治区": "内蒙古",
        "宁夏回族自治区": "宁夏",
        "新疆维吾尔自治区": "新疆",
        "西藏自治区": "西藏",
        "北京市": "北京",
        "天津市": "天津",
        "上海市": "上海",
        "重庆市": "重庆",
    }
    text = replacements.get(text, text)
    if text.endswith("省"):
        text = text[:-1]
    return text


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = clean_text(value)
    if not text or text in {"-", "--", "无", "暂无", "None", "null"}:
        return None
    match = re.search(r"-?\d+", text.replace(",", ""))
    if not match:
        return None
    return int(match.group(0))


def clean_year(value: Any) -> int | None:
    year = clean_int(value)
    if year is None:
        return None
    return year


def parse_flags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [clean_text(flag) for flag in value if clean_text(flag)]
    text = clean_text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(parsed, list):
        return [clean_text(flag) for flag in parsed if clean_text(flag)]
    if isinstance(parsed, dict):
        return [key for key, val in parsed.items() if val]
    return [clean_text(parsed)]


def normalize_school_key(value: str) -> str:
    text = clean_text(value)
    text = re.sub(r"[（(].*?[）)]", "", text)
    text = re.sub(r"\s+", "", text)
    text = text.replace("·", "")
    return text


def make_dedupe_key(record: dict[str, Any]) -> str:
    parts = [
        record["province"],
        record["year"] or "",
        record["category"],
        record["batch"],
        record["education_level"],
        record["school_key"],
        clean_text(record.get("major_name")),
        record["score"] if record["score"] is not None else "",
        record["rank"] if record["rank"] is not None else "",
    ]
    return make_hash(*parts)


def make_hash(*parts: Any) -> str:
    raw = "|".join(clean_text(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def none_to_empty(value: Any) -> str:
    if value is None:
        return ""
    return clean_text(value)


def safe_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = clean_text(payload.get(key))
        if value:
            return value
    return ""


def choose_school_name(school_key: str, grouped_name: str, profile_name: str) -> str:
    if profile_name and normalize_school_key(profile_name) == school_key:
        return profile_name
    return grouped_name


if __name__ == "__main__":
    main()
