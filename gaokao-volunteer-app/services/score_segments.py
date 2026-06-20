"""Unified score-segment lookup for rank and equivalent-score conversion."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from services.xuefeng_data import COMPREHENSIVE_PROVINCES, PHYSICS_HISTORY_PROVINCES


PROVINCE_IDS = {
    "北京": 11, "天津": 12, "河北": 13, "山西": 14, "内蒙古": 15,
    "辽宁": 21, "吉林": 22, "黑龙江": 23, "上海": 31, "江苏": 32,
    "浙江": 33, "安徽": 34, "福建": 35, "江西": 36, "山东": 37,
    "河南": 41, "湖北": 42, "湖南": 43, "广东": 44, "广西": 45,
    "海南": 46, "重庆": 50, "四川": 51, "贵州": 52, "云南": 53,
    "西藏": 54, "陕西": 61, "甘肃": 62, "青海": 63, "宁夏": 64,
    "新疆": 65,
}


@dataclass
class ScoreSegmentStatus:
    ready: bool
    db_path: str
    message: str


class ScoreSegmentRepository:
    def __init__(self, app_dir: str):
        workspace_dir = os.path.dirname(app_dir)
        self.db_path = os.path.join(workspace_dir, "data-pipeline", "output", "score_segments.db")
        self.status = self._prepare()

    def _prepare(self) -> ScoreSegmentStatus:
        if not os.path.exists(self.db_path):
            return ScoreSegmentStatus(False, self.db_path, "missing score_segments.db")
        try:
            with sqlite3.connect(self.db_path) as conn:
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='score_segment_best'"
                ).fetchone()
                if not table:
                    return ScoreSegmentStatus(False, self.db_path, "missing score_segment_best table")
                rows = conn.execute("SELECT COUNT(*) FROM score_segment_best").fetchone()[0]
        except Exception as exc:
            return ScoreSegmentStatus(False, self.db_path, f"cannot open score_segments.db: {exc}")
        if rows <= 0:
            return ScoreSegmentStatus(False, self.db_path, "score_segment_best is empty")
        return ScoreSegmentStatus(True, self.db_path, f"score segments ready: {rows} rows")

    @property
    def ready(self) -> bool:
        return self.status.ready

    def coverage(self) -> dict[str, Any]:
        if not self.ready:
            return {"ready": False, "db_path": self.db_path, "message": self.status.message}
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute("SELECT COUNT(*) FROM score_segment_best").fetchone()[0]
            years = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT year, COUNT(*) AS rows, COUNT(DISTINCT province) AS province_count
                    FROM score_segment_best
                    GROUP BY year
                    ORDER BY year DESC
                    LIMIT 8
                    """
                )
            ]
            sources = [
                dict(row)
                for row in conn.execute(
                    "SELECT source_type, COUNT(*) AS rows FROM score_segment_best GROUP BY source_type ORDER BY rows DESC"
                )
            ]
        return {
            "ready": True,
            "db_path": self.db_path,
            "message": self.status.message,
            "record_count": total,
            "recent_years": years,
            "sources": sources,
        }

    def build_equivalent_scores(
        self,
        province: str,
        province_id: int | None,
        category: str,
        rank: int | None,
        score: int | None,
        years: list[int] | None = None,
    ) -> dict[str, Any]:
        years = years or [2025, 2024, 2023]
        province_id = province_id or PROVINCE_IDS.get(province)
        target_categories = target_categories_for_province(province, category)
        normalized_category = category_label_for_targets(province, category, target_categories)
        if not self.ready:
            return self._missing_payload(province, normalized_category, rank, score, years, self.status.message)
        if not province_id:
            return self._missing_payload(province, normalized_category, rank, score, years, "缺少省份 ID")
        if not target_categories:
            return self._missing_payload(province, normalized_category, rank, score, years, "缺少选科大类")

        rows = []
        found_years = set()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            anchor_rank = int(rank or 0)
            for target_category in target_categories:
                category_anchor_rank = anchor_rank
                anchor_row = None
                if not category_anchor_rank and score:
                    anchor_year = years[0] if years else 2025
                    anchor_row = self._score_to_rank(conn, province_id, anchor_year, target_category, int(score))
                    if anchor_row:
                        category_anchor_rank = int(anchor_row["cumulative_rank"])
                for year in years:
                    target = None
                    if category_anchor_rank:
                        target = self._rank_to_score(conn, province_id, year, target_category, category_anchor_rank)
                        if target and anchor_row and year == anchor_row["year"]:
                            target["lookup"] = "score_to_rank_to_score"
                    elif score:
                        target = self._score_to_rank(conn, province_id, year, target_category, int(score))
                    if target:
                        rows.append(target)
                        found_years.add(year)

        rows.sort(key=lambda row: (int(row.get("year") or 0), str(row.get("category") or "")), reverse=True)
        missing_years = [year for year in years if year not in found_years]

        status = "ok" if rows and not missing_years else ("partial" if rows else "missing")
        message = {
            "ok": "已按位次换算出目标年份等位分。",
            "partial": f"已换算部分年份；缺少 {', '.join(map(str, missing_years))} 年数据。",
            "missing": "没有找到该省份/科类的一分一段表，无法换算等位分。",
        }[status]
        if 2025 in missing_years:
            message += " 2025 年最有参考意义，建议优先补齐。"

        return {
            "status": status,
            "province": province,
            "province_id": province_id,
            "category": normalized_category,
            "rank": anchor_rank or rank,
            "score": score,
            "years": rows,
            "missing_years": missing_years,
            "message": message,
            "blocking": not rows,
        }

    def _rank_to_score(
        self,
        conn: sqlite3.Connection,
        province_id: int,
        year: int,
        category: str,
        rank: int,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT province, province_id, year, category, score_high, score_low,
                   same_score_count, cumulative_rank, source_type, source_dataset,
                   source_priority, quality_flags
            FROM score_segment_best
            WHERE province_id=? AND year=? AND category=? AND cumulative_rank >= ?
            ORDER BY cumulative_rank ASC, score_high DESC
            LIMIT 1
            """,
            (province_id, year, category, rank),
        ).fetchone()
        if not row:
            return None
        return dict(row) | {"equivalent_score": row["score_high"], "lookup": "rank_to_score"}

    def _score_to_rank(
        self,
        conn: sqlite3.Connection,
        province_id: int,
        year: int,
        category: str,
        score: int,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT province, province_id, year, category, score_high, score_low,
                   same_score_count, cumulative_rank, source_type, source_dataset,
                   source_priority, quality_flags
            FROM score_segment_best
            WHERE province_id=? AND year=? AND category=? AND score_high >= ? AND score_low <= ?
            ORDER BY source_priority DESC, score_high DESC
            LIMIT 1
            """,
            (province_id, year, category, score, score),
        ).fetchone()
        if not row:
            return None
        return dict(row) | {"equivalent_score": row["score_high"], "lookup": "score_to_rank"}

    @staticmethod
    def _missing_payload(
        province: str,
        category: str,
        rank: int | None,
        score: int | None,
        years: list[int],
        message: str,
    ) -> dict[str, Any]:
        return {
            "status": "missing",
            "province": province,
            "category": category,
            "rank": rank,
            "score": score,
            "years": [],
            "missing_years": years,
            "message": message,
            "blocking": True,
        }


def normalize_category_for_province(province: str, category: str) -> str:
    targets = target_categories_for_province(province, category)
    if not targets:
        return ""
    return targets[0] if len(targets) == 1 else category_label_for_targets(province, category, targets)


def target_categories_for_province(province: str, category: str) -> list[str]:
    text = (category or "").strip()
    if not text:
        return []
    if province in COMPREHENSIVE_PROVINCES:
        return ["综合"]
    if is_broad_category(text):
        if province in PHYSICS_HISTORY_PROVINCES:
            return ["物理类", "历史类"]
        return ["理科", "文科"]
    if province in PHYSICS_HISTORY_PROVINCES:
        if "物理" in text or "理" in text:
            return ["物理类"]
        if "历史" in text or "文" in text:
            return ["历史类"]
    if "物理" in text:
        return ["物理类"]
    if "历史" in text:
        return ["历史类"]
    if "综合" in text or "普通" in text:
        return ["综合"]
    if "理" in text:
        return ["理科"]
    if "文" in text:
        return ["文科"]
    return [text]


def is_broad_category(text: str) -> bool:
    return text in {"不限", "全部", "综合", "普通类", "综合/普通类", "不分文理", "文理综合"}


def category_label_for_targets(province: str, category: str, targets: list[str]) -> str:
    if len(targets) <= 1:
        return targets[0] if targets else (category or "")
    return "不限(" + "/".join(targets) + ")"
