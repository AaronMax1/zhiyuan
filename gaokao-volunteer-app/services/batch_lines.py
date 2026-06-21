"""Province batch-control-line lookup."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Any

from services.score_segments import PROVINCE_IDS, target_categories_for_province


@dataclass
class BatchLineStatus:
    ready: bool
    db_path: str
    message: str


class BatchControlLineRepository:
    def __init__(self, app_dir: str):
        workspace_dir = os.path.dirname(app_dir)
        self.db_path = os.path.join(workspace_dir, "data-pipeline", "output", "batch_control_lines.db")
        self.status = self._prepare()

    @property
    def ready(self) -> bool:
        return self.status.ready

    def _prepare(self) -> BatchLineStatus:
        if not os.path.exists(self.db_path):
            return BatchLineStatus(False, self.db_path, "missing batch_control_lines.db")
        try:
            with sqlite3.connect(self.db_path) as conn:
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_control_lines'"
                ).fetchone()
                if not table:
                    return BatchLineStatus(False, self.db_path, "missing batch_control_lines table")
                rows = conn.execute("SELECT COUNT(*) FROM batch_control_lines").fetchone()[0]
        except Exception as exc:
            return BatchLineStatus(False, self.db_path, f"cannot open batch_control_lines.db: {exc}")
        return BatchLineStatus(rows > 0, self.db_path, f"batch control lines ready: {rows} rows")

    def coverage(self) -> dict[str, Any]:
        if not self.ready:
            return {"ready": False, "db_path": self.db_path, "message": self.status.message}
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT province, category, COUNT(*) AS line_count,
                           GROUP_CONCAT(line_type || ':' || score, ' / ') AS lines
                    FROM batch_control_lines
                    GROUP BY province, category
                    ORDER BY province_id, category
                    """
                )
            ]
        return {"ready": True, "db_path": self.db_path, "message": self.status.message, "groups": rows}

    def for_profile(self, profile: dict[str, Any], year: int = 2025) -> dict[str, Any]:
        province = str(profile.get("province") or "")
        province_id = int(profile.get("province_id") or PROVINCE_IDS.get(province) or 0)
        category = str(profile.get("category") or "")
        score = int(profile.get("score") or 0)
        education_level = str(profile.get("education_level") or "")
        target_categories = target_categories_for_province(province, category)

        payload: dict[str, Any] = {
            "ready": self.ready,
            "db_path": self.db_path,
            "province": province,
            "province_id": province_id,
            "year": year,
            "category": category,
            "target_categories": target_categories,
            "lines": [],
            "warnings": [],
        }
        if not self.ready:
            payload["warnings"].append(self.status.message)
            return payload
        if not province_id or not target_categories:
            payload["warnings"].append("缺少省份或选科大类，无法匹配省控线。")
            return payload

        placeholders = ",".join("?" for _ in target_categories)
        sql = f"""
            SELECT province, province_id, year, category, line_type, score,
                   source_type, source_url, source_title, confidence
            FROM batch_control_lines
            WHERE province_id=? AND year=? AND category IN ({placeholders})
            ORDER BY category, score DESC
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(sql, [province_id, year, *target_categories])]
        payload["lines"] = rows

        by_category: dict[str, set[str]] = {}
        for row in rows:
            by_category.setdefault(row["category"], set()).add(row["line_type"])
        for target in target_categories:
            types = by_category.get(target, set())
            if not types & {"本科线", "一段线", "一本线", "二本线"}:
                payload["warnings"].append(f"{target} 未收录 2025 本科/一段控制线。")
            if not types & {"专科线", "二段线"}:
                payload["warnings"].append(f"{target} 未收录 2025 专科/二段控制线。")

        threshold = self._threshold_for_level(rows, education_level)
        if threshold:
            payload["matched_threshold"] = threshold
            if score and score < int(threshold["score"]):
                payload["warnings"].append(
                    f"当前分数 {score} 低于{threshold['category']}{threshold['line_type']} {threshold['score']}，"
                    "对应层次候选需要谨慎。"
                )
        return payload

    @staticmethod
    def _threshold_for_level(rows: list[dict[str, Any]], education_level: str) -> dict[str, Any] | None:
        if not rows:
            return None
        if "专科" in education_level:
            preferred = {"专科线", "二段线"}
        else:
            preferred = {"本科线", "一段线", "二本线"}
        candidates = [row for row in rows if row.get("line_type") in preferred]
        if not candidates:
            return None
        return min(candidates, key=lambda row: int(row["score"]))
