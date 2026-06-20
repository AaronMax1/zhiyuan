#!/usr/bin/env python3
"""Create a local inventory for downloaded official source files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data-pipeline" / "raw" / "official"
OUT = RAW_DIR / "local_inventory.json"


def main() -> None:
    rows = []
    for path in sorted(RAW_DIR.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "local_inventory.json", "download_summary.md"}:
            continue
        if is_site_asset(path):
            continue
        rel = path.relative_to(ROOT)
        parts = path.relative_to(RAW_DIR).parts
        province = parts[0] if len(parts) > 0 else ""
        year = parts[1] if len(parts) > 1 else ""
        source_id = parts[2] if len(parts) > 2 else ""
        data = path.read_bytes()
        rows.append({
            "province": province,
            "year": year,
            "source_id": source_id,
            "path": str(rel),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "suffix": path.suffix.lower(),
        })
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Inventory: {OUT}")
    print(f"Files: {len(rows)}")
    by_province = {}
    for row in rows:
        by_province[row["province"]] = by_province.get(row["province"], 0) + 1
    for province, count in sorted(by_province.items()):
        print(f"{province}: {count}")


def is_site_asset(path: Path) -> bool:
    name = path.name.lower()
    return any(token in name for token in ("logo", "icon", "ewm", "qrcode", "ghs.", "alipay.", "wx.", "blue."))


if __name__ == "__main__":
    main()
