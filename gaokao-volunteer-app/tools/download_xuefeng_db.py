#!/usr/bin/env python3
"""
Download xuefeng-agent's bundled admission database with validation.

Network to GitHub can be unstable, so this script is intentionally explicit:
it downloads to data/admission_clean.db.gz, validates gzip, then decompresses
to data/admission_clean.db.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
GZ_PATH = os.path.join(DATA_DIR, "admission_clean.db.gz")
DB_PATH = os.path.join(DATA_DIR, "admission_clean.db")

URLS = [
    "https://api.github.com/repos/ziqihe10-droid/xuefeng-agent/contents/admission_clean.db.gz?ref=master",
    "https://raw.githubusercontent.com/ziqihe10-droid/xuefeng-agent/master/admission_clean.db.gz",
]


def run(cmd: list[str]) -> int:
    print("+", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


def valid_gzip(path: str) -> bool:
    try:
        with gzip.open(path, "rb") as f:
            while f.read(1024 * 1024):
                pass
        return True
    except Exception as exc:
        print(f"gzip validation failed: {exc}", flush=True)
        return False


def decompress() -> None:
    tmp = DB_PATH + ".tmp"
    with gzip.open(GZ_PATH, "rb") as gz:
        with open(tmp, "wb") as f:
            shutil.copyfileobj(gz, f)
    os.replace(tmp, DB_PATH)


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(DB_PATH):
        print(f"Already exists: {DB_PATH}", flush=True)
        return 0

    for url in URLS:
        headers = []
        if "api.github.com" in url:
            headers = ["-H", "Accept: application/vnd.github.raw"]
        code = run([
            "curl",
            "-L",
            "--fail",
            "--connect-timeout",
            "20",
            "--max-time",
            "600",
            "-C",
            "-",
            *headers,
            "-o",
            GZ_PATH,
            url,
        ])
        if code == 0 and os.path.exists(GZ_PATH) and valid_gzip(GZ_PATH):
            decompress()
            print(f"Ready: {DB_PATH}", flush=True)
            return 0
        print(f"download attempt failed or partial: {url}", flush=True)

    print("Could not download a complete database. Retry this script later.", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())

