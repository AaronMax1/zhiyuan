#!/usr/bin/env python3
"""Download Hebei logged-in 2026 enrollment-plan pages.

The Hebei query site requires a logged-in browser session. Pass the current
browser cookie via environment; the script never writes the cookie to disk.

    HEBEEA_COOKIE='JSESSIONID=...; BIGipServerpool_gkbm_88=...' \
      python3 data-pipeline/download_hebei_zsjh_loggedin.py

Only the regular undergraduate/vocational batches are crawled by default:
本科批 and 专科批. 提前批 is intentionally excluded.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data-pipeline" / "raw" / "hebei_zsjh_loggedin"
OUT_DIR = ROOT / "data-pipeline" / "output"
CSV_PATH = OUT_DIR / "hebei_zsjh_loggedin.csv"
RAW_DB_PATH = OUT_DIR / "hebei_zsjh_loggedin.db"
PLAN_DB_PATH = OUT_DIR / "hebei_2026_plan.db"

MAIN_URL = "https://gk.hebeea.edu.cn:88/xxcx/xxcxzx/zsjh"
ZSJH_URL = "https://gk.hebeea.edu.cn:88/xxcx/xxcxzx/zsjhIframe"

DEFAULT_BATCHES = {
    "本科批": "3",
    "专科批": "9",
}

DEFAULT_CATEGORIES = {
    "历史科目组合": "0",
    "物理科目组合": "B",
}

FIELDS = [
    "year",
    "province",
    "source_system",
    "batch_code",
    "batch_name",
    "category_code",
    "category_name",
    "plan_type_code",
    "plan_type_name",
    "major_sort_mode",
    "page",
    "row_no",
    "school_code",
    "school_name",
    "major_code",
    "major_name",
    "remarks",
    "subject_requirement",
    "plan_count",
    "duration",
    "tuition",
    "tuition_text",
    "operation",
    "raw_cells",
    "source_url",
    "source_file",
]


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_cell = False
        self.cell = ""
        self.row: list[str] = []
        self.rows: list[list[str]] = []
        self.tables: list[list[list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self.in_table = True
            self.rows = []
        elif self.in_table and tag == "tr":
            self.row = []
        elif self.in_table and tag in {"td", "th"}:
            self.in_cell = True
            self.cell = ""

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell += data

    def handle_endtag(self, tag: str) -> None:
        if self.in_table and tag in {"td", "th"}:
            self.row.append(" ".join(html.unescape(self.cell).split()))
            self.in_cell = False
        elif self.in_table and tag == "tr":
            if any(self.row):
                self.rows.append(self.row)
        elif tag == "table" and self.in_table:
            if self.rows:
                self.tables.append(self.rows)
            self.in_table = False


def request(cookie: str, data: dict[str, str] | None = None, timeout: int = 45) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148 Safari/537.36",
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Origin": "https://gk.hebeea.edu.cn:88",
        "Referer": MAIN_URL,
    }
    body = None
    method = "GET"
    url = MAIN_URL
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        method = "POST"
        url = ZSJH_URL
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="ignore")


def fetch_or_read(cookie: str, target: Path, data: dict[str, str], sleep: float) -> tuple[str, bool]:
    if target.exists() and target.stat().st_size > 1000:
        return target.read_text(encoding="utf-8", errors="ignore"), True
    page_html = request(cookie, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page_html, encoding="utf-8")
    if sleep > 0:
        time.sleep(sleep)
    return page_html, False


def build_form(batch_code: str, category_code: str, page: int) -> dict[str, str]:
    return {
        "pcdm": batch_code,
        "jhxzdm": "0",
        "kldm": category_code,
        "zyms": "ZYPX",
        "page": str(page),
        "yxjblxmc": "",
        "yxmc": "",
        "zymc": "",
    }


def parse_total_pages(page_html: str) -> int | None:
    text = html.unescape(page_html)
    for pattern in [
        r"共\s*(\d+)\s*页",
        r"总页数[:：]?\s*(\d+)",
        r'var\s+pages\s*=\s*["\']?(\d+)',
        r'pages\s*[:=]\s*["\']?(\d+)',
    ]:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    page_numbers = [int(value) for value in re.findall(r"(?:page|goPage)\((\d+)\)", text)]
    return max(page_numbers) if page_numbers else None


def parse_page(page_html: str, meta: dict[str, str]) -> list[dict[str, str]]:
    parser = TableParser()
    parser.feed(page_html)
    table = max(parser.tables, key=len, default=[])
    rows: list[dict[str, str]] = []
    for cells in table:
        if len(cells) < 9:
            continue
        if not re.fullmatch(r"\d+", cells[0] or ""):
            continue
        row = dict(meta)
        row.update(normalize_cells(cells))
        rows.append(row)
    return rows


def normalize_cells(cells: list[str]) -> dict[str, str]:
    padded = cells + [""] * 12
    tuition_text = padded[9]
    tuition = first_int(tuition_text)
    return {
        "row_no": padded[0],
        "school_code": padded[1],
        "school_name": padded[2],
        "major_code": padded[3],
        "major_name": padded[4],
        "remarks": padded[5],
        "subject_requirement": padded[6],
        "plan_count": first_int(padded[7]),
        "duration": padded[8],
        "tuition": tuition,
        "tuition_text": tuition_text,
        "operation": padded[10],
        "raw_cells": "|".join(cells),
    }


def first_int(value: str) -> str:
    match = re.search(r"\d+", value.replace(",", "").replace("，", ""))
    return match.group(0) if match else ""


def validate_login(page_html: str) -> None:
    if "考生登录" in page_html and ("请输入密码" in page_html or "登录" in page_html):
        raise RuntimeError("Login session expired; refresh HEBEEA_COOKIE and rerun.")


def write_outputs(rows: list[dict[str, str]], write_plan_db: bool) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    with sqlite3.connect(RAW_DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS hebei_zsjh_loggedin")
        conn.execute(
            """
            CREATE TABLE hebei_zsjh_loggedin (
                year INTEGER,
                province TEXT,
                source_system TEXT,
                batch_code TEXT,
                batch_name TEXT,
                category_code TEXT,
                category_name TEXT,
                plan_type_code TEXT,
                plan_type_name TEXT,
                major_sort_mode TEXT,
                page INTEGER,
                row_no INTEGER,
                school_code TEXT,
                school_name TEXT,
                major_code TEXT,
                major_name TEXT,
                remarks TEXT,
                subject_requirement TEXT,
                plan_count INTEGER,
                duration TEXT,
                tuition INTEGER,
                tuition_text TEXT,
                operation TEXT,
                raw_cells TEXT,
                source_url TEXT,
                source_file TEXT
            )
            """
        )
        conn.executemany(
            f"INSERT INTO hebei_zsjh_loggedin ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)})",
            [[row.get(field, "") for field in FIELDS] for row in rows],
        )
        conn.execute("CREATE INDEX idx_hebei_zsjh_key ON hebei_zsjh_loggedin(batch_name, category_name, school_code, major_code)")
        conn.execute("CREATE INDEX idx_hebei_zsjh_school_major ON hebei_zsjh_loggedin(school_name, major_name)")

    if write_plan_db:
        write_plan_outputs(rows)


def write_plan_outputs(rows: list[dict[str, str]]) -> None:
    with sqlite3.connect(PLAN_DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS hebei_2026_plan")
        conn.execute(
            """
            CREATE TABLE hebei_2026_plan (
                year INTEGER NOT NULL,
                province TEXT NOT NULL,
                batch_name TEXT NOT NULL,
                category_name TEXT NOT NULL,
                school_code TEXT NOT NULL,
                school_name TEXT NOT NULL,
                major_code TEXT NOT NULL,
                major_name TEXT NOT NULL,
                plan_count INTEGER,
                tuition INTEGER,
                tuition_text TEXT,
                duration TEXT,
                campus TEXT,
                subject_requirement TEXT,
                remarks TEXT,
                source_system TEXT NOT NULL,
                source_url TEXT,
                source_file TEXT,
                confidence TEXT NOT NULL,
                is_mock INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        plan_fields = [
            "year",
            "province",
            "batch_name",
            "category_name",
            "school_code",
            "school_name",
            "major_code",
            "major_name",
            "plan_count",
            "tuition",
            "tuition_text",
            "duration",
            "campus",
            "subject_requirement",
            "remarks",
            "source_system",
            "source_url",
            "source_file",
            "confidence",
            "is_mock",
        ]
        payload: list[list[Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for row in rows:
            key = (row["batch_name"], row["category_name"], row["school_code"], row["major_code"])
            if key in seen:
                continue
            seen.add(key)
            payload.append(
                [
                    row["year"],
                    row["province"],
                    row["batch_name"],
                    row["category_name"],
                    row["school_code"],
                    row["school_name"],
                    row["major_code"],
                    row["major_name"],
                    row["plan_count"],
                    row["tuition"],
                    row["tuition_text"],
                    row["duration"],
                    "",
                    row["subject_requirement"],
                    row["remarks"],
                    row["source_system"],
                    row["source_url"],
                    row["source_file"],
                    "official_hebeea_2026_zsjh",
                    0,
                ]
            )
        conn.executemany(
            f"INSERT INTO hebei_2026_plan ({','.join(plan_fields)}) VALUES ({','.join('?' for _ in plan_fields)})",
            payload,
        )
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_hebei_2026_plan_key
            ON hebei_2026_plan(batch_name, category_name, school_code, major_code)
            """
        )
        conn.execute("CREATE INDEX idx_hebei_2026_plan_school_major ON hebei_2026_plan(school_name, major_name)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batches", nargs="+", default=["本科批", "专科批"])
    parser.add_argument("--categories", nargs="+", default=["历史科目组合", "物理科目组合"])
    parser.add_argument("--batch-code", action="append", default=[], help="Override/add batch as 名称=代码")
    parser.add_argument("--category-code", action="append", default=[], help="Override/add category as 名称=代码")
    parser.add_argument("--max-pages", type=int, default=5000)
    parser.add_argument("--stop-after-empty", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--probe", action="store_true", help="Fetch only page 1 for each selected batch/category.")
    parser.add_argument("--no-plan-db", action="store_true", help="Do not replace data-pipeline/output/hebei_2026_plan.db.")
    return parser.parse_args()


def apply_overrides(defaults: dict[str, str], overrides: list[str]) -> dict[str, str]:
    result = dict(defaults)
    for override in overrides:
        if "=" not in override:
            raise SystemExit(f"Invalid override {override!r}; expected 名称=代码")
        name, code = override.split("=", 1)
        result[name.strip()] = code.strip()
    return result


def main() -> None:
    args = parse_args()
    cookie = os.environ.get("HEBEEA_COOKIE", "").strip()
    if not cookie:
        raise SystemExit("Set HEBEEA_COOKIE from your logged-in browser request cookie.")

    batches = apply_overrides(DEFAULT_BATCHES, args.batch_code)
    categories = apply_overrides(DEFAULT_CATEGORIES, args.category_code)
    target_batches = {name: code for name, code in batches.items() if name in args.batches}
    target_categories = {name: code for name, code in categories.items() if name in args.categories}
    print("Batches:", target_batches)
    print("Categories:", target_categories)

    all_rows: list[dict[str, str]] = []
    for batch_name, batch_code in target_batches.items():
        for category_name, category_code in target_categories.items():
            total_pages = 1 if args.probe else args.max_pages
            empty_streak = 0
            for page in range(1, total_pages + 1):
                form = build_form(batch_code, category_code, page)
                safe_category_code = re.sub(r"[^0-9A-Za-z_-]+", "_", category_code or "blank")
                out = RAW_DIR / f"{batch_name}_{category_name}_{safe_category_code}_page_{page:04d}.html"
                page_html, from_cache = fetch_or_read(cookie, out, form, args.sleep)
                validate_login(page_html)
                if page == 1 and not args.probe:
                    total_pages = min(args.max_pages, parse_total_pages(page_html) or args.max_pages)
                meta = {
                    "year": "2026",
                    "province": "河北",
                    "source_system": "gk.hebeea.edu.cn:88/xxcx/xxcxzx/zsjhIframe",
                    "batch_code": batch_code,
                    "batch_name": batch_name,
                    "category_code": category_code,
                    "category_name": category_name,
                    "plan_type_code": "0",
                    "plan_type_name": "非定向",
                    "major_sort_mode": "ZYPX",
                    "page": str(page),
                    "source_url": ZSJH_URL,
                    "source_file": str(out.relative_to(ROOT)),
                }
                rows = parse_page(page_html, meta)
                all_rows.extend(rows)
                if page == 1 or page % args.progress_every == 0 or page >= total_pages or not rows:
                    cache_label = "cache" if from_cache else "fetch"
                    print(f"{batch_name} {category_name} page={page}/{total_pages} rows={len(rows)} total={len(all_rows)} {cache_label}", flush=True)
                if not rows:
                    empty_streak += 1
                    if empty_streak >= args.stop_after_empty:
                        break
                else:
                    empty_streak = 0
                if page >= total_pages:
                    break

    write_outputs(all_rows, not args.no_plan_db and not args.probe)
    print(f"Wrote {CSV_PATH.relative_to(ROOT)} rows={len(all_rows)}")
    print(f"Wrote {RAW_DB_PATH.relative_to(ROOT)}")
    if not args.no_plan_db and not args.probe:
        print(f"Wrote {PLAN_DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
