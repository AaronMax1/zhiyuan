#!/usr/bin/env python3
"""Download Hebei logged-in historical admission pages.

The Hebei information-query site requires a candidate login session. This script
does not store credentials or cookies; pass the browser cookie via environment:

    HEBEEA_COOKIE='JSESSIONID=...; BIGipServerpool_gkbm_88=...' \
      python3 data-pipeline/download_hebei_lnwc_loggedin.py

It downloads undergraduate and vocational batches for physics/history groups,
keeps raw HTML, and writes CSV + SQLite outputs.
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
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data-pipeline" / "raw" / "hebei_lnwc_loggedin"
OUT_DIR = ROOT / "data-pipeline" / "output"
CSV_PATH = OUT_DIR / "hebei_lnwc_loggedin.csv"
DB_PATH = OUT_DIR / "hebei_lnwc_loggedin.db"
MAIN_URL = "https://gk.hebeea.edu.cn:88/xxcx/xxcxzx/main"
LNWC_URL = "https://gk.hebeea.edu.cn:88/xxcx/xxcxzx/lnwc"


DEFAULT_BATCHES = {
    "本科批": "3",
    "专科批": "9",
}

DEFAULT_CATEGORIES = {
    "物理科目组合": "B0",
    "历史科目组合": "00",
}

FIELDS = [
    "source_province",
    "source_system",
    "query_year",
    "batch_code",
    "batch_name",
    "category_code",
    "category_name",
    "page",
    "row_no",
    "year",
    "school_code",
    "school_name",
    "major_code",
    "major_name",
    "min_score",
    "avg_score",
    "min_rank",
    "volunteer_type",
    "raw_cells",
    "source_url",
    "source_file",
]


@dataclass
class Option:
    value: str
    label: str


class SelectParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current_select = ""
        self.current_option: tuple[str, list[str]] | None = None
        self.options: dict[str, list[Option]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "select":
            self.current_select = data.get("name") or data.get("id") or ""
            if self.current_select:
                self.options.setdefault(self.current_select, [])
        elif tag == "option" and self.current_select:
            self.current_option = (data.get("value") or "", [])

    def handle_data(self, data: str) -> None:
        if self.current_option is not None:
            self.current_option[1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "option" and self.current_option is not None and self.current_select:
            value, chunks = self.current_option
            label = " ".join("".join(chunks).split())
            if label:
                self.options.setdefault(self.current_select, []).append(Option(value, label))
            self.current_option = None
        elif tag == "select":
            self.current_select = ""


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
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
            self.in_row = True
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
            self.in_row = False
        elif tag == "table" and self.in_table:
            if self.rows:
                self.tables.append(self.rows)
            self.in_table = False


def request(url: str, cookie: str, data: dict[str, str] | None = None) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148 Safari/537.36",
        "Cookie": cookie,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Origin": "https://gk.hebeea.edu.cn:88",
        "Referer": MAIN_URL,
    }
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=35) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="ignore")


def fetch_or_read(url: str, cookie: str, target: Path, data: dict[str, str] | None = None, sleep: float = 0.0) -> tuple[str, bool]:
    if target.exists() and target.stat().st_size > 1000:
        return target.read_text(encoding="utf-8", errors="ignore"), True
    page_html = request(url, cookie, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page_html, encoding="utf-8")
    if sleep > 0:
        time.sleep(sleep)
    return page_html, False


def discover_options(cookie: str) -> tuple[dict[str, str], dict[str, str], str]:
    main_html = request(MAIN_URL, cookie)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / "main.html").write_text(main_html, encoding="utf-8")
    parser = SelectParser()
    parser.feed(main_html)

    batch_options = find_options(parser.options, ["lspcdm", "pcdm", "batch"], DEFAULT_BATCHES)
    category_options = find_options(parser.options, ["lskldm", "kldm", "kl"], DEFAULT_CATEGORIES)
    form_id = find_form_id(main_html)
    return batch_options, category_options, form_id


def find_options(options: dict[str, list[Option]], names: list[str], defaults: dict[str, str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for select_name, opts in options.items():
        if not any(name.lower() in select_name.lower() for name in names):
            continue
        for opt in opts:
            if not opt.value:
                continue
            for target in defaults:
                if target in opt.label:
                    found[target] = opt.value
    return found or dict(defaults)


def find_form_id(page: str) -> str:
    for pattern in [
        r'name=["\']id["\']\s+value=["\']([^"\']+)',
        r'value=["\']([^"\']+)["\']\s+name=["\']id["\']',
        r'id=([0-9a-fA-F-]{20,})',
    ]:
        match = re.search(pattern, page)
        if match:
            return match.group(1)
    return ""


def build_form(form_id: str, batch_code: str, category_code: str, category_name: str, page: int) -> dict[str, str]:
    return {
        "id": form_id,
        "pcdm": "",
        "jhxzdm": "",
        "kldm": "",
        "yxsl": "",
        "lsnf": "",
        "lspcdm": batch_code,
        "lskldm": category_code,
        "lsklmc": category_name,
        "lsyxmc": "",
        "lszymc": "",
        "lskswc": "",
        "lsjswc": "",
        "page": str(page),
    }


def parse_page(page_html: str, meta: dict[str, str]) -> list[dict[str, str]]:
    parser = TableParser()
    parser.feed(page_html)
    table = max(parser.tables, key=len, default=[])
    rows: list[dict[str, str]] = []
    for cells in table:
        if len(cells) < 8:
            continue
        if not re.fullmatch(r"\d+", cells[0] or ""):
            continue
        normalized = normalize_cells(cells)
        row = dict(meta)
        row.update(normalized)
        rows.append(row)
    return rows


def normalize_cells(cells: list[str]) -> dict[str, str]:
    padded = cells + [""] * 12
    return {
        "row_no": padded[0],
        "year": padded[1],
        "school_code": padded[2],
        "school_name": padded[3],
        "major_code": padded[4],
        "major_name": padded[5],
        "min_score": first_int(padded[6]),
        "avg_score": first_int(padded[7]),
        "min_rank": first_int(padded[8]),
        "volunteer_type": padded[9],
        "raw_cells": "|".join(cells),
    }


def first_int(value: str) -> str:
    match = re.search(r"\d+", value.replace(",", "").replace("，", ""))
    return match.group(0) if match else ""


def parse_total_pages(page_html: str) -> int | None:
    text = html.unescape(page_html)
    match = re.search(r"共\s*(\d+)\s*页", text)
    if match:
        return int(match.group(1))
    match = re.search(r'var\s+pages\s*=\s*["\'](\d+)["\']', page_html)
    if match:
        return int(match.group(1))
    return None


def write_outputs(rows: list[dict[str, str]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS hebei_lnwc_loggedin")
        conn.execute(
            """
            CREATE TABLE hebei_lnwc_loggedin (
                source_province TEXT,
                source_system TEXT,
                query_year TEXT,
                batch_code TEXT,
                batch_name TEXT,
                category_code TEXT,
                category_name TEXT,
                page INTEGER,
                row_no INTEGER,
                year INTEGER,
                school_code TEXT,
                school_name TEXT,
                major_code TEXT,
                major_name TEXT,
                min_score INTEGER,
                avg_score INTEGER,
                min_rank INTEGER,
                volunteer_type TEXT,
                raw_cells TEXT,
                source_url TEXT,
                source_file TEXT
            )
            """
        )
        conn.executemany(
            f"INSERT INTO hebei_lnwc_loggedin ({','.join(FIELDS)}) VALUES ({','.join('?' for _ in FIELDS)})",
            [[row.get(field, "") for field in FIELDS] for row in rows],
        )
        conn.execute("CREATE INDEX idx_hebei_lnwc_lookup ON hebei_lnwc_loggedin(batch_name, category_name, year, school_name, major_name)")


def main() -> None:
    args = parse_args()
    cookie = os.environ.get("HEBEEA_COOKIE", "").strip()
    if not cookie:
        raise SystemExit("Set HEBEEA_COOKIE from your logged-in browser request cookie.")

    batches, categories, form_id = discover_options(cookie)
    if args.form_id:
        form_id = args.form_id
    if args.batch_code:
        batches[args.batch_name] = args.batch_code
    if args.category_code:
        categories[args.category_name] = args.category_code
    if not form_id:
        print("Warning: did not discover form id; continuing with empty id.")

    target_batches = {name: code for name, code in batches.items() if name in args.batches}
    target_categories = {name: code for name, code in categories.items() if name in args.categories}
    print("Batches:", target_batches)
    print("Categories:", target_categories)

    all_rows: list[dict[str, str]] = []
    for batch_name, batch_code in target_batches.items():
        for category_name, category_code in target_categories.items():
            empty_streak = 0
            total_pages = args.max_pages
            for page in range(1, args.max_pages + 1):
                out = RAW_DIR / f"{batch_name}_{category_name}_page_{page:04d}.html"
                form = build_form(form_id, batch_code, category_code, category_name, page)
                page_html, from_cache = fetch_or_read(LNWC_URL, cookie, out, form, args.sleep)
                if "考生登录" in page_html and "请输入密码" in page_html:
                    raise RuntimeError("Login session expired; refresh HEBEEA_COOKIE and rerun.")
                if page == 1:
                    total_pages = min(args.max_pages, parse_total_pages(page_html) or args.max_pages)
                meta = {
                    "source_province": "河北",
                    "source_system": "gk.hebeea.edu.cn:88/xxcx/xxcxzx/lnwc",
                    "query_year": "2026",
                    "batch_code": batch_code,
                    "batch_name": batch_name,
                    "category_code": category_code,
                    "category_name": category_name,
                    "page": str(page),
                    "source_url": LNWC_URL,
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

    write_outputs(all_rows)
    print(f"Wrote {CSV_PATH.relative_to(ROOT)} rows={len(all_rows)}")
    print(f"Wrote {DB_PATH.relative_to(ROOT)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--form-id", default="", help="Optional id field copied from browser form data.")
    parser.add_argument("--batches", nargs="+", default=["本科批", "专科批"])
    parser.add_argument("--categories", nargs="+", default=["物理科目组合", "历史科目组合"])
    parser.add_argument("--batch-name", default="自定义批次")
    parser.add_argument("--batch-code", default="")
    parser.add_argument("--category-name", default="自定义科类")
    parser.add_argument("--category-code", default="")
    parser.add_argument("--max-pages", type=int, default=5000)
    parser.add_argument("--stop-after-empty", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=1.2)
    parser.add_argument("--progress-every", type=int, default=50)
    return parser.parse_args()


if __name__ == "__main__":
    main()
