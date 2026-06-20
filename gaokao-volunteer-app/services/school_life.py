"""
School life-quality lookup.

This module treats cn.colleges.chat as an auxiliary information source, not as
admission evidence. The local JSON cache should contain short attributed
summaries or links, depending on the project's license constraints.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import Any


VENDOR_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".vendor"))
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)

try:
    from pypinyin import lazy_pinyin
except Exception:  # pragma: no cover - dependency is optional at runtime
    lazy_pinyin = None

DEFAULT_DATA_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "school_life_quality.json"
)


@dataclass
class SchoolLifeInfo:
    school_name: str
    matched_name: str
    summary: str
    dormitory: str = ""
    campus: str = ""
    transport: str = ""
    food_delivery: str = ""
    network: str = ""
    schedule: str = ""
    source_url: str = ""
    license: str = "CC BY-NC-SA 4.0 if sourced from CollegesChat"
    confidence: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SchoolLifeRepository:
    def __init__(self, data_path: str = DEFAULT_DATA_PATH):
        self.data_path = os.path.abspath(data_path)
        self._items = self._load()
        self._aliases = self._build_aliases()

    def _load(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.data_path):
            return []
        with open(self.data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return list(data.get("schools", []))
        if isinstance(data, list):
            return data
        return []

    def _build_aliases(self) -> dict[str, dict[str, Any]]:
        aliases: dict[str, dict[str, Any]] = {}
        for item in self._items:
            names = {item.get("school_name", "")}
            names.update(item.get("aliases", []) or [])
            names.update(item.get("campuses", []) or [])
            for name in names:
                norm = self._normalize(name)
                if norm:
                    aliases[norm] = item
        return aliases

    @staticmethod
    def _normalize(name: str) -> str:
        return (
            (name or "")
            .replace("（", "(")
            .replace("）", ")")
            .replace(" ", "")
            .strip()
        )

    def find(self, school_name: str) -> SchoolLifeInfo | None:
        norm = self._normalize(school_name)
        if not norm:
            return None

        item = self._aliases.get(norm)
        if item is None:
            item = self._fuzzy_find(norm)
        if item is None:
            return None

        return SchoolLifeInfo(
            school_name=school_name,
            matched_name=item.get("school_name", school_name),
            summary=item.get("summary", ""),
            dormitory=item.get("dormitory", ""),
            campus=item.get("campus", ""),
            transport=item.get("transport", ""),
            food_delivery=item.get("food_delivery", ""),
            network=item.get("network", ""),
            schedule=item.get("schedule", ""),
            source_url=item.get("source_url", ""),
            license=item.get("license", "CC BY-NC-SA 4.0 if sourced from CollegesChat"),
            confidence=item.get("confidence", "unknown"),
        )

    def _fuzzy_find(self, normalized_name: str) -> dict[str, Any] | None:
        for key, item in self._aliases.items():
            if key and (key in normalized_name or normalized_name in key):
                return item
        return None


def default_life_link(school_name: str) -> str:
    slug = school_slug(school_name)
    return f"https://cn.colleges.chat/universities/{slug}/" if slug else "https://cn.colleges.chat/universities/"


def school_slug(school_name: str) -> str:
    text = (school_name or "").strip()
    text = re.sub(r"[（(]\s*(?:本部|校本部|主校区)\s*[）)]", "", text)
    text = text.replace("（", "(").replace("）", ")")
    if lazy_pinyin:
        tokens = lazy_pinyin(text, errors=lambda chars: list(chars))
    else:
        tokens = [text]
    parts: list[str] = []
    for token in tokens:
        for piece in re.findall(r"[A-Za-z0-9]+", str(token).lower()):
            if piece:
                parts.append(piece)
    return "-".join(parts)
