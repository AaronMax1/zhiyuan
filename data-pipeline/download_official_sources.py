#!/usr/bin/env python3
"""Download official gaokao source attachments listed in source_registry.json."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "data-pipeline" / "source_registry.json"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "raw" / "official"
ATTACHMENT_RE = re.compile(
    r"""href=["'](?P<href>[^"']+\.(?:xls|xlsx|pdf|doc|docx|zip|rar)(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--only-province", default="")
    parser.add_argument("--only-year", type=int, default=0)
    parser.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--no-discover", action="store_true", help="Do not parse article pages; use attachment_urls only.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-download files that already exist.")
    parser.add_argument("--include-pages", action="store_true", help="Save page_url as HTML when no attachment URLs are available.")
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    sources = registry.get("sources", [])
    args.output_dir.mkdir(parents=True, exist_ok=True)

    opener = build_opener(args.proxy)
    manifest_path = args.output_dir / "manifest.json"
    manifest: list[dict[str, Any]] = load_existing_manifest(manifest_path)

    for source in sources:
        if args.only_province and source.get("province") != args.only_province:
            continue
        if args.only_year and int(source.get("year", 0)) != args.only_year:
            continue

        urls = list(source.get("attachment_urls") or [])
        discovered = []
        if source.get("page_url") and not args.no_discover:
            try:
                discovered = discover_attachments(opener, source["page_url"], args.timeout)
            except Exception as exc:
                print(f"[WARN] cannot parse page {source['page_url']}: {exc}")
        for url in discovered:
            if url not in urls:
                urls.append(url)

        if not urls:
            if args.include_pages and source.get("page_url"):
                urls = [source["page_url"]]
            else:
                print(f"[WARN] no attachments for {source.get('id')}")
                continue

        for index, url in enumerate(urls, start=1):
            rel_dir = Path(str(source.get("province", "unknown"))) / str(source.get("year", "unknown")) / source.get("id", "source")
            target_dir = args.output_dir / rel_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = filename_from_url(url, fallback=f"{source.get('id', 'source')}-{index}")
            target = target_dir / filename
            entry = {
                "source_id": source.get("id"),
                "province": source.get("province"),
                "year": source.get("year"),
                "category": source.get("category", ""),
                "batch": source.get("batch", ""),
                "publisher": source.get("publisher", ""),
                "page_url": source.get("page_url", ""),
                "url": url,
                "path": str(target.relative_to(ROOT)),
                "status": "pending",
            }
            if args.dry_run:
                entry["status"] = "dry_run"
                print(f"[DRY] {url} -> {target}")
            elif target.exists() and not args.force:
                meta = file_meta(target)
                entry.update(meta)
                entry["status"] = "exists"
                print(f"[SKIP] {entry['path']} {entry['bytes']} bytes")
            else:
                try:
                    meta = download_with_retries(opener, url, target, args.timeout, args.retries)
                    entry.update(meta)
                    entry["status"] = "downloaded"
                    print(f"[OK] {entry['path']} {entry['bytes']} bytes")
                except Exception as exc:
                    entry["status"] = "error"
                    entry["error"] = str(exc)
                    print(f"[ERR] {url}: {exc}")
            manifest.append(entry)

    manifest = merge_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest_path}")


def build_opener(proxy: str):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def load_existing_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def merge_manifest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (str(item.get("source_id", "")), str(item.get("url", "")))
        merged[key] = item
    return sorted(
        merged.values(),
        key=lambda x: (str(x.get("province", "")), str(x.get("year", "")), str(x.get("source_id", "")), str(x.get("url", ""))),
    )


def discover_attachments(opener, page_url: str, timeout: int) -> list[str]:
    req = urllib.request.Request(page_url, headers=request_headers(page_url))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="ignore")
    urls = []
    for match in ATTACHMENT_RE.finditer(text):
        href = html.unescape(match.group("href"))
        urls.append(urllib.parse.urljoin(page_url, href))
    return dedupe(urls)


def download_with_retries(opener, url: str, target: Path, timeout: int, retries: int) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return download_once(opener, url, target, timeout)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(min(2 * attempt, 6))
    if shutil.which("curl"):
        return download_with_curl(url, target, timeout)
    raise RuntimeError(last_exc)


def download_once(opener, url: str, target: Path, timeout: int) -> dict[str, Any]:
    tmp = target.with_suffix(target.suffix + ".tmp")
    req = urllib.request.Request(url, headers=request_headers(url))
    sha = hashlib.sha256()
    total = 0
    with opener.open(req, timeout=timeout) as resp:
        content_type = resp.headers.get("content-type", "")
        with open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1024 * 128)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                total += len(chunk)
    if total == 0:
        raise RuntimeError("empty download")
    tmp.replace(target)
    return {
        "bytes": total,
        "sha256": sha.hexdigest(),
        "content_type": content_type,
    }


def download_with_curl(url: str, target: Path, timeout: int) -> dict[str, Any]:
    tmp = target.with_suffix(target.suffix + ".tmp")
    cmd = [
        "curl",
        "-L",
        "--fail",
        "--silent",
        "--show-error",
        "--connect-timeout",
        str(min(timeout, 30)),
        "--max-time",
        str(timeout),
        "-A",
        "Mozilla/5.0",
        "-e",
        origin(url),
        "-o",
        str(tmp),
        url,
    ]
    subprocess.run(cmd, check=True)
    data = tmp.read_bytes()
    if not data:
        raise RuntimeError("empty download")
    tmp.replace(target)
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": "",
        "download_method": "curl",
    }


def file_meta(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content_type": "",
    }


def request_headers(url: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 gaokao-data-pipeline/1.0",
        "Referer": origin(url),
    }


def origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def filename_from_url(url: str, fallback: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    for key in ("filename", "showname", "fileName"):
        value = query.get(key)
        if value and value[0]:
            return sanitize_filename(urllib.parse.unquote(value[0]))
    name = Path(urllib.parse.unquote(parsed.path)).name
    if not name or "." not in name:
        return fallback + ".html"
    parent = Path(urllib.parse.unquote(parsed.path)).parent.name
    # Some official sites expose multiple attachments with the same basename
    # under different numeric parent directories, e.g. .../585885/4746781.pdf.
    if parent and parent.isdigit():
        stem = Path(name).stem
        suffix = Path(name).suffix
        return sanitize_filename(f"{parent}-{stem}{suffix}")
    return sanitize_filename(name)


def sanitize_filename(name: str) -> str:
    return re.sub(r"[\\\\/:*?\"<>|]+", "_", name)


def dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


if __name__ == "__main__":
    main()
