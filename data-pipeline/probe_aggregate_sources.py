#!/usr/bin/env python3
"""Probe public aggregate gaokao data sources for province coverage.

This does not import data into the official database. It records whether public
aggregate endpoints can provide fallback score data for provinces whose exam
authority does not publish batch files.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sqlite3
import shutil
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "output" / "aggregate_source_research.json"
DEFAULT_MD = ROOT / "data-pipeline" / "output" / "aggregate_source_research.md"
DEFAULT_CLEAN_DB = ROOT / "data-pipeline" / "output" / "gaokao_clean.db"

CDN_BASE = "https://static-data.gaokao.cn/www/2.0"
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
SAMPLE_SCHOOLS = [31, 35, 59, 114, 125, 140, 148, 281, 459, 1059]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    parser.add_argument("--clean-db", type=Path, default=DEFAULT_CLEAN_DB)
    parser.add_argument("--school-id", type=int, action="append", default=[])
    parser.add_argument("--timeout", type=int, default=15)
    args = parser.parse_args()

    schools = args.school_id or SAMPLE_SCHOOLS
    report = {
        "static_data_gaokao_cn": probe_static_cdn(schools, args.timeout),
        "local_open_source_clean_db": probe_clean_db(args.clean_db),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.markdown.write_text(render_markdown(report, schools), encoding="utf-8")
    print(f"JSON: {args.output}")
    print(f"Markdown: {args.markdown}")


def probe_static_cdn(school_ids: list[int], timeout: int) -> list[dict[str, Any]]:
    rows = []
    for province_id, province in PROVINCES.items():
        province_hits = []
        for school_id in school_ids:
            url = f"{CDN_BASE}/school/{school_id}/provincescore/{province_id}.json"
            data = fetch_json(url, timeout)
            years = sorted((data.get("data") or {}).keys(), reverse=True) if data else []
            if years:
                province_hits.append({
                    "school_id": school_id,
                    "url": url,
                    "years": years[:5],
                    "sample": summarize_province_score(data),
                })
        rows.append({
            "province": province,
            "province_id": province_id,
            "sample_school_hits": len(province_hits),
            "hits": province_hits[:3],
            "status": "reachable" if province_hits else "not_seen_in_sample",
        })
    return rows


def summarize_province_score(data: dict[str, Any]) -> dict[str, Any]:
    by_year = data.get("data") or {}
    for year in sorted(by_year.keys(), reverse=True):
        type_map = by_year.get(year) or {}
        for type_code, items in type_map.items():
            if items:
                item = items[0]
                return {
                    "year": year,
                    "type_code": type_code,
                    "type_name": item.get("type_name"),
                    "batch_name": item.get("batch_name"),
                    "min_score": item.get("min"),
                    "min_rank": item.get("min_section"),
                }
    return {}


def probe_clean_db(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    rows = []
    for province in PROVINCES.values():
        row = conn.execute(
            """
            SELECT count(*) AS total,
                   coalesce(sum(is_usable), 0) AS usable,
                   coalesce(sum(rank_reliable), 0) AS rank_reliable,
                   min(year),
                   max(year)
            FROM admission_records
            WHERE province = ?
            """,
            (province,),
        ).fetchone()
        rows.append({
            "province": province,
            "records": row[0],
            "usable_records": row[1],
            "rank_reliable_records": row[2],
            "min_year": row[3],
            "max_year": row[4],
        })
    conn.close()
    return rows


def fetch_json(url: str, timeout: int) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        data = fetch_json_with_curl(url, timeout)
        if data is None:
            return None
    if data.get("code") != "0000":
        return None
    return data


def fetch_json_with_curl(url: str, timeout: int) -> dict[str, Any] | None:
    if not shutil.which("curl"):
        return None
    try:
        raw = subprocess.check_output([
            "curl", "-L", "--fail", "--silent", "--show-error",
            "--connect-timeout", "8", "--max-time", str(timeout),
            "-A", "Mozilla/5.0", url,
        ])
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def render_markdown(report: dict[str, Any], schools: list[int]) -> str:
    lines = [
        "# 非官方/聚合数据源候选审计",
        "",
        "本报告只用于补齐官方公开源缺口，不写入 `official_admission.db`。所有数据应进入 fallback 数据层，并保留来源与可信等级。",
        "",
        "## 掌上高考静态 CDN 探测",
        "",
        f"- Endpoint: `{CDN_BASE}/school/{{school_id}}/provincescore/{{province_id}}.json`",
        f"- Sample school ids: `{', '.join(map(str, schools))}`",
        "- 字段：院校、省份、年份、科类、批次、最低分、最低位次。",
        "",
        "| 省份 | 省份ID | 样本命中学校数 | 状态 | 示例 |",
        "|---|---:|---:|---|---|",
    ]
    for row in report["static_data_gaokao_cn"]:
        sample = row["hits"][0]["sample"] if row["hits"] else {}
        text = ""
        if sample:
            text = f"{sample.get('year')} {sample.get('type_name')} {sample.get('batch_name')} min={sample.get('min_score')} rank={sample.get('min_rank')}"
        lines.append(f"| {row['province']} | {row['province_id']} | {row['sample_school_hits']} | {row['status']} | {text} |")

    lines.extend([
        "",
        "## 本地开源清洗库覆盖",
        "",
        "- 来源：已拉取的 `xuefeng-agent` / `qiming-zhiyuan` 清洗库合并产物 `gaokao_clean.db`。",
        "- 风险：来源多为第三方整理/开源库，适合补推荐候选，不应标为官方投档线。",
        "",
        "| 省份 | 记录 | 可用记录 | 位次可靠 | 年份范围 |",
        "|---|---:|---:|---:|---|",
    ])
    for row in report["local_open_source_clean_db"]:
        years = ""
        if row["min_year"] and row["max_year"]:
            years = f"{row['min_year']}-{row['max_year']}"
        lines.append(f"| {row['province']} | {row['records']} | {row['usable_records']} | {row['rank_reliable_records']} | {years} |")

    lines.extend([
        "",
        "## 建议",
        "",
        "1. 官方考试院数据继续进入 `official_admission.db`。",
        "2. 掌上高考 CDN、EOL、开源库进入单独 fallback 表，字段保留 `source_url/source_dataset/trust_level`。",
        "3. 推荐引擎优先使用官方源；官方缺失时再使用 fallback，并在 UI 标注“第三方聚合数据”。",
    ])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
