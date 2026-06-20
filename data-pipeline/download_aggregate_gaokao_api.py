#!/usr/bin/env python3
"""Download and import aggregate gaokao admission data.

This pipeline intentionally writes to a separate fallback database. It should
not be mixed into official_admission.db because the source is a public
third-party aggregate API, not a provincial examination authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data-pipeline" / "raw" / "aggregate" / "gaokao_api"
OUT_DB = ROOT / "data-pipeline" / "output" / "fallback_admission.db"
REPORT_MD = ROOT / "data-pipeline" / "output" / "fallback_import_report.md"
REPORT_JSON = ROOT / "data-pipeline" / "output" / "fallback_import_report.json"

API_BASE = "https://api.zjzw.cn/web/api/"
PROVINCES = {
    12: "天津",
    15: "内蒙古",
    22: "吉林",
    34: "安徽",
    36: "江西",
    41: "河南",
    54: "西藏",
    63: "青海",
}
STAGES = {
    "province_score": "apidata/api/gk/score/province",
}

MAX_API_WINDOW_ROWS = 4000

# 2025 gap provinces use 掌上高考 as fallback. The API returns empty data after
# about 200 pages for broad queries, so large provinces must be queried by
# narrower filters. These ids are from the gaokao.cn frontend and verified by
# probing the public API.
NEW_GAOKAO_TYPE_IDS = {
    15: ["2073", "2074"],  # 内蒙古
    22: ["2073", "2074"],  # 吉林
    34: ["2073", "2074"],  # 安徽
    36: ["2073", "2074"],  # 江西
    41: ["2073", "2074"],  # 河南
    63: ["2073", "2074"],  # 青海
}
OLD_GAOKAO_TYPE_IDS = {
    54: ["1", "2"],  # 西藏
}

# Conservative candidate set for 普通类 score/province data. The downloader
# only keeps combinations where the API reports rows.
BATCH_ID_CANDIDATES = [
    "6",   # 本科提前批
    "7",   # 本科一批
    "8",   # 本科二批
    "10",  # 专科批
    "11",  # 专科提前批
    "12",  # 国家专项计划本科批
    "13",  # 地方专项计划本科批
    "14",  # 本科批
    "15",  # 普通类提前批
    "36",  # 本科提前批A段
    "37",  # 本科提前批B段
    "39",  # 提前批专项计划
    "43",  # 专项批
    "74",  # 国家专项批
    "79",  # 地方专项批
    "81",  # 高校专项批
]

STATIC_SPLIT_FILTERS = {
    # Complete by type, verified: 3105 + 1660 = 4765.
    (22, 2025): [
        {"local_type_id": "2073"},
        {"local_type_id": "2074"},
    ],
    # Partial but much better than broad 4000-row window; remaining gaps are
    # reported and can be filled after more batch ids or score-band filters are
    # verified.
    (15, 2025): [
        {"local_type_id": "2074"},
        {"local_type_id": "2073", "local_batch_id": "10"},
        {"local_type_id": "2073", "local_batch_id": "14"},
        {"local_type_id": "2073", "local_batch_id": "36"},
        {"local_type_id": "2073", "local_batch_id": "37"},
    ],
    (34, 2025): [
        {"local_type_id": "2074"},
        {"local_type_id": "2073", "local_batch_id": "10"},
        {"local_type_id": "2073", "local_batch_id": "14"},
    ],
    (36, 2025): [
        {"local_type_id": "2074"},
        {"local_type_id": "2073", "local_batch_id": "10"},
        {"local_type_id": "2073", "local_batch_id": "14"},
    ],
    (41, 2025): [
        {"local_type_id": "2074"},
        {"local_type_id": "2073", "local_batch_id": "10"},
        {"local_type_id": "2073", "local_batch_id": "14"},
    ],
}


class RateLimitExhausted(RuntimeError):
    pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--province-id", type=int, action="append", default=[])
    parser.add_argument("--year", type=int, action="append", default=[])
    parser.add_argument("--stage", choices=sorted(STAGES), default="province_score")
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--rate-limit-sleep", type=float, default=60.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--split-large", action="store_true", help="split large province queries by type/batch filters")
    parser.add_argument("--max-window-rows", type=int, default=MAX_API_WINDOW_ROWS)
    args = parser.parse_args()

    province_ids = args.province_id or sorted(PROVINCES)
    years = args.year or [2025]

    raw_files = []
    stopped_by_rate_limit = False
    try:
        for province_id in province_ids:
            if province_id not in PROVINCES:
                raise SystemExit(f"unknown province id: {province_id}")
            for year in years:
                raw_files.extend(download_stage_with_optional_split(args.stage, province_id, year, args))
    except RateLimitExhausted as exc:
        stopped_by_rate_limit = True
        print(f"[STOP] {exc}", flush=True)

    if not args.download_only:
        build_db()
    print(f"Downloaded pages: {len(raw_files)}")
    print(f"Raw dir: {RAW_DIR}")
    if not args.download_only:
        print(f"DB: {OUT_DB}")
        print(f"Report: {REPORT_MD}")
    if stopped_by_rate_limit:
        print("Stopped early because the aggregate API rate limit persisted. Re-run later; cached pages will be skipped.")


def download_stage_with_optional_split(stage: str, province_id: int, year: int, args: argparse.Namespace) -> list[Path]:
    if not args.split_large:
        return download_stage(stage, province_id, year, args, {})

    static_filters = STATIC_SPLIT_FILTERS.get((province_id, year))
    if static_filters:
        print(f"{PROVINCES[province_id]} {year} {stage}: using {len(static_filters)} static split queries", flush=True)
        raw_files: list[Path] = []
        for query in static_filters:
            raw_files.extend(download_stage(stage, province_id, year, args, query))
        return raw_files

    total = probe_count(stage, province_id, year, args, {})
    if total <= args.max_window_rows:
        return download_stage(stage, province_id, year, args, {})

    filters = discover_filters(stage, province_id, year, args)
    if not filters:
        print(f"[WARN] no split filters discovered for {PROVINCES[province_id]} {year}; using broad query", flush=True)
        return download_stage(stage, province_id, year, args, {})

    print(f"{PROVINCES[province_id]} {year} {stage}: broad query has {total} rows; using {len(filters)} split queries", flush=True)
    raw_files: list[Path] = []
    for query in filters:
        raw_files.extend(download_stage(stage, province_id, year, args, query))
    return raw_files


def discover_filters(stage: str, province_id: int, year: int, args: argparse.Namespace) -> list[dict[str, str]]:
    type_ids = NEW_GAOKAO_TYPE_IDS.get(province_id) or OLD_GAOKAO_TYPE_IDS.get(province_id) or []
    filters: list[dict[str, str]] = []
    for type_id in type_ids:
        type_query = {"local_type_id": type_id}
        type_total = probe_count(stage, province_id, year, args, type_query)
        if type_total == 0:
            continue
        if type_total <= args.max_window_rows:
            filters.append(type_query)
            continue
        batch_filters = []
        batch_total = 0
        for batch_id in BATCH_ID_CANDIDATES:
            query = {"local_type_id": type_id, "local_batch_id": batch_id}
            count = probe_count(stage, province_id, year, args, query)
            if count:
                batch_filters.append(query)
                batch_total += count
        if batch_filters:
            if batch_total < type_total:
                print(
                    f"[WARN] {PROVINCES[province_id]} {year} type={type_id} split batches cover {batch_total}/{type_total}; more batch ids may be needed",
                    flush=True,
                )
            filters.extend(batch_filters)
        else:
            print(f"[WARN] {PROVINCES[province_id]} {year} type={type_id} still has {type_total} rows and no batch split", flush=True)
            filters.append(type_query)
    return filters


def probe_count(stage: str, province_id: int, year: int, args: argparse.Namespace, extra_params: dict[str, str]) -> int:
    target = meta_path(stage, province_id, year, extra_params)
    if target.exists() and not args.force:
        data = json.loads(target.read_text(encoding="utf-8"))
    else:
        url = make_url(stage, province_id, year, 1, 1, extra_params)
        data = fetch_json(url, args.timeout, args.rate_limit_sleep)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(args.sleep)
    payload = data.get("data")
    if not isinstance(payload, dict):
        return 0
    return int(payload.get("numFound") or 0)


def download_stage(stage: str, province_id: int, year: int, args: argparse.Namespace, extra_params: dict[str, str]) -> list[Path]:
    page = 1
    total_pages = None
    raw_files: list[Path] = []
    while total_pages is None or page <= total_pages:
        target = raw_path(stage, province_id, year, page, extra_params)
        if target.exists() and not args.force:
            data = json.loads(target.read_text(encoding="utf-8"))
            if not is_valid_page(data):
                url = make_url(stage, province_id, year, page, args.page_size, extra_params)
                data = fetch_json(url, args.timeout, args.rate_limit_sleep)
                target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                time.sleep(args.sleep)
        else:
            url = make_url(stage, province_id, year, page, args.page_size, extra_params)
            data = fetch_json(url, args.timeout, args.rate_limit_sleep)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            time.sleep(args.sleep)
        raw_files.append(target)
        num_found = int(((data.get("data") or {}).get("numFound") or 0))
        items = (data.get("data") or {}).get("item") or []
        if total_pages is None:
            total_pages = max(1, math.ceil(num_found / args.page_size))
            suffix = f" {extra_params}" if extra_params else ""
            print(f"{PROVINCES[province_id]} {year} {stage}{suffix}: {num_found} rows, {total_pages} pages", flush=True)
        if not items:
            break
        if page % 50 == 0:
            print(f"{PROVINCES[province_id]} {year} {stage}: page {page}/{total_pages}", flush=True)
        page += 1
    return raw_files


def is_valid_page(data: dict[str, Any]) -> bool:
    payload = data.get("data")
    return isinstance(payload, dict) and "numFound" in payload and isinstance(payload.get("item"), list)


def make_url(stage: str, province_id: int, year: int, page: int, size: int, extra_params: dict[str, str] | None = None) -> str:
    params = {
        "uri": STAGES[stage],
        "local_province_id": province_id,
        "year": year,
        "page": page,
        "size": size,
    }
    if extra_params:
        params.update(extra_params)
    return f"{API_BASE}?{urllib.parse.urlencode(params)}"


def raw_path(stage: str, province_id: int, year: int, page: int, extra_params: dict[str, str] | None = None) -> Path:
    province = PROVINCES[province_id]
    base = RAW_DIR / stage / province / str(year)
    if extra_params:
        base = base / split_name(extra_params)
    return base / f"page-{page:04d}.json"


def meta_path(stage: str, province_id: int, year: int, extra_params: dict[str, str]) -> Path:
    province = PROVINCES[province_id]
    return RAW_DIR / stage / province / str(year) / "_meta" / f"{split_name(extra_params) or 'broad'}.json"


def split_name(extra_params: dict[str, str] | None) -> str:
    if not extra_params:
        return ""
    return "__".join(f"{key}-{value}" for key, value in sorted(extra_params.items()))


def query_params_from_path(path: Path) -> dict[str, str]:
    parent = path.parent.name
    if parent == path.parts[-2]:
        return {}
    if parent.startswith("page-"):
        return {}
    if parent == "_meta":
        return {}
    params = {}
    for part in parent.split("__"):
        if "-" in part:
            key, value = part.split("-", 1)
            params[key] = value
    return params


def fetch_json(url: str, timeout: int, rate_limit_sleep: float, retries: int = 8) -> dict[str, Any]:
    last = ""
    for attempt in range(1, retries + 1):
        try:
            raw = subprocess.check_output([
                "curl", "-L", "--fail", "--silent", "--show-error",
                "--connect-timeout", "8", "--max-time", str(timeout),
                "-A", "Mozilla/5.0", "-e", "https://www.gaokao.cn/", url,
            ])
            data = json.loads(raw.decode("utf-8"))
            if data.get("code") != "0000":
                if str(data.get("code")) == "1069":
                    last = f"api code 1069: {data.get('message')}"
                    print(f"[RATE] sleeping {rate_limit_sleep:.0f}s: {data.get('message')}", flush=True)
                    time.sleep(rate_limit_sleep)
                    continue
                raise RuntimeError(f"api code {data.get('code')}: {data.get('message')}")
            if not isinstance(data.get("data"), dict):
                last = f"invalid api payload: {data.get('data')!r}"
                if attempt < retries:
                    time.sleep(max(rate_limit_sleep, attempt))
                    continue
                raise RuntimeError(last)
            return data
        except Exception as exc:
            last = str(exc)
            if attempt < retries:
                time.sleep(attempt)
    if "1069" in last or "访问太过频繁" in last:
        raise RateLimitExhausted(f"persistent rate limit at {url}")
    raise RuntimeError(f"fetch failed: {url} {last}")


def build_db() -> None:
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()
    conn = sqlite3.connect(OUT_DB)
    create_schema(conn)
    total = 0
    for path in import_paths("province_score"):
        total += import_province_score_file(conn, path)
    create_indexes(conn)
    conn.commit()
    write_report(conn, total)
    conn.close()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE fallback_admission_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_dataset TEXT NOT NULL,
            source_url TEXT NOT NULL,
            source_file TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            province TEXT NOT NULL,
            province_id INTEGER,
            year INTEGER,
            category TEXT,
            batch TEXT,
            school_id INTEGER,
            school_name TEXT,
            school_province TEXT,
            school_city TEXT,
            school_type TEXT,
            school_level TEXT,
            school_nature TEXT,
            special_group TEXT,
            special_group_name TEXT,
            select_subjects TEXT,
            score INTEGER,
            rank INTEGER,
            plan_count INTEGER,
            zslx_name TEXT,
            raw_json TEXT NOT NULL,
            quality_flags TEXT NOT NULL,
            record_hash TEXT NOT NULL
        );
        """
    )


def import_paths(stage: str) -> list[Path]:
    stage_dir = RAW_DIR / stage
    paths = sorted(stage_dir.glob("*/*/**/page-*.json"))
    split_keys = {
        (path.parts[-4], path.parts[-3])
        for path in paths
        if path.parent.parent.name not in {stage, "province_score"} and path.parent.name != "_meta"
    }
    selected = []
    for path in paths:
        province = path.parts[-3] if path.parent.name.startswith("page-") else None
        if path.parent.parent.name == stage:
            selected.append(path)
            continue
        if path.parent.name == "_meta":
            continue
        if path.parent.name.startswith("local_"):
            selected.append(path)
            continue
        # Root broad pages: .../<province>/<year>/page-0001.json.
        if path.parent.name.isdigit():
            key = (path.parts[-3], path.parts[-2])
            if key not in split_keys:
                selected.append(path)
            continue
        selected.append(path)
    return selected


def import_province_score_file(conn: sqlite3.Connection, path: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = ((data.get("data") or {}).get("item") or [])
    parts = path.parts
    if path.parent.name.isdigit():
        province = parts[-3]
        year = int(parts[-2])
    else:
        province = parts[-4]
        year = int(parts[-3])
    page = int(path.stem.split("-")[-1])
    province_id = next((pid for pid, name in PROVINCES.items() if name == province), None)
    source_url = make_url("province_score", province_id or 0, year, page, len(items) or 20, query_params_from_path(path))
    rows = []
    for item in items:
        flags = []
        rank = to_int(item.get("min_section"))
        score = to_int(item.get("min"))
        if rank is None:
            flags.append("missing_rank")
        if score is None:
            flags.append("missing_score")
        record_hash = stable_record_hash(item, province, year)
        rows.append((
            "gaokao.cn-api",
            source_url,
            str(path.relative_to(ROOT)),
            "aggregate_public",
            str(item.get("local_province_name") or province),
            to_int(item.get("local_province_id")) or province_id,
            to_int(item.get("year")) or year,
            str(item.get("local_type_name") or item.get("type_name") or ""),
            str(item.get("local_batch_name") or ""),
            to_int(item.get("school_id")),
            str(item.get("name") or ""),
            str(item.get("province_name") or ""),
            str(item.get("city_name") or ""),
            str(item.get("type_name") or ""),
            str(item.get("level_name") or ""),
            str(item.get("nature_name") or ""),
            str(item.get("special_group") or ""),
            str(item.get("sg_name") or ""),
            str(item.get("sg_info") or ""),
            score,
            rank,
            to_int(item.get("num")),
            str(item.get("zslx_name") or ""),
            json.dumps(item, ensure_ascii=False),
            json.dumps(flags, ensure_ascii=False),
            record_hash,
        ))
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO fallback_admission_records (
            source_dataset, source_url, source_file, trust_level,
            province, province_id, year, category, batch,
            school_id, school_name, school_province, school_city,
            school_type, school_level, school_nature,
            special_group, special_group_name, select_subjects,
            score, rank, plan_count, zslx_name, raw_json, quality_flags, record_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return conn.total_changes - before


def stable_record_hash(item: dict[str, Any], province: str, year: int) -> str:
    raw = json.dumps({"province": province, "year": year, "item": item}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_fallback_lookup_score
            ON fallback_admission_records(province, year, score);
        CREATE INDEX idx_fallback_lookup_rank
            ON fallback_admission_records(province, year, rank);
        CREATE INDEX idx_fallback_school
            ON fallback_admission_records(school_name);
        """
    )


def write_report(conn: sqlite3.Connection, total: int) -> None:
    rows = [dict(zip(["province", "records", "rank_records", "years"], row)) for row in conn.execute(
        """
        SELECT province,
               count(*) AS records,
               sum(rank IS NOT NULL) AS rank_records,
               group_concat(DISTINCT year) AS years
        FROM fallback_admission_records
        GROUP BY province
        ORDER BY province
        """
    )]
    for row in rows:
        years = [int(year) for year in str(row["years"]).split(",") if year]
        expected = 0
        for year in years:
            expected += broad_num_found(str(row["province"]), year)
        row["api_num_found"] = expected or ""
        row["gap"] = (expected - int(row["records"])) if expected else ""
    report = {"records": total, "by_province": rows}
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Fallback 聚合数据导入报告",
        "",
        "来源：`api.zjzw.cn` / 掌上高考公开接口。该库只作为官方缺口 fallback，不写入官方库。",
        "",
        f"- 总记录：{total}",
        "",
        "| 省份 | 导入记录 | API numFound | 缺口 | 含位次 | 年份 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['province']} | {row['records']} | {row['api_num_found']} | {row['gap']} | {row['rank_records']} | {row['years']} |"
        )
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def broad_num_found(province: str, year: int) -> int:
    page = RAW_DIR / "province_score" / province / str(year) / "page-0001.json"
    if not page.exists():
        return 0
    try:
        data = json.loads(page.read_text(encoding="utf-8"))
        payload = data.get("data")
        if isinstance(payload, dict):
            return int(payload.get("numFound") or 0)
    except Exception:
        return 0
    return 0


def to_int(value: Any) -> int | None:
    if value is None or value in {"", "-", "0"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    main()
