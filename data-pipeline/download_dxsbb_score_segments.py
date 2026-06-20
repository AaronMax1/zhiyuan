#!/usr/bin/env python3
"""Download Dxsbb score-segment article HTML and images.

The dxsbb pages are mostly image tables. This script intentionally keeps raw
HTML/images plus a manifest so OCR/table extraction can be handled separately.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX_URL = "https://www.dxsbb.com/news/list_223.html"
DEFAULT_OUTPUT = ROOT / "data-pipeline" / "raw" / "score_segments" / "dxsbb"
REPORT_JSON = ROOT / "data-pipeline" / "output" / "dxsbb_score_segments_manifest.json"
REPORT_MD = ROOT / "data-pipeline" / "output" / "dxsbb_score_segments_report.md"
BASE_URL = "https://www.dxsbb.com"

PROVINCES = [
    "内蒙古", "黑龙江", "北京", "天津", "河北", "山西", "辽宁", "吉林",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.dxsbb.com/",
}


@dataclass
class ImageItem:
    url: str
    alt: str
    title: str
    path: str
    bytes: int
    status: str
    error: str = ""


@dataclass
class ArticleItem:
    title: str
    url: str
    province: str
    category: str
    year: int
    article_path: str
    image_count: int
    images: list[ImageItem]
    status: str
    error: str = ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-url", default=DEFAULT_INDEX_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--delay", type=float, default=1.2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--transport", choices=["curl", "python", "auto"], default="curl")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "data-pipeline" / "output").mkdir(parents=True, exist_ok=True)

    print(f"Fetching index: {args.index_url}")
    index_bytes = fetch_bytes(args.index_url, args.transport)
    index_html = decode_html(index_bytes)
    index_path = args.output_dir / f"list_223_{args.year}.html"
    index_path.write_text(index_html, encoding="utf-8")

    articles = discover_articles(index_html, args.index_url, args.year)
    if args.limit:
        articles = articles[: args.limit]
    print(f"Discovered {len(articles)} article(s) for {args.year}")

    results: list[ArticleItem] = []
    for idx, article in enumerate(articles, start=1):
        print(f"[{idx}/{len(articles)}] {article['title']} -> {article['url']}")
        try:
            result = download_article(article, args.output_dir, args.transport, args.force)
        except Exception as exc:
            result = ArticleItem(
                title=article["title"],
                url=article["url"],
                province=article["province"],
                category=article["category"],
                year=article["year"],
                article_path="",
                image_count=0,
                images=[],
                status="error",
                error=str(exc),
            )
            print(f"  ERROR: {exc}")
        results.append(result)
        write_reports(args, index_path, results)
        if idx < len(articles):
            time.sleep(args.delay)

    write_reports(args, index_path, results)
    ok_images = sum(1 for item in results for img in item.images if img.status == "ok")
    print(f"Done. Articles: {len(results)}, images downloaded/reused: {ok_images}")
    print(f"Manifest: {REPORT_JSON}")


def discover_articles(index_html: str, index_url: str, year: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    articles: list[dict[str, Any]] = []
    for href, raw_text in re.findall(
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        index_html,
        flags=re.I | re.S,
    ):
        text = clean_text(raw_text)
        title_year = detect_title_year(text)
        if title_year != year or "高考一分一段表" not in text:
            continue
        url = urllib.parse.urljoin(index_url, href)
        if url in seen:
            continue
        seen.add(url)
        articles.append(
            {
                "title": text,
                "url": url,
                "province": detect_province(text),
                "category": detect_category(text),
                "year": year,
            }
        )
    return articles


def download_article(
    article: dict[str, Any],
    output_dir: Path,
    transport: str,
    force: bool,
) -> ArticleItem:
    province = article["province"] or "未知省份"
    category = article["category"] or "未知科类"
    article_id = Path(urllib.parse.urlparse(article["url"]).path).stem
    article_dir = output_dir / str(article["year"]) / safe_name(province) / f"{article_id}_{safe_name(category)}"
    image_dir = article_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    article_path = article_dir / "article.html"

    if article_path.exists() and not force:
        article_html = article_path.read_text(encoding="utf-8", errors="replace")
    else:
        article_html = decode_html(fetch_bytes(article["url"], transport))
        article_path.write_text(article_html, encoding="utf-8")

    title = extract_h1(article_html) or article["title"]
    image_refs = extract_article_images(article_html, article["year"], province, category)
    images: list[ImageItem] = []
    seen_urls: set[str] = set()
    for image_index, image in enumerate(image_refs, start=1):
        image_url = image["url"]
        if image_url in seen_urls:
            continue
        seen_urls.add(image_url)
        image_path = image_dir / image_filename(image_index, image_url)
        status = "ok"
        error = ""
        try:
            if image_path.exists() and not force:
                size = image_path.stat().st_size
            else:
                data = fetch_bytes(image_url, transport, binary=True)
                image_path.write_bytes(data)
                size = len(data)
                time.sleep(0.2)
        except Exception as exc:
            status = "error"
            error = str(exc)
            size = 0
            print(f"  image error: {image_url} {exc}")
        images.append(
            ImageItem(
                url=image_url,
                alt=image["alt"],
                title=image["title"],
                path=str(image_path.relative_to(ROOT)),
                bytes=size,
                status=status,
                error=error,
            )
        )
    print(f"  images: {sum(1 for img in images if img.status == 'ok')}/{len(images)}")
    return ArticleItem(
        title=title,
        url=article["url"],
        province=province,
        category=category,
        year=article["year"],
        article_path=str(article_path.relative_to(ROOT)),
        image_count=len(images),
        images=images,
        status="ok",
    )


def extract_article_images(article_html: str, year: int, province: str, category: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for tag in re.findall(r"<img\b[^>]*>", article_html, flags=re.I | re.S):
        attrs = parse_attrs(tag)
        src = attrs.get("data-echo") or attrs.get("src") or ""
        if not src:
            continue
        url = urllib.parse.urljoin(BASE_URL, html.unescape(src))
        alt = html.unescape(attrs.get("alt", ""))
        title = html.unescape(attrs.get("title", ""))
        label = f"{alt} {title}"
        if not image_matches(label, url, year, province, category):
            continue
        refs.append({"url": url, "alt": alt, "title": title})
    return refs


def image_matches(label: str, url: str, year: int, province: str, category: str) -> bool:
    if "logo" in url or "beian" in url or "echo.gif" in url:
        return False
    if str(year) not in label or province not in label:
        return False
    if category in {"物理类", "历史类", "理科", "文科", "综合"} and category not in label:
        return False
    return True


def fetch_bytes(url: str, transport: str, binary: bool = False) -> bytes:
    if transport in {"python", "auto"}:
        try:
            return fetch_with_python(url)
        except Exception:
            if transport == "python":
                raise
    return fetch_with_curl(url, binary=binary)


def fetch_with_python(url: str) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def fetch_with_curl(url: str, binary: bool = False) -> bytes:
    cmd = [
        "curl",
        "-L",
        "--compressed",
        "--retry",
        "3",
        "--retry-delay",
        "1",
        "--connect-timeout",
        "20",
        "--max-time",
        "90",
        "-A",
        HEADERS["User-Agent"],
        "-H",
        f"Referer: {HEADERS['Referer']}",
        "-sS",
        url,
    ]
    result = subprocess.run(cmd, check=True, capture_output=True)
    return result.stdout if binary else result.stdout


def write_reports(args: argparse.Namespace, index_path: Path, results: list[ArticleItem]) -> None:
    payload = {
        "source": "dxsbb",
        "index_url": args.index_url,
        "index_path": str(index_path.relative_to(ROOT)),
        "year": args.year,
        "output_dir": str(args.output_dir.relative_to(ROOT)),
        "article_count": len(results),
        "image_count": sum(len(item.images) for item in results),
        "ok_image_count": sum(1 for item in results for image in item.images if image.status == "ok"),
        "articles": [
            asdict(item) | {"images": [asdict(image) for image in item.images]}
            for item in results
        ],
    }
    REPORT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Dxsbb Score Segments Download Report",
        "",
        f"- Index: {args.index_url}",
        f"- Year: {args.year}",
        f"- Articles: {payload['article_count']}",
        f"- Images: {payload['ok_image_count']}/{payload['image_count']}",
        "",
        "## Articles",
        "",
    ]
    for item in results:
        lines.append(f"- {item.province} {item.category}: {item.title} ({item.image_count} images) - {item.status}")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def decode_html(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_attrs(tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, _quote, value in re.findall(r"([:\w-]+)\s*=\s*([\"'])(.*?)\2", tag, flags=re.S):
        attrs[key.lower()] = value
    return attrs


def extract_h1(article_html: str) -> str:
    match = re.search(r"<h1[^>]*>(.*?)</h1>", article_html, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def clean_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def detect_province(title: str) -> str:
    for province in PROVINCES:
        if province in title:
            return province
    return ""


def detect_title_year(title: str) -> int | None:
    match = re.search(r"(20\d{2}).{0,12}高考一分一段表", title)
    return int(match.group(1)) if match else None


def detect_category(title: str) -> str:
    if "物理类+历史类" in title or ("物理类" in title and "历史类" in title):
        return "物理类+历史类"
    for category in ("物理类", "历史类", "理科", "文科"):
        if category in title:
            return category
    return "综合"


def safe_name(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff+-]+", "_", value).strip("_") or short_hash(value)


def image_filename(index: int, url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".img"
    return f"{index:03d}_{short_hash(url)}{suffix}"


def short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


if __name__ == "__main__":
    main()
