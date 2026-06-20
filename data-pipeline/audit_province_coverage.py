#!/usr/bin/env python3
"""Audit province-level official data coverage."""

from __future__ import annotations

import collections
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data-pipeline" / "source_registry.json"
OFFICIAL_DB = ROOT / "data-pipeline" / "output" / "official_admission.db"
OUT_MD = ROOT / "data-pipeline" / "output" / "province_coverage_audit.md"
OUT_JSON = ROOT / "data-pipeline" / "output" / "province_coverage_audit.json"

PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["sources"]
    reg_by_province: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for source in registry:
        reg_by_province[str(source.get("province", ""))].append(source)

    conn = sqlite3.connect(OFFICIAL_DB)
    conn.row_factory = sqlite3.Row
    records = {
        row["province"]: row
        for row in conn.execute(
            """
            SELECT province,
                   COUNT(*) records,
                   SUM(CASE WHEN rank IS NOT NULL THEN 1 ELSE 0 END) rank_records,
                   COUNT(DISTINCT year) years,
                   GROUP_CONCAT(DISTINCT year) year_list
            FROM official_admission_records
            GROUP BY province
            """
        )
    }
    queue: dict[str, list[str]] = collections.defaultdict(list)
    for row in conn.execute(
        """
        SELECT province, suffix, reason, COUNT(*) files
        FROM parse_queue
        GROUP BY province, suffix, reason
        ORDER BY province, suffix, reason
        """
    ):
        queue[row["province"]].append(f"{row['suffix']}:{row['reason']}:{row['files']}")

    rows = []
    for province in PROVINCES:
        record = records.get(province)
        reg_count = len(reg_by_province.get(province, []))
        structured = int(record["records"]) if record else 0
        rank_records = int(record["rank_records"] or 0) if record else 0
        year_list = str(record["year_list"] or "") if record else ""
        queue_items = queue.get(province, [])
        if structured > 0:
            status = "structured"
        elif queue_items:
            status = "downloaded_unparsed"
        elif reg_count:
            status = "registered_not_downloaded_or_empty"
        else:
            status = "missing_registry"
        rows.append({
            "province": province,
            "registry_sources": reg_count,
            "structured_records": structured,
            "rank_records": rank_records,
            "years": year_list,
            "queued": queue_items,
            "status": status,
        })

    OUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(rows)
    print(f"Audit: {OUT_MD}")


def write_markdown(rows: list[dict[str, Any]]) -> None:
    lines = ["# 省份官方数据覆盖审计", ""]
    lines.append("| 省份 | Registry | 结构化记录 | 含位次 | 年份 | 队列 | 状态 |")
    lines.append("|---|---:|---:|---:|---|---|---|")
    for row in rows:
        lines.append(
            "| {province} | {registry_sources} | {structured_records} | {rank_records} | {years} | {queued} | {status} |".format(
                province=row["province"],
                registry_sources=row["registry_sources"],
                structured_records=row["structured_records"],
                rank_records=row["rank_records"],
                years=row["years"],
                queued="<br>".join(row["queued"]),
                status=row["status"],
            )
        )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
