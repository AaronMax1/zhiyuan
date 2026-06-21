#!/usr/bin/env python3
"""Build province batch-control-line database.

The current data model keeps score segments and admission records separate from
province-level control lines. This script imports 2025 control lines from public
summary pages and writes an independent SQLite database for the app.
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import sqlite3
import subprocess
import urllib.parse
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "output"
RAW_DIR = ROOT / "raw" / "batch_control_lines"
DB_PATH = OUT_DIR / "batch_control_lines.db"
CSV_PATH = OUT_DIR / "batch_control_lines_2025.csv"
REPORT_PATH = OUT_DIR / "batch_control_lines_report.md"

LIST_URL = "https://www.dxsbb.com/news/list_180.html"

PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏",
    "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
    "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]

PHYSICS_HISTORY = {
    "河北", "辽宁", "江苏", "福建", "湖北", "湖南", "广东", "重庆",
    "黑龙江", "吉林", "安徽", "江西", "广西", "贵州", "甘肃", "山西",
    "内蒙古", "云南", "四川", "河南", "陕西", "青海", "宁夏",
}
TRADITIONAL = {"新疆", "西藏"}
COMPREHENSIVE = {"北京", "天津", "上海", "浙江", "山东", "海南"}


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.current: list[str] | None = None
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.current = [dict(attrs).get("href") or "", ""]

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.current[1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current is not None:
            text = " ".join(self.current[1].split())
            if text:
                self.links.append((text, urllib.parse.urljoin(LIST_URL, self.current[0])))
            self.current = None


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
            value = " ".join(html.unescape(self.cell).split())
            self.row.append(value)
            self.in_cell = False
        elif self.in_table and tag == "tr":
            if any(self.row):
                self.rows.append(self.row)
        elif tag == "table" and self.in_table:
            if self.rows:
                self.tables.append(self.rows)
            self.in_table = False


def fetch(url: str, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 1000:
        return target.read_text(encoding="utf-8", errors="ignore")
    cmd = ["curl", "-L", "--max-time", "25", "-A", "Mozilla/5.0", url, "-o", str(target)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return target.read_text(encoding="utf-8", errors="ignore")


def discover_pages() -> dict[str, tuple[str, str]]:
    html_text = fetch(LIST_URL, RAW_DIR / "dxsbb_list_180.html")
    parser = LinkParser()
    parser.feed(html_text)
    candidates: dict[str, list[tuple[int, str, str]]] = {p: [] for p in PROVINCES}
    for text, url in parser.links:
        if not re.match(r"^2025年?.*高考.*分数线", text):
            continue
        for province in PROVINCES:
            if province not in text:
                continue
            score = 0
            if "一览表" in text:
                score += 100
            if "本科" in text:
                score += 20
            if "专科" in text:
                score += 20
            if "特殊类型" in text:
                score += 5
            if "多少分" in text:
                score += 60
            candidates[province].append((score, text, url))
    pages = {}
    for province, rows in candidates.items():
        if rows:
            _, text, url = sorted(rows, reverse=True)[0]
            pages[province] = (text, url)
    return pages


def clean_text(html_text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", "\n", html_text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def normalize_category(value: str) -> str:
    text = value.replace("首选", "").replace("科目组", "").strip()
    if "物理" in text or "物理组" in text or "理科" in text:
        return "物理类" if "物理" in text else "理科"
    if "历史" in text or "历史组" in text or "文科" in text:
        return "历史类" if "历史" in text else "文科"
    if "普通类" in text or "综合" in text:
        return "综合"
    return text


def valid_categories(province: str) -> set[str]:
    if province in PHYSICS_HISTORY:
        return {"物理类", "历史类"}
    if province in TRADITIONAL:
        return {"理科", "文科"}
    if province in COMPREHENSIVE:
        return {"综合"}
    return {"物理类", "历史类", "理科", "文科", "综合"}


def normalize_category_for_province(province: str, value: str) -> str:
    category = normalize_category(value)
    if province in PHYSICS_HISTORY:
        if category == "理科":
            return "物理类"
        if category == "文科":
            return "历史类"
    if province in TRADITIONAL:
        if category == "物理类":
            return "理科"
        if category == "历史类":
            return "文科"
    if province in COMPREHENSIVE and category in {"", "普通类", "综合"}:
        return "综合"
    return category


def normalize_line_type(value: str) -> str:
    text = value.replace(" ", "")
    if "特殊" in text:
        return "特殊类型线"
    if "一段" in text:
        return "一段线"
    if "二段" in text:
        return "二段线"
    if "一本" in text or "本科一批" in text or "本科第一批" in text or "重点本科" in text:
        return "一本线"
    if "二本" in text or "本科二批" in text or "本科第二批" in text:
        return "二本线"
    if "专科" in text or "高职" in text:
        return "专科线"
    if "本科" in text:
        return "本科线"
    return ""


def infer_other_category(province: str, seen: set[str]) -> str | None:
    if province in PHYSICS_HISTORY:
        if "历史类" in seen and "物理类" not in seen:
            return "物理类"
        if "物理类" in seen and "历史类" not in seen:
            return "历史类"
    if province in TRADITIONAL:
        if "文科" in seen and "理科" not in seen:
            return "理科"
        if "理科" in seen and "文科" not in seen:
            return "文科"
    if province in COMPREHENSIVE:
        return "综合"
    return None


LINE_PATTERNS = [
    ("特殊类型线", r"(?:特殊类型招生控制线|特殊类型资格线|特殊类型录取控制分数线|特殊类型[^\d]{0,10})(\d{2,3})\s*分"),
    ("本科线", r"(?:普通本科录取控制分数线|普通本科批|本科批|本科分数线|本科录取控制分数线|本科)\D{0,12}(\d{2,3})\s*分"),
    ("专科线", r"(?:普通专科录取控制分数线|高职（专科）分数线|高职专科|高职（专科）|专科分数线|专科)\D{0,12}(\d{2,3})\s*分"),
    ("一本线", r"(?:一本|重点本科|本科第一批)\D{0,12}(\d{2,3})\s*分"),
    ("二本线", r"(?:二本|本科第二批)\D{0,12}(\d{2,3})\s*分"),
    ("一段线", r"(?:普通类一段线|一段线|一段)\D{0,12}(\d{2,3})\s*分"),
    ("二段线", r"(?:普通类二段线|二段线|二段)\D{0,12}(\d{2,3})\s*分"),
]


def add_line(lines: dict[tuple[str, str], dict[str, Any]], province: str, category: str, line_type: str, score: int, source_url: str, source_title: str, method: str) -> None:
    category = normalize_category_for_province(province, category)
    if not category or score <= 0:
        return
    if category not in valid_categories(province):
        return
    if province in COMPREHENSIVE and line_type == "二本线":
        return
    # Keep ordinary academic lines only.
    if any(bad in category for bad in ("艺术", "体育", "舞蹈", "音乐", "美术", "三校生", "高职单招")):
        return
    key = (category, line_type)
    priority = {"meta": 4, "table": 3, "sentence": 2, "manual": 1}.get(method, 0)
    old = lines.get(key)
    if old and old.get("_priority", 0) >= priority:
        return
    lines[key] = {
        "province": province,
        "province_id": province_id(province),
        "year": 2025,
        "category": category,
        "line_type": line_type,
        "score": int(score),
        "source_type": "dxsbb_2025",
        "source_url": source_url,
        "source_title": source_title,
        "confidence": "medium" if method != "manual" else "low",
        "notes": method,
        "_priority": priority,
    }


def parse_tables(province: str, title: str, url: str, html_text: str, lines: dict[tuple[str, str], dict[str, Any]]) -> None:
    parser = TableParser()
    parser.feed(html_text)
    for table in parser.tables:
        parse_structured_table(province, title, url, table, lines)
        current_category = ""
        for row in table:
            joined = " ".join(row)
            if any(x in joined for x in ("艺术", "体育", "舞蹈", "音乐", "美术", "书法")):
                continue
            cat = normalize_category_for_province(province, joined)
            if cat in {"物理类", "历史类", "理科", "文科", "综合"}:
                current_category = cat
            if not current_category and province in COMPREHENSIVE:
                current_category = "综合"
            for line_type, pattern in LINE_PATTERNS:
                if not any(k in joined for k in ("本科", "专科", "特殊", "一本", "二本", "一段", "二段")):
                    continue
                for m in re.finditer(pattern, joined):
                    add_line(lines, province, current_category, line_type, int(m.group(1)), url, title, "table")


def parse_structured_table(province: str, title: str, url: str, table: list[list[str]], lines: dict[tuple[str, str], dict[str, Any]]) -> None:
    if not table:
        return
    header = [cell.strip() for cell in table[0]]
    if "年份" not in header or "分数线" not in header:
        return
    for row in table[1:]:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        data = dict(zip(header, row))
        if str(data.get("年份", "")).strip() != "2025":
            continue
        row_text = " ".join(row)
        if any(bad in row_text for bad in ("艺术", "体育", "舞蹈", "音乐", "美术", "书法", "三校生")):
            continue
        category = normalize_category_for_province(province, data.get("科类", "") or data.get("类别", "") or "")
        if not category and province in COMPREHENSIVE:
            category = "综合"
        if not category and province in PHYSICS_HISTORY:
            category = normalize_category_for_province(province, row_text)
        if not category and province in TRADITIONAL:
            category = normalize_category_for_province(province, row_text)
        batch = data.get("批次", "") or data.get("类型", "") or row_text
        line_type = normalize_line_type(batch)
        score_text = data.get("分数线", "") or data.get("分数", "")
        m = re.search(r"\d{2,3}", score_text)
        if category and line_type and m:
            add_line(lines, province, category, line_type, int(m.group(0)), url, title, "table")


def parse_sentences(province: str, title: str, url: str, text: str, lines: dict[tuple[str, str], dict[str, Any]]) -> None:
    compact = re.sub(r"\s+", "", text)
    # Focus on the article lead and the first ordinary-section paragraphs.
    head = compact[:3500]
    category_pattern = r"(首选科目物理|首选科目历史|首选物理|首选历史|物理科目组|历史科目组|物理类|历史类|物理组|历史组|普通类理科|普通类文科|理科|文科|普通类|综合)"
    matches = list(re.finditer(category_pattern, head))
    seen = {normalize_category_for_province(province, m.group(1)) for m in matches}
    segments: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        cat = normalize_category_for_province(province, match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(head), start + 240)
        segments.append((cat, head[start:end]))
    if matches:
        lead = head[: matches[0].start()]
        inferred = infer_other_category(province, seen)
        if inferred:
            segments.insert(0, (inferred, lead[-240:]))
    elif province in COMPREHENSIVE:
        segments.append(("综合", head[:500]))

    for category, segment in segments:
        if any(bad in segment[:20] for bad in ("艺术", "体育", "舞蹈", "音乐", "美术", "三校生")):
            continue
        seen_line_types: set[str] = set()
        for line_type, pattern in LINE_PATTERNS:
            for m in re.finditer(pattern, segment):
                if line_type in seen_line_types:
                    continue
                context = segment[max(0, m.start() - 30): min(len(segment), m.end() + 20)]
                if any(bad in context for bad in ("艺术", "体育", "舞蹈", "音乐", "美术", "书法", "三校生")):
                    continue
                add_line(lines, province, category, line_type, int(m.group(1)), url, title, "sentence")
                seen_line_types.add(line_type)
        if province in COMPREHENSIVE and "本科" in title and ("综合", "本科线") not in lines:
            m = re.search(r"(?:^|。|：)(\d{2,3})分[，,、](?:特殊类型|以下)", segment)
            if m:
                add_line(lines, province, category, "本科线", int(m.group(1)), url, title, "sentence")


def parse_meta_summary(province: str, title: str, url: str, html_text: str, lines: dict[tuple[str, str], dict[str, Any]]) -> None:
    m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html_text, flags=re.I)
    if not m:
        return
    summary = html.unescape(m.group(1))
    compact = re.sub(r"\s+", "", summary)
    compact = re.split(r"20(?:24|23|22|21|20)年", compact, maxsplit=1)[0]
    categories = ["物理", "历史"] if province in PHYSICS_HISTORY else (["理科", "文科"] if province in TRADITIONAL else ["综合"])
    if province in COMPREHENSIVE:
        segment = compact[:260]
        for line_type, pattern in LINE_PATTERNS:
            for hit in re.finditer(pattern, segment):
                add_line(lines, province, "综合", line_type, int(hit.group(1)), url, title, "meta")
                break
        return
    matches = []
    for cat in categories:
        for hit in re.finditer(cat, compact):
            matches.append((hit.start(), hit.end(), normalize_category_for_province(province, cat)))
    matches.sort()
    for index, (start, end, category) in enumerate(matches):
        next_start = matches[index + 1][0] if index + 1 < len(matches) else min(len(compact), end + 120)
        segment = compact[end:next_start]
        if any(bad in segment for bad in ("艺术", "体育", "舞蹈", "音乐", "美术", "三校生")):
            continue
        for line_type, pattern in LINE_PATTERNS:
            for hit in re.finditer(pattern, segment):
                add_line(lines, province, category, line_type, int(hit.group(1)), url, title, "meta")
                break


def parse_page(province: str, title: str, url: str) -> list[dict[str, Any]]:
    page_id = re.sub(r"\W+", "_", urllib.parse.urlparse(url).path.strip("/")) or province
    html_text = fetch(url, RAW_DIR / f"{province}_{page_id}.html")
    lines: dict[tuple[str, str], dict[str, Any]] = {}
    parse_meta_summary(province, title, url, html_text, lines)
    parse_tables(province, title, url, html_text, lines)
    parse_sentences(province, title, url, clean_text(html_text), lines)
    for category in valid_categories(province):
        if province in COMPREHENSIVE and (category, "一段线") in lines and (category, "本科线") in lines:
            del lines[(category, "本科线")]
    return [dict(row, _priority=None) for row in lines.values()]


def province_id(name: str) -> int:
    ids = {
        "北京": 11, "天津": 12, "河北": 13, "山西": 14, "内蒙古": 15,
        "辽宁": 21, "吉林": 22, "黑龙江": 23, "上海": 31, "江苏": 32,
        "浙江": 33, "安徽": 34, "福建": 35, "江西": 36, "山东": 37,
        "河南": 41, "湖北": 42, "湖南": 43, "广东": 44, "广西": 45,
        "海南": 46, "重庆": 50, "四川": 51, "贵州": 52, "云南": 53,
        "西藏": 54, "陕西": 61, "甘肃": 62, "青海": 63, "宁夏": 64,
        "新疆": 65,
    }
    return ids[name]


def write_outputs(rows: list[dict[str, Any]], pages: dict[str, tuple[str, str]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS batch_control_lines")
        conn.execute(
            """
            CREATE TABLE batch_control_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                province TEXT NOT NULL,
                province_id INTEGER NOT NULL,
                year INTEGER NOT NULL,
                category TEXT NOT NULL,
                line_type TEXT NOT NULL,
                score INTEGER NOT NULL,
                source_type TEXT,
                source_url TEXT,
                source_title TEXT,
                confidence TEXT,
                notes TEXT,
                UNIQUE(province_id, year, category, line_type)
            )
            """
        )
        conn.executemany(
            """
            INSERT OR REPLACE INTO batch_control_lines
            (province, province_id, year, category, line_type, score, source_type, source_url, source_title, confidence, notes)
            VALUES (:province, :province_id, :year, :category, :line_type, :score, :source_type, :source_url, :source_title, :confidence, :notes)
            """,
            [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows],
        )
        conn.execute("CREATE INDEX idx_batch_lines_lookup ON batch_control_lines(province_id, year, category, line_type)")

    fieldnames = ["province", "province_id", "year", "category", "line_type", "score", "source_type", "source_url", "source_title", "confidence", "notes"]
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["province_id"], r["category"], r["line_type"])):
            writer.writerow({key: row.get(key, "") for key in fieldnames})

    by_prov: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_prov.setdefault(row["province"], []).append(row)
    lines = ["# 2025 省控线导入报告", "", f"- 省份页面：{len(pages)}", f"- 入库记录：{len(rows)}", ""]
    lines.append("| 省份 | 类别 | 已有线 | 缺口 | 来源 |")
    lines.append("|---|---|---|---|---|")
    for province in PROVINCES:
        grouped: dict[str, set[str]] = {}
        for row in by_prov.get(province, []):
            grouped.setdefault(row["category"], set()).add(row["line_type"])
        if not grouped:
            title, url = pages.get(province, ("未发现页面", ""))
            lines.append(f"| {province} | - | - | 本科线、专科线 | [{title}]({url}) |")
            continue
        for category, types in sorted(grouped.items()):
            has_undergrad = bool(types & {"本科线", "一本线", "二本线", "一段线"})
            has_specialist = bool(types & {"专科线", "二段线"})
            missing = []
            if not has_undergrad:
                missing.append("本科线")
            if not has_specialist:
                missing.append("专科线")
            title, url = pages.get(province, ("", ""))
            lines.append(f"| {province} | {category} | {'、'.join(sorted(types))} | {'、'.join(missing) or '无'} | [{title}]({url}) |")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    pages = discover_pages()
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        page = pages.get(province)
        if not page:
            continue
        title, url = page
        rows.extend(parse_page(province, title, url))
    write_outputs(rows, pages)
    print(f"wrote {DB_PATH}: {len(rows)} rows")
    print(f"wrote {CSV_PATH}")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
