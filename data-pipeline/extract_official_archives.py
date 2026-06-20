#!/usr/bin/env python3
"""Extract downloaded official zip archives into raw/official_extracted."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data-pipeline" / "raw" / "official"
OUT_DIR = ROOT / "data-pipeline" / "raw" / "official_extracted"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for zip_path in sorted(RAW_DIR.rglob("*.zip")):
        rel_parts = zip_path.relative_to(RAW_DIR).parts
        if len(rel_parts) < 4:
            continue
        target_dir = OUT_DIR.joinpath(*rel_parts[:-1], zip_path.stem)
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as z:
            for i, info in enumerate(z.infolist(), start=1):
                if info.is_dir():
                    continue
                name = decode_zip_name(info.filename)
                safe = sanitize_filename(Path(name).name or f"file-{i}")
                target = target_dir / safe
                if target.exists():
                    stem, suffix = target.stem, target.suffix
                    target = target_dir / f"{stem}-{i}{suffix}"
                data = z.read(info)
                target.write_bytes(data)
                manifest.append({
                    "archive": str(zip_path.relative_to(ROOT)),
                    "path": str(target.relative_to(ROOT)),
                    "bytes": len(data),
                })
                print(f"[OK] {target.relative_to(ROOT)} {len(data)} bytes")
    out = OUT_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {out}")


def decode_zip_name(name: str) -> str:
    # Python decodes legacy Chinese zip names as cp437 mojibake. Reinterpret as gbk.
    try:
        return name.encode("cp437").decode("gbk")
    except Exception:
        return name


def sanitize_filename(name: str) -> str:
    return re.sub(r"[\\\\/:*?\"<>|]+", "_", name)


if __name__ == "__main__":
    main()

