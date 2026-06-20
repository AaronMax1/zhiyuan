#!/usr/bin/env python3
"""Download official image-only admission sources.

Some education examination authorities publish admission lines as image
sequences rather than tables or attachments. This script stores those images in
the same raw official tree so they are visible in inventory/import queues.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "data-pipeline" / "source_registry.json"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "raw" / "official"
IMG_RE = re.compile(r"""<img[^>]+src=["'](?P<src>[^"']+\.(?:jpg|jpeg|png)(?:\?[^"']*)?)["']""", re.I)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only-province", default="")
    parser.add_argument("--only-year", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--connect-timeout", type=int, default=8)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))["sources"]
    manifest_path = args.output_dir / "manifest.json"
    manifest = load_manifest(manifest_path)

    for source in registry:
        if args.only_province and source.get("province") != args.only_province:
            continue
        if args.only_year and int(source.get("year", 0)) != args.only_year:
            continue
        if not should_process(source):
            continue

        try:
            image_urls = discover_image_urls(source, args.timeout, args.connect_timeout)
        except Exception as exc:
            print(f"[ERR] discover {source.get('id')}: {exc}")
            continue
        if not image_urls:
            print(f"[WARN] no images for {source.get('id')}")
            continue

        rel_dir = Path(str(source["province"])) / str(source["year"]) / str(source["id"])
        target_dir = args.output_dir / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for index, url in enumerate(image_urls, start=1):
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".jpg"
            filename = f"{index:03d}-{Path(urllib.parse.urlparse(url).path).name or 'image'}"
            if not filename.lower().endswith((".jpg", ".jpeg", ".png")):
                filename += suffix
            target = target_dir / filename
            entry = {
                "source_id": source["id"],
                "province": source["province"],
                "year": source["year"],
                "category": source.get("category", ""),
                "batch": source.get("batch", ""),
                "publisher": source.get("publisher", ""),
                "page_url": source.get("page_url", ""),
                "url": url,
                "path": str(target.relative_to(ROOT)),
                "status": "pending",
            }
            if target.exists() and not args.force:
                entry.update(file_meta(target))
                entry["status"] = "exists"
                print(f"[SKIP] {entry['path']}")
            else:
                try:
                    download(url, target, args.timeout, args.connect_timeout)
                    entry.update(file_meta(target))
                    entry["status"] = "downloaded"
                    print(f"[OK] {entry['path']} {entry['bytes']} bytes")
                except Exception as exc:
                    entry["status"] = "error"
                    entry["error"] = str(exc)
                    print(f"[ERR] {url}: {exc}")
            manifest.append(entry)

        manifest = merge_manifest(manifest)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = merge_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


def should_process(source: dict[str, Any]) -> bool:
    return bool(source.get("image_api_url") or source.get("source_type") == "official_image_page")


def discover_image_urls(source: dict[str, Any], timeout: int, connect_timeout: int) -> list[str]:
    if source.get("image_api_url"):
        data = fetch_json(source["image_api_url"], timeout, connect_timeout)
        content = ((data.get("data") or {}).get("content") or "")
        base = source.get("page_url") or source["image_api_url"]
        return extract_images(content, base)
    page_url = source.get("page_url")
    if not page_url:
        return []
    raw = fetch_bytes(page_url, timeout, connect_timeout)
    text = decode_page(raw)
    return [u for u in extract_images(text, page_url) if not looks_like_site_asset(u)]


def extract_images(text: str, base_url: str) -> list[str]:
    urls = []
    for match in IMG_RE.finditer(text):
        src = html.unescape(match.group("src"))
        urls.append(urllib.parse.urljoin(base_url, src))
    return dedupe(urls)


def looks_like_site_asset(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    asset_names = ("logo", "icon", "ewm", "qrcode", "ghs.", "alipay.", "wx.", "blue.")
    return any(name in path for name in asset_names) or "/images/" in path


def fetch_json(url: str, timeout: int, connect_timeout: int) -> dict[str, Any]:
    raw = fetch_bytes(url, timeout, connect_timeout)
    return json.loads(raw.decode("utf-8"))


def fetch_bytes(url: str, timeout: int, connect_timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": origin(url)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception:
        return subprocess.check_output([
            "curl", "-L", "--fail", "--silent", "--show-error",
            "--connect-timeout", str(connect_timeout), "--max-time", str(timeout),
            "-A", "Mozilla/5.0", "-e", origin(url), url,
        ])


def download(url: str, target: Path, timeout: int, connect_timeout: int) -> None:
    tmp = target.with_suffix(target.suffix + ".tmp")
    if shutil.which("curl"):
        subprocess.run([
            "curl", "-L", "--fail", "--silent", "--show-error",
            "--connect-timeout", str(connect_timeout), "--max-time", str(timeout),
            "--retry", "2", "--retry-delay", "1",
            "--speed-time", "30", "--speed-limit", "1024",
            "-A", "Mozilla/5.0", "-e", origin(url),
            "-o", str(tmp), url,
        ], check=True)
    else:
        data = fetch_bytes(url, timeout, connect_timeout)
        if not data:
            raise RuntimeError("empty download")
        tmp.write_bytes(data)
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("empty download")
    tmp.replace(target)


def decode_page(raw: bytes) -> str:
    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="ignore")


def file_meta(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "suffix": path.suffix.lower()}


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def merge_manifest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {(str(item.get("source_id")), str(item.get("url"))): item for item in items}
    return sorted(merged.values(), key=lambda x: (str(x.get("province")), str(x.get("year")), str(x.get("source_id")), str(x.get("url"))))


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


if __name__ == "__main__":
    main()
