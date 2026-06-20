#!/usr/bin/env python3
"""Import downloaded official source files into a structured SQLite database.

This first pass intentionally handles only formats that are parseable with the
Python standard library:

- OOXML .xlsx files (zip + XML)
- static .html/.htm tables

Legacy .xls, PDF, images, and zip archives are recorded into parse_queue for
later conversion/OCR.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sqlite3
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "data-pipeline" / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

DEFAULT_REGISTRY = ROOT / "data-pipeline" / "source_registry.json"
DEFAULT_INVENTORY = ROOT / "data-pipeline" / "raw" / "official" / "local_inventory.json"
DEFAULT_EXTRACTED_MANIFEST = ROOT / "data-pipeline" / "raw" / "official_extracted" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "output" / "official_admission.db"

SUPPORTED_TABLE_SUFFIXES = {".pdf", ".xls", ".xlsx", ".html", ".htm"}
QUEUED_SUFFIXES = {".jpg", ".jpeg", ".png", ".zip"}

SCORE_HEADERS = ("投档最低分", "最低分", "投档线", "投档分", "投档分数", "最低投档分", "分数线", "总分")
RANK_HEADERS = ("最低位次", "位次", "投档最低排位", "最低排位")
SCHOOL_HEADERS = ("院校名称", "学校名称", "院校专业组名称", "院校名称及专业组", "招生院校", "院校、专业组", "院校代号及名称")
MAJOR_HEADERS = ("专业名称", "专业组名称", "院校专业组名称", "招生专业", "专业组")
MAJOR_COMBINED_HEADERS = ("专业代号及名称",)
MAJOR_CODE_HEADERS = ("专业代号", "专业代码", "院校专业组", "专业组代码", "专业组编号", "专业组代号")
CODE_HEADERS = ("院校代码", "院校代号", "院校专业组代码", "院校编号", "学校代号")
CATEGORY_HEADERS = ("科类", "首选科目", "科目类")


@dataclass
class SourceMeta:
    source_id: str
    province: str
    year: int | None
    category: str
    batch: str
    publisher: str
    page_url: str
    source_type: str


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--extracted-manifest", type=Path, default=DEFAULT_EXTRACTED_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    registry = {s["id"]: s for s in json.loads(args.registry.read_text(encoding="utf-8"))["sources"]}
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    if args.extracted_manifest.exists():
        inventory.extend(extracted_manifest_to_inventory(args.extracted_manifest))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    conn = sqlite3.connect(args.output)
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    report: dict[str, Any] = {"files": [], "totals": {}}
    for item in inventory:
        source_id = item["source_id"]
        source = registry.get(source_id, {})
        meta = SourceMeta(
            source_id=source_id,
            province=item["province"],
            year=to_int(item["year"]),
            category=str(source.get("category") or ""),
            batch=str(source.get("batch") or ""),
            publisher=str(source.get("publisher") or ""),
            page_url=str(source.get("page_url") or ""),
            source_type=str(source.get("source_type") or ""),
        )
        path = ROOT / item["path"]
        suffix = item["suffix"].lower()
        file_report = {
            "path": item["path"],
            "source_id": source_id,
            "province": meta.province,
            "year": meta.year,
            "suffix": suffix,
            "status": "",
            "rows": 0,
            "error": "",
        }
        try:
            if suffix == ".xlsx" and is_ooxml_xlsx(path):
                rows = parse_xlsx(path)
                count = import_tables(conn, meta, item["path"], rows)
                file_report.update({"status": "parsed", "rows": count})
            elif suffix == ".xls":
                rows = parse_xls(path)
                count = import_tables(conn, meta, item["path"], rows)
                file_report.update({"status": "parsed" if count else "no_records", "rows": count})
                if not count:
                    queue_file(conn, meta, item["path"], suffix, "xls_no_recognized_table")
            elif suffix == ".pdf":
                rows = parse_pdf_tables(path, meta)
                count = import_tables(conn, meta, item["path"], rows)
                file_report.update({"status": "parsed" if count else "queued", "rows": count})
                if not count:
                    queue_file(conn, meta, item["path"], suffix, "pdf_no_recognized_table")
            elif suffix in {".html", ".htm"}:
                rows = parse_html_tables(path)
                count = import_tables(conn, meta, item["path"], rows)
                file_report.update({"status": "parsed" if count else "no_records", "rows": count})
                if not count:
                    queue_file(conn, meta, item["path"], suffix, "html_no_recognized_table")
            elif suffix in QUEUED_SUFFIXES:
                queue_file(conn, meta, item["path"], suffix, "unsupported_format")
                file_report["status"] = "queued"
            else:
                queue_file(conn, meta, item["path"], suffix, "unknown_format")
                file_report["status"] = "queued"
        except Exception as exc:
            queue_file(conn, meta, item["path"], suffix, f"parse_error:{type(exc).__name__}")
            file_report.update({"status": "error", "error": str(exc)})
        report["files"].append(file_report)

    create_indexes(conn)
    conn.commit()
    report["totals"] = build_totals(conn)
    write_report(args.output.parent, report)
    conn.close()

    print(f"Built: {args.output}")
    print(f"Records: {report['totals']['records']}")
    print(f"Queued files: {report['totals']['queued_files']}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE official_admission_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            province TEXT NOT NULL,
            year INTEGER,
            category TEXT,
            batch TEXT,
            school_code TEXT,
            school_name TEXT NOT NULL,
            major_code TEXT,
            major_name TEXT,
            score INTEGER,
            rank INTEGER,
            raw_row TEXT NOT NULL,
            quality_flags TEXT NOT NULL
        );

        CREATE TABLE parse_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source_file TEXT NOT NULL,
            province TEXT,
            year INTEGER,
            suffix TEXT,
            reason TEXT NOT NULL
        );
        """
    )


def extracted_manifest_to_inventory(path: Path) -> list[dict[str, Any]]:
    rows = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        rel = Path(item["path"])
        parts = rel.parts
        # data-pipeline/raw/official_extracted/{province}/{year}/{source_id}/{archive}/{file}
        try:
            idx = parts.index("official_extracted")
            province, year, source_id = parts[idx + 1], parts[idx + 2], parts[idx + 3]
        except Exception:
            continue
        rows.append({
            "province": province,
            "year": year,
            "source_id": source_id,
            "path": item["path"],
            "bytes": item.get("bytes", 0),
            "sha256": "",
            "suffix": rel.suffix.lower(),
        })
    return rows


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX idx_official_lookup_score
            ON official_admission_records(province, year, score);
        CREATE INDEX idx_official_lookup_rank
            ON official_admission_records(province, year, rank);
        CREATE INDEX idx_official_school
            ON official_admission_records(school_name);
        CREATE INDEX idx_official_source
            ON official_admission_records(source_id, source_file);
        """
    )


def is_ooxml_xlsx(path: Path) -> bool:
    return path.read_bytes()[:2] == b"PK"


def parse_xlsx(path: Path) -> list[list[list[str]]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    tables: list[list[list[str]]] = []
    with zipfile.ZipFile(path) as z:
        shared = read_shared_strings(z, ns)
        sheet_names = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        for sheet_name in sorted(sheet_names):
            root = ET.fromstring(z.read(sheet_name))
            rows: list[list[str]] = []
            for row in root.findall(".//a:sheetData/a:row", ns):
                cells = []
                for c in row.findall("a:c", ns):
                    v = c.find("a:v", ns)
                    value = "" if v is None else (v.text or "")
                    if c.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                        value = shared[int(value)]
                    cells.append(clean_cell(value))
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
    return tables


def read_shared_strings(z: zipfile.ZipFile, ns: dict[str, str]) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out = []
    for si in root.findall("a:si", ns):
        texts = [t.text or "" for t in si.findall(".//a:t", ns)]
        out.append("".join(texts))
    return out


def parse_xls(path: Path) -> list[list[list[str]]]:
    try:
        import xlrd  # type: ignore
    except ImportError as exc:
        raise RuntimeError("xlrd is required for legacy .xls files; install it into data-pipeline/.vendor") from exc

    book = xlrd.open_workbook(str(path), formatting_info=False)
    tables: list[list[list[str]]] = []
    for sheet in book.sheets():
        rows: list[list[str]] = []
        for r in range(sheet.nrows):
            row = [clean_cell(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
            if any(row):
                rows.append(row)
        if rows:
            tables.append(rows)
    return tables


def parse_pdf_tables(path: Path, meta: SourceMeta) -> list[list[list[str]]]:
    if meta.province not in {"上海", "云南", "北京", "广东", "江苏", "重庆", "宁夏", "贵州"}:
        return []

    text = extract_pdf_text(path)
    lines = [clean_cell(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if meta.province == "广东":
        return parse_guangdong_pdf(lines, text)
    if meta.province == "江苏":
        return parse_jiangsu_pdf(lines, text)
    if meta.province == "上海":
        return parse_shanghai_pdf(lines, text)
    if meta.province == "重庆":
        return parse_chongqing_pdf(lines, text)
    if meta.province == "宁夏":
        return parse_ningxia_pdf(lines, text)
    if meta.province == "云南":
        return parse_yunnan_pdf(lines, text)
    if meta.province == "北京":
        return parse_beijing_pdf(lines, text, path)
    if meta.province == "贵州":
        return parse_guizhou_pdf(lines, text)
    return []


def extract_pdf_text(path: Path) -> str:
    cache_dir = ROOT / "data-pipeline" / "raw" / "pdf_text"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_path = cache_dir / f"{digest}.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pypdf is required for PDF text extraction; install it into data-pipeline/.vendor") from exc

    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    cache_path.write_text(text, encoding="utf-8")
    return text


def parse_guangdong_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    if "普通类" not in text:
        return []
    category = infer_category_from_text(text)
    table = [["院校代码", "院校名称", "专业组代码", "投档最低分", "投档最低排位", "科类"]]
    for line in lines:
        m = re.match(r"^(\d{5})\s+(.+?)\s+([A-Z0-9]{3})\s+\d+\s+\d+\s+(\d{3})\s+(\d+)$", line)
        if not m:
            continue
        table.append([m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), category])
    return [table] if len(table) > 1 else []


def parse_jiangsu_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    category = infer_category_from_text(text)
    table = [["院校代号", "院校、专业组（再选科目要求）", "投档最低分", "科类"]]
    for line in lines:
        m = re.match(r"^(\d{4})\s+(.+?专业组(?:\([^)）]+[)）])?(?:\([^)）]+[)）])?)\s+(\d{3})\s+", line)
        if not m:
            continue
        table.append([m.group(1), m.group(2), m.group(3), category])
    return [table] if len(table) > 1 else []


def parse_shanghai_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    category = infer_category_from_text(text)
    table = [["院校专业组代码", "院校专业组名称", "投档线", "科类"]]
    for line in lines:
        m = re.match(r"^([0-9A-Z]{5})\s+(.+?)\s+((?:\d{3})|(?:\d{3}分及以上))(?:\s|$)", line)
        if not m:
            continue
        table.append([m.group(1), m.group(2), m.group(3), category])
    return [table] if len(table) > 1 else []


def parse_chongqing_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    category = infer_category_from_text(text)
    table = [["院校代号", "院校名称", "专业代号", "专业名称", "投档最低分", "科类"]]
    buffer = ""
    for line in lines:
        if is_pdf_noise_line(line):
            continue
        if re.match(r"^[0-9A-Z]{4}\s+", line):
            add_chongqing_record(table, buffer, category)
            buffer = line
        elif buffer:
            buffer = f"{buffer} {line}"
            if add_chongqing_record(table, buffer, category):
                buffer = ""
    add_chongqing_record(table, buffer, category)
    return [table] if len(table) > 1 else []


def add_chongqing_record(table: list[list[str]], line: str, category: str) -> bool:
    line = clean_cell(line)
    if not line:
        return False
    m = re.match(r"^([0-9A-Z]{4})\s+(.+?)\s+([0-9A-Z]{3})\s+(.+?)\s+(\d{3})\s+[\d\s]+$", line)
    if not m:
        return False
    school = m.group(2)
    major = cleanup_pdf_tail(m.group(4))
    if not looks_like_school(normalize_school_name(school)):
        return False
    table.append([m.group(1), school, m.group(3), major, m.group(5), category])
    return True


def parse_ningxia_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    table = [["院校代号", "院校名称", "专业组名称", "投档最低分", "科类"]]
    category = infer_category_from_text(text)
    buffer = ""
    for line in lines:
        if "普通类（历史）" in line or "普通类(历史)" in line:
            category = "历史类"
            continue
        if "普通类（物理）" in line or "普通类(物理)" in line:
            category = "物理类"
            continue
        if is_pdf_noise_line(line):
            continue
        if re.match(r"^[0-9A-Z]{4}\s+", line):
            add_ningxia_record(table, buffer, category)
            buffer = line
        elif buffer:
            buffer = f"{buffer} {line}"
            if add_ningxia_record(table, buffer, category):
                buffer = ""
    add_ningxia_record(table, buffer, category)
    return [table] if len(table) > 1 else []


def parse_guizhou_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    category = infer_category_from_text(text)
    table = [["院校代码", "院校名称", "专业代码", "专业名称", "科类", "投档最低分", "最低位次"]]
    for line in lines:
        if is_pdf_noise_line(line) or "投档" in line or "序号" in line:
            continue
        m = re.match(
            r"^\d+\s+([0-9A-Z]{4})\s+(.+?)\s+([0-9A-Z]{3})\s+(.+?)\s+(.+?)\s+\d+\s+(?:\d+\s+)?(\d{2,3})\s+(\d+)$",
            line,
        )
        if not m:
            continue
        school = m.group(2)
        if not looks_like_school(normalize_school_name(school)):
            continue
        table.append([m.group(1), school, m.group(3), cleanup_pdf_tail(m.group(4)), category, m.group(6), m.group(7)])
    return [table] if len(table) > 1 else []


def parse_yunnan_pdf(lines: list[str], text: str) -> list[list[list[str]]]:
    table = [["院校代码", "院校名称", "专业名称", "投档最低分", "科类"]]
    records = parse_yunnan_tokens(lines)
    for rec in records:
        table.append([rec["school_code"], rec["school_name"], rec["major_name"], rec["score"], ""])
    return [table] if len(table) > 1 else []


def parse_yunnan_tokens(lines: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    tokens = [clean_cell(x) for x in lines if clean_cell(x)]
    i = 0
    while i < len(tokens):
        if not re.fullmatch(r"[0-9A-Z]{4}", tokens[i]):
            i += 1
            continue
        start = i
        major_parts: list[str] = []
        i += 1
        while i < len(tokens) and not tokens[i].startswith("("):
            i += 1
        if i >= len(tokens):
            i = start + 1
            continue
        while i < len(tokens):
            major_parts.append(tokens[i])
            i += 1
            if i < len(tokens) and re.fullmatch(r"[0-9A-Z]{4}", tokens[i]):
                break
        if i >= len(tokens) or not re.fullmatch(r"[0-9A-Z]{4}", tokens[i]):
            i = start + 1
            continue
        school_code = tokens[i]
        i += 1
        school_parts: list[str] = []
        while i < len(tokens) and not re.fullmatch(r"\d{1,3}", tokens[i]):
            school_parts.append(tokens[i])
            i += 1
        if i >= len(tokens):
            i = start + 1
            continue
        quota = tokens[i]
        i += 1
        if i >= len(tokens) or not re.fullmatch(r"\d{3}", tokens[i]):
            i = start + 1
            continue
        score = tokens[i]
        school = normalize_school_name("".join(school_parts))
        major = "".join(major_parts)
        if looks_like_school(school) and major:
            out.append({"school_code": school_code, "school_name": school, "major_name": major, "score": score, "quota": quota})
        i += 1
    return out


def parse_beijing_pdf(lines: list[str], text: str, path: Path) -> list[list[list[str]]]:
    if "专科" in path.as_posix() or "高职" in text:
        return parse_beijing_specialist_pdf(lines)
    return parse_beijing_undergraduate_pdf(lines)


def parse_beijing_undergraduate_pdf(lines: list[str]) -> list[list[list[str]]]:
    table = [["序号", "院校代码", "院校名称", "专业组代码", "专业组名称", "总分"]]
    buffer = ""
    for line in lines:
        if is_pdf_noise_line(line):
            continue
        if re.match(r"^\d+\s+[0-9A-Z]{4}\s+", line):
            add_beijing_undergrad_record(table, buffer)
            buffer = line
        elif buffer:
            buffer = f"{buffer} {line}"
            if add_beijing_undergrad_record(table, buffer):
                buffer = ""
    add_beijing_undergrad_record(table, buffer)
    return [table] if len(table) > 1 else []


def add_beijing_undergrad_record(table: list[list[str]], line: str) -> bool:
    line = clean_cell(line)
    m = re.match(r"^(\d+)\s+([0-9A-Z]{4})\s+(.+?)\s+([0-9A-Z]{2})\s+(.+?)\s+(\d{3})(?:\s+.*)?$", line)
    if not m:
        return False
    school = m.group(3)
    if not looks_like_school(normalize_school_name(school)):
        return False
    table.append([m.group(1), m.group(2), school, m.group(4), m.group(5), m.group(6)])
    return True


def parse_beijing_specialist_pdf(lines: list[str]) -> list[list[list[str]]]:
    table = [["序号", "院校代码", "院校名称", "专业代码", "专业名称", "总分"]]
    buffer = ""
    for line in lines:
        if is_pdf_noise_line(line):
            continue
        if re.match(r"^\d+\s+[0-9A-Z]{4}\s+", line):
            add_beijing_specialist_record(table, buffer)
            buffer = line
        elif buffer:
            buffer = f"{buffer} {line}"
            if add_beijing_specialist_record(table, buffer):
                buffer = ""
    add_beijing_specialist_record(table, buffer)
    return [table] if len(table) > 1 else []


def add_beijing_specialist_record(table: list[list[str]], line: str) -> bool:
    line = clean_cell(line)
    m = re.match(r"^(\d+)\s+([0-9A-Z]{4})\s+(.+?)\s+([0-9A-Z]{2})\s+(.+?)\s+(\d{3})(?:\s+.*)?$", line)
    if not m:
        return False
    school = m.group(3)
    if not looks_like_school(normalize_school_name(school)):
        return False
    table.append([m.group(1), m.group(2), school, m.group(4), cleanup_pdf_tail(m.group(5)), m.group(6)])
    return True


def add_ningxia_record(table: list[list[str]], line: str, category: str) -> bool:
    line = clean_cell(line)
    if not line:
        return False
    m = re.match(r"^([0-9A-Z]{4})\s+(.+?)\s+(\d{3}专业组[^\s]*)\s+(\d{3})\s+[\d\s]+$", line)
    if not m:
        return False
    school = m.group(2)
    if not looks_like_school(normalize_school_name(school)):
        return False
    table.append([m.group(1), school, m.group(3), m.group(4), category])
    return True


def is_pdf_noise_line(line: str) -> bool:
    return bool(
        not line
        or line in {"语数", "之和", "最高", "外语", "首选", "科目", "最低分", "重庆", "院"}
        or "教育考试" in line
        or "招生信息表" in line
        or "投档最低分同分排序项" in line
        or re.fullmatch(r"\d+/\d+", line)
        or line.startswith("院校代号")
        or line.startswith("院校 代号")
        or line.startswith("代号 ")
    )


def cleanup_pdf_tail(value: str) -> str:
    return re.sub(r"\s+", "", value).strip()


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = 0
        self._in_row = False
        self._in_cell = False
        self._table: list[list[str]] = []
        self._row: list[str] = []
        self._cell: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._table = []
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._row = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell:
            self._row.append(clean_cell("".join(self._cell)))
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            if any(self._row):
                self._table.append(self._row)
            self._in_row = False
        elif tag == "table" and self._in_table:
            self._in_table -= 1
            if self._in_table == 0 and self._table:
                self.tables.append(self._table)

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def parse_html_tables(path: Path) -> list[list[list[str]]]:
    raw = path.read_bytes()
    text = decode_html(raw)
    parser = TableParser()
    parser.feed(text)
    return parser.tables


def decode_html(raw: bytes) -> str:
    head = raw[:1000].decode("ascii", errors="ignore").lower()
    if "charset=gbk" in head or "charset=gb2312" in head:
        return raw.decode("gbk", errors="ignore")
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="ignore")


def import_tables(conn: sqlite3.Connection, meta: SourceMeta, source_file: str, tables: list[list[list[str]]]) -> int:
    count = 0
    tables = normalize_tables(meta, source_file, tables)
    for rows in tables:
        header_index, columns = detect_columns(rows)
        if header_index is None:
            continue
        for raw in rows[header_index + 1:]:
            rec = row_to_record(meta, source_file, raw, columns)
            if rec is None:
                continue
            conn.execute(
                """
                INSERT INTO official_admission_records(
                    source_id, source_file, province, year, category, batch,
                    school_code, school_name, major_code, major_name, score, rank,
                    raw_row, quality_flags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rec,
            )
            count += 1
    return count


def normalize_tables(meta: SourceMeta, source_file: str, tables: list[list[list[str]]]) -> list[list[list[str]]]:
    if meta.province == "北京":
        return normalize_beijing_tables(source_file, tables)
    return tables


def normalize_beijing_tables(source_file: str, tables: list[list[list[str]]]) -> list[list[list[str]]]:
    normalized: list[list[list[str]]] = []
    for rows in tables:
        if "specialist" in source_file:
            out = [["序号", "院校代码", "院校名称", "专业代码", "专业名称", "总分"]]
            for row in rows:
                if len(row) >= 6 and row[0].isdigit() and re.fullmatch(r"[0-9A-Z]{4}", row[1]):
                    out.append([row[0], row[1], row[2], row[3], row[4], row[5]])
            if len(out) > 1:
                normalized.append(out)
        elif "undergraduate" in source_file:
            out = [["序号", "院校代码", "院校名称", "专业组代码", "专业组名称", "总分"]]
            for row in rows:
                if len(row) >= 6 and row[0].isdigit() and re.fullmatch(r"[0-9A-Z]{4}", row[1]):
                    out.append([row[0], row[1], row[2], row[3], row[4], row[5]])
            if len(out) > 1:
                normalized.append(out)
    return normalized or tables


def detect_columns(rows: list[list[str]]) -> tuple[int | None, dict[str, int]]:
    best: tuple[int | None, dict[str, int], int] = (None, {}, 0)
    for i, row in enumerate(rows[:20]):
        normalized = [normalize_header(c) for c in row]
        cols: dict[str, int] = {}
        for idx, cell in enumerate(normalized):
            if "school" not in cols and any(h in cell for h in SCHOOL_HEADERS):
                cols["school"] = idx
            if "score" not in cols and any(h in cell for h in SCORE_HEADERS):
                cols["score"] = idx
            if "rank" not in cols and any(h in cell for h in RANK_HEADERS):
                cols["rank"] = idx
            if "major" not in cols and any(h in cell for h in MAJOR_HEADERS):
                if cell != "院校专业组" and "代码" not in cell and "代号" not in cell and "编号" not in cell:
                    cols["major"] = idx
            if "major_combined" not in cols and any(h in cell for h in MAJOR_COMBINED_HEADERS):
                cols["major_combined"] = idx
            if "major_code" not in cols and any(h in cell for h in MAJOR_CODE_HEADERS):
                if "名称" not in cell:
                    cols["major_code"] = idx
            if "code" not in cols and any(h in cell for h in CODE_HEADERS):
                if "及名称" not in cell:
                    cols["code"] = idx
            if "category" not in cols and any(h in cell for h in CATEGORY_HEADERS):
                cols["category"] = idx
        score = sum(k in cols for k in ("school",)) * 2 + sum(k in cols for k in ("score", "rank")) * 2 + len(cols)
        if score > best[2]:
            best = (i, cols, score)
    if best[0] is None or "school" not in best[1] or not ({"score", "rank"} & set(best[1])):
        return None, {}
    return best[0], best[1]


def row_to_record(meta: SourceMeta, source_file: str, row: list[str], cols: dict[str, int]) -> tuple[Any, ...] | None:
    school = get(row, cols.get("school"))
    score = to_int(get(row, cols.get("score")))
    rank = to_int(get(row, cols.get("rank")))
    if not school or (score is None and rank is None):
        return None
    school_code_from_name, school_name_from_combined = split_code_name(school)
    if school_name_from_combined:
        school = school_name_from_combined
    school_name = normalize_school_name(school)
    if not looks_like_school(school_name):
        return None
    raw_major = get(row, cols.get("major")) or get(row, cols.get("major_combined"))
    major_code = get(row, cols.get("major_code"))
    major_code_from_name, major_name_from_combined = split_code_name(raw_major)
    if major_name_from_combined:
        raw_major = major_name_from_combined
    if not major_code and major_code_from_name:
        major_code = major_code_from_name
    major_name = raw_major
    school_code = get(row, cols.get("code")) or school_code_from_name
    category = get(row, cols.get("category")) or infer_category_from_text(source_file) or meta.category
    flags = []
    if score is None:
        flags.append("missing_score")
    if rank is None:
        flags.append("missing_rank")
    if raw_major in {"", school_name}:
        flags.append("missing_major")
    if source_file.lower().endswith(".pdf") and maybe_truncated_pdf_text(school_name, major_name):
        flags.append("pdf_text_may_be_truncated")
    group_school = split_school_group(school)
    if group_school:
        school_name = normalize_school_name(group_school)
        major_name = raw_major or school
        flags.append("school_group_row")
    elif "院校专业组" in source_file or re.search(r"\(\d+\)", school):
        major_name = school
        school_name = normalize_school_name(re.sub(r"[（(]\d+[）)]", "", school))
        flags.append("school_group_row")
    return (
        meta.source_id,
        source_file,
        meta.province,
        meta.year,
        category,
        meta.batch,
        school_code,
        school_name,
        major_code,
        major_name,
        score,
        rank,
        json.dumps(row, ensure_ascii=False),
        json.dumps(flags, ensure_ascii=False),
    )


def queue_file(conn: sqlite3.Connection, meta: SourceMeta, source_file: str, suffix: str, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO parse_queue(source_id, source_file, province, year, suffix, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (meta.source_id, source_file, meta.province, meta.year, suffix, reason),
    )


def build_totals(conn: sqlite3.Connection) -> dict[str, Any]:
    records = conn.execute("SELECT COUNT(*) FROM official_admission_records").fetchone()[0]
    queued = conn.execute("SELECT COUNT(*) FROM parse_queue").fetchone()[0]
    by_province = [dict(r) for r in conn.execute(
        """
        SELECT province, COUNT(*) records, SUM(CASE WHEN rank IS NOT NULL THEN 1 ELSE 0 END) rank_records
        FROM official_admission_records
        GROUP BY province
        ORDER BY province
        """
    )]
    queued_by_suffix = [dict(r) for r in conn.execute(
        "SELECT suffix, COUNT(*) files FROM parse_queue GROUP BY suffix ORDER BY suffix"
    )]
    return {"records": records, "queued_files": queued, "by_province": by_province, "queued_by_suffix": queued_by_suffix}


def write_report(out_dir: Path, report: dict[str, Any]) -> None:
    json_path = out_dir / "official_import_report.json"
    md_path = out_dir / "official_import_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 官方数据导入报告", ""]
    totals = report["totals"]
    lines.append(f"- 结构化记录：{totals['records']}")
    lines.append(f"- 待转换/OCR 文件：{totals['queued_files']}")
    lines.extend(["", "## 省份记录", "", "| 省份 | 记录 | 含位次记录 |", "|---|---:|---:|"])
    for row in totals["by_province"]:
        lines.append(f"| {row['province']} | {row['records']} | {row['rank_records']} |")
    lines.extend(["", "## 待处理文件类型", "", "| 类型 | 文件数 |", "|---|---:|"])
    for row in totals["queued_by_suffix"]:
        lines.append(f"| `{row['suffix']}` | {row['files']} |")
    lines.extend(["", "## 文件解析状态", "", "| 状态 | 文件 | 行数 | 错误 |", "|---|---|---:|---|"])
    for item in report["files"]:
        lines.append(f"| {item['status']} | `{item['path']}` | {item['rows']} | {item.get('error','')} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_cell(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text.strip()


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", "", value)


def normalize_school_name(value: str) -> str:
    value = clean_cell(value)
    value = re.sub(r"\[[^\]]+\]$", "", value)
    value = re.sub(r"（[^）]*）$", "", value)
    return value.strip()


def split_code_name(value: str) -> tuple[str, str]:
    value = clean_cell(value)
    if not value:
        return "", ""
    m = re.match(r"^([A-Z0-9]{1,8})\s*(.+)$", value, flags=re.I)
    if not m:
        return "", ""
    code, name = m.group(1), m.group(2).strip()
    if not name or re.fullmatch(r"[\d.]+", name):
        return "", ""
    return code, name


def split_school_group(value: str) -> str:
    value = clean_cell(value)
    m = re.match(r"^(.+?)(?:\d{2,3}|[A-Z]\d{1,3})专业组", value)
    if not m:
        return ""
    return m.group(1).strip()


def looks_like_school(value: str) -> bool:
    return bool(value and len(value) <= 60 and re.search(r"(大学|学院|学校|职业|高等|专科|校区|分校)", value))


def maybe_truncated_pdf_text(school_name: str, major_name: str) -> bool:
    if major_name.count("(") > major_name.count(")") or major_name.count("（") > major_name.count("）"):
        return True
    return bool(re.search(r"(职业|职业学|技术|高等专科)$", school_name))


def infer_category_from_text(text: str) -> str:
    if "物理" in text:
        return "物理类"
    if "历史" in text:
        return "历史类"
    if "理工" in text or "-LG" in text or "LG." in text:
        return "理工"
    if "文史" in text or "-WS" in text or "WS." in text:
        return "文史"
    return ""


def get(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return clean_cell(row[idx])


def to_int(value: Any) -> int | None:
    value = clean_cell(value)
    if not value or value in {"-", "—", "无"}:
        return None
    m = re.search(r"\d+", value.replace(",", ""))
    if not m:
        return None
    return int(m.group(0))


if __name__ == "__main__":
    main()
