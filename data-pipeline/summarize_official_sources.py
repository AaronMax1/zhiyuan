#!/usr/bin/env python3
"""Summarize official source registry and local downloaded files."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "data-pipeline" / "source_registry.json"
INVENTORY = ROOT / "data-pipeline" / "raw" / "official" / "local_inventory.json"
OUT = ROOT / "data-pipeline" / "raw" / "official" / "download_summary.md"

ALL_PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]


def main() -> None:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))["sources"]
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8")) if INVENTORY.exists() else []

    reg_by_prov = defaultdict(list)
    for item in registry:
        reg_by_prov[item["province"]].append(item)

    file_by_prov = defaultdict(list)
    for item in inventory:
        file_by_prov[item["province"]].append(item)

    lines = ["# 官方数据下载覆盖报告", ""]
    lines.append(f"- Registry 条目：{len(registry)}")
    lines.append(f"- 已下载文件：{len(inventory)}")
    lines.append(f"- Registry 覆盖省份：{len(reg_by_prov)}")
    lines.append(f"- 已下载覆盖省份：{len(file_by_prov)}")
    lines.append("")
    lines.append("## 省份覆盖")
    lines.append("")
    lines.append("| 省份 | Registry 条目 | 已下载文件 | 文件类型 | 状态 |")
    lines.append("|---|---:|---:|---|---|")
    for province in ALL_PROVINCES:
        entries = reg_by_prov.get(province, [])
        files = file_by_prov.get(province, [])
        suffixes = Counter(f["suffix"] for f in files)
        suffix_text = ", ".join(f"{k}:{v}" for k, v in sorted(suffixes.items())) if suffixes else ""
        if files:
            status = "downloaded"
        elif entries:
            status = "registered_no_file"
        else:
            status = "needs_manual_check"
        lines.append(f"| {province} | {len(entries)} | {len(files)} | {suffix_text} | {status} |")

    missing = [p for p in ALL_PROVINCES if p not in file_by_prov]
    lines.extend(["", "## 待人工确认省份", ""])
    for province in missing:
        lines.append(f"- {province}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Summary: {OUT}")


if __name__ == "__main__":
    main()

