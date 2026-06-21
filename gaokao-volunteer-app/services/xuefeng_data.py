"""
Adapter for the local admissions database.

The preferred source is data-pipeline/output/unified_admission.db. The original
xuefeng-agent admission_clean.db remains supported as a fallback so the app can
still run in a minimal setup.
"""

from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
from dataclasses import dataclass
from typing import Any


PHYSICS_HISTORY_PROVINCES = {
    "河北", "辽宁", "江苏", "福建", "湖北", "湖南", "广东", "重庆",
    "黑龙江", "吉林", "安徽", "江西", "广西", "贵州", "甘肃",
    "山西", "内蒙古", "云南", "四川", "河南", "陕西", "青海", "宁夏",
}

COMPREHENSIVE_PROVINCES = {"北京", "天津", "上海", "浙江", "山东", "海南"}


def cls_category_group(category: str) -> str:
    text = str(category or "")
    if "物理" in text or "理科" in text:
        return "science"
    if "历史" in text or "文科" in text:
        return "liberal"
    if "综合" in text or "普通" in text:
        return "comprehensive"
    return text


@dataclass
class XuefengStatus:
    ready: bool
    db_path: str
    gz_path: str
    source_kind: str = ""
    message: str = ""


class XuefengAdmissionRepository:
    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        self.app_dir = os.path.dirname(self.data_dir)
        self.workspace_dir = os.path.dirname(self.app_dir)
        self.unified_db_path = os.path.join(self.data_dir, "unified_admission.db")
        self.pipeline_unified_db_path = os.path.join(self.workspace_dir, "data-pipeline", "output", "unified_admission.db")
        self.db_path = os.path.join(self.data_dir, "admission_clean.db")
        self.gz_path = os.path.join(self.data_dir, "admission_clean.db.gz")
        self.status = self._prepare()

    def _prepare(self) -> XuefengStatus:
        for path in (self.unified_db_path, self.pipeline_unified_db_path):
            if os.path.exists(path):
                return XuefengStatus(
                    ready=True,
                    db_path=path,
                    gz_path=self.gz_path,
                    source_kind="unified",
                    message="unified_admission.db ready",
                )
        if os.path.exists(self.db_path):
            return XuefengStatus(
                ready=True,
                db_path=self.db_path,
                gz_path=self.gz_path,
                source_kind="legacy_xuefeng",
                message="admission_clean.db ready",
            )
        if not os.path.exists(self.gz_path):
            return XuefengStatus(
                ready=False,
                db_path=self.pipeline_unified_db_path,
                gz_path=self.gz_path,
                message="missing unified_admission.db or admission_clean.db.gz",
            )
        try:
            tmp_path = self.db_path + ".tmp"
            with gzip.open(self.gz_path, "rb") as gz:
                with open(tmp_path, "wb") as f:
                    shutil.copyfileobj(gz, f)
            os.replace(tmp_path, self.db_path)
            return XuefengStatus(
                ready=True,
                db_path=self.db_path,
                gz_path=self.gz_path,
                source_kind="legacy_xuefeng",
                message="decompressed admission_clean.db.gz",
            )
        except Exception as exc:
            try:
                if os.path.exists(self.db_path + ".tmp"):
                    os.remove(self.db_path + ".tmp")
            except OSError:
                pass
            return XuefengStatus(
                ready=False,
                db_path=self.db_path,
                gz_path=self.gz_path,
                message=f"cannot decompress gz: {exc}",
            )

    @property
    def ready(self) -> bool:
        return self.status.ready

    def inspect_schema(self) -> dict[str, Any]:
        if not self.ready:
            return {"ready": False, "message": self.status.message}
        with sqlite3.connect(self.status.db_path) as conn:
            tables = [
                r[0]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ]
            columns = {}
            for table in tables:
                columns[table] = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        return {"ready": True, "tables": tables, "columns": columns}

    def coverage(self) -> dict[str, Any]:
        if not self.ready:
            return {
                "ready": False,
                "message": self.status.message,
                "quality_warnings": [self._coverage_warning()],
            }
        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if self._is_unified:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, MIN(year) AS min_year, MAX(year) AS max_year FROM admission_best_records"
                ).fetchone()
                provinces = conn.execute(
                    """
                    SELECT province, COUNT(*) AS n
                    FROM admission_best_records
                    GROUP BY province
                    ORDER BY n DESC, province ASC
                    """
                ).fetchall()
                years = conn.execute(
                    "SELECT year, COUNT(*) AS n FROM admission_best_records GROUP BY year ORDER BY year"
                ).fetchall()
                source_counts = conn.execute(
                    "SELECT source_type, COUNT(*) AS n FROM admission_best_records GROUP BY source_type ORDER BY n DESC"
                ).fetchall()
                source_counts_payload = [{"source_type": r["source_type"], "records": r["n"]} for r in source_counts]
            else:
                row = conn.execute("SELECT COUNT(*) AS n, MIN(year) AS min_year, MAX(year) AS max_year FROM admission").fetchone()
                provinces = conn.execute(
                    """
                    SELECT province, COUNT(*) AS n
                    FROM admission
                    GROUP BY province
                    ORDER BY n DESC, province ASC
                    """
                ).fetchall()
                years = conn.execute(
                    "SELECT year, COUNT(*) AS n FROM admission GROUP BY year ORDER BY year"
                ).fetchall()
                source_counts_payload = [{"source_type": "legacy_xuefeng", "records": row["n"] if row else 0}]
        return {
            "ready": True,
            "source_kind": self.status.source_kind,
            "record_count": row["n"] if row else 0,
            "year_min": row["min_year"] if row else None,
            "year_max": row["max_year"] if row else None,
            "province_count": len(provinces),
            "top_provinces": [{"province": r["province"], "records": r["n"]} for r in provinces[:8]],
            "years": [{"year": r["year"], "records": r["n"]} for r in years],
            "source_counts": source_counts_payload,
            "quality_warnings": [self._coverage_warning()],
        }

    def recommend(
        self,
        province: str,
        category: str = "",
        education_level: str = "",
        score: int = 0,
        rank: int = 0,
        keywords: list[str] | None = None,
        max_slots: int = 30,
    ) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError(self.status.message)
        keywords = [kw for kw in (keywords or []) if kw]
        education_level = self._normalize_education_level(education_level)
        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            chong, wen, bao = self._query_buckets(conn, province, category, education_level, score, rank, keywords)
            if not (chong or wen or bao) and keywords:
                chong, wen, bao = self._query_buckets(conn, province, category, education_level, score, rank, [])

        selected = self._select(chong, wen, bao, max_slots)
        return {
            "mode": "unified_primary" if self._is_unified else "xuefeng_primary",
            "data_source": self.data_source_meta(),
            "student_rank": rank or None,
            "summary": {
                "total": len(selected),
                "chong": sum(1 for r in selected if r["tag"] == "冲"),
                "wen": sum(1 for r in selected if r["tag"] == "稳"),
                "bao": sum(1 for r in selected if r["tag"] == "保"),
                "coverage": None,
                "note": self._summary_note(),
            },
            "advisor_note": "",
            "advisor_top_majors": [],
            "recommendations": selected,
            "quality_warnings": [self._coverage_warning()],
            "explanation": self._explain(selected, province, category, education_level, score, rank, bool(keywords)),
        }

    def recommend_for_plan(
        self,
        province: str,
        category: str,
        education_level: str,
        equivalent_scores: dict[str, Any],
        rank: int = 0,
        score: int = 0,
        keywords: list[str] | None = None,
        preferred_cities: list[str] | None = None,
        max_slots: int = 80,
    ) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError(self.status.message)
        if not self._is_unified:
            return self.recommend(province, category, education_level, score, rank, keywords, max_slots)

        keywords = [kw for kw in (keywords or []) if kw]
        preferred_cities = [city for city in (preferred_cities or []) if city]
        education_level = self._normalize_education_level(education_level)
        eq_by_year = {
            int(row["year"]): int(row["equivalent_score"])
            for row in equivalent_scores.get("years", [])
            if row.get("year") and row.get("equivalent_score")
        }
        eq_by_year_category = {
            (int(row["year"]), cls_category_group(row.get("category", ""))): int(row["equivalent_score"])
            for row in equivalent_scores.get("years", [])
            if row.get("year") and row.get("equivalent_score")
        }
        if not eq_by_year and score:
            eq_by_year = {0: score}

        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = self._query_plan_rows(
                conn, province, category, education_level, eq_by_year,
                rank, keywords, max_slots * 40,
            )
            if keywords and len(rows) < max_slots * 2:
                broadened = self._query_plan_rows(
                    conn, province, category, education_level, eq_by_year,
                    rank, [], max_slots * 40,
                )
                seen = {(row["school_name"], row["major_name"], row["year"]) for row in rows}
                rows.extend(
                    row for row in broadened
                    if (row["school_name"], row["major_name"], row["year"]) not in seen
                )

        candidates = [
            self._plan_candidate(row, eq_by_year, rank, preferred_cities, keywords, eq_by_year_category)
            for row in rows
        ]
        candidates = [item for item in candidates if item.get("risk_bucket") in {"冲", "稳", "保"}]
        candidates.sort(key=lambda item: (
            {"稳": 0, "保": 1, "冲": 2}.get(item["risk_bucket"], 9),
            -item["plan_score"],
            item.get("risk_abs", 999999),
            -int(item.get("source_priority") or 0),
        ))
        selected = self._balanced_plan_select(candidates, max_slots)
        return {
            "mode": "six_step_plan_unified",
            "data_source": self.data_source_meta(),
            "student_rank": rank or None,
            "summary": {
                "total": len(selected),
                "chong": sum(1 for r in selected if r["tag"] == "冲"),
                "wen": sum(1 for r in selected if r["tag"] == "稳"),
                "bao": sum(1 for r in selected if r["tag"] == "保"),
                "candidate_pool_before_select": len(candidates),
                "note": "使用等位分窗口和历史录取位次/分数差距生成候选池。",
            },
            "advisor_note": "",
            "advisor_top_majors": [],
            "recommendations": selected,
            "quality_warnings": [self._coverage_warning()],
            "explanation": self._plan_explain(selected, province, category, education_level, rank, score, bool(keywords)),
        }

    def _query_plan_rows(
        self,
        conn: sqlite3.Connection,
        province: str,
        category: str,
        education_level: str,
        eq_by_year: dict[int, int],
        rank: int,
        keywords: list[str],
        limit: int,
    ) -> list[sqlite3.Row]:
        base = "b.province LIKE ? AND (b.score_reliable = 1 OR b.rank_reliable = 1)"
        params: list[Any] = [f"%{province}%"]
        category_sql, category_params = self._unified_category_clause(province, category)
        if category_sql:
            base += category_sql
            params.extend(category_params)
        if education_level:
            base += " AND b.education_level = ?"
            params.append(education_level)
        if eq_by_year and 0 not in eq_by_year:
            placeholders = ",".join("?" for _ in eq_by_year)
            base += f" AND b.year IN ({placeholders})"
            params.extend(sorted(eq_by_year))
        if keywords:
            clauses = []
            for kw in keywords:
                clauses.append("(b.major_name LIKE ? OR b.school_name LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%"])
            base += " AND (" + " OR ".join(clauses) + ")"

        score_values = list(eq_by_year.values())
        score_low = max(0, min(score_values) - 60) if score_values else 0
        score_high = max(score_values) + 35 if score_values else 0
        score_mid = int(sum(score_values) / len(score_values)) if score_values else 0
        windows = []
        window_params: list[Any] = []
        if score_values:
            windows.append("(b.score_reliable = 1 AND b.score BETWEEN ? AND ?)")
            window_params.extend([score_low, score_high])
        if rank:
            windows.append("(b.rank_reliable = 1 AND b.rank BETWEEN ? AND ?)")
            window_params.extend([max(1, int(rank * 0.70)), int(rank * 1.90)])
        if not windows:
            return []
        base += " AND (" + " OR ".join(windows) + ")"
        params.extend(window_params)

        return conn.execute(
            f"""
            SELECT
                b.school_name, b.major_name, b.score, b.rank, b.year,
                b.province, b.category, b.batch, b.education_level,
                b.source_type, b.source_trust_level, b.source_file,
                b.quality_flags, b.source_priority,
                p.city, p.province AS school_province,
                p.school_level, p.school_type, p.school_nature
            FROM admission_best_records b
            LEFT JOIN school_profiles p ON p.school_key = b.school_key
            WHERE {base}
            ORDER BY b.year DESC,
                     ABS(COALESCE(b.score, ?) - ?) ASC,
                     b.source_priority DESC,
                     CASE WHEN b.rank_reliable = 1 THEN 0 ELSE 1 END,
                     b.rank ASC, b.score DESC
            LIMIT ?
            """,
            params + [score_mid, score_mid, limit],
        ).fetchall()

    @classmethod
    def _plan_candidate(
        cls,
        row: sqlite3.Row,
        eq_by_year: dict[int, int],
        student_rank: int,
        preferred_cities: list[str],
        keywords: list[str],
        eq_by_year_category: dict[tuple[int, str], int] | None = None,
    ) -> dict[str, Any]:
        year = int(row["year"] or 0)
        category_group = cls_category_group(row["category"])
        equivalent_score = (
            (eq_by_year_category or {}).get((year, category_group))
            or eq_by_year.get(year)
            or (next(iter(eq_by_year.values())) if eq_by_year else 0)
        )
        admission_score = int(row["score"] or 0)
        admission_rank = int(row["rank"] or 0)
        risk_bucket, risk_gap, risk_abs = cls._risk_bucket(student_rank, admission_rank, equivalent_score, admission_score)
        item = {
            "school_name": row["school_name"],
            "sp_name": row["major_name"],
            "score": admission_score,
            "rank_value": admission_rank or None,
            "year": year,
            "province": row["province"],
            "category": row["category"],
            "batch": row["batch"],
            "education_level": row["education_level"],
            "source_type": row["source_type"],
            "source_trust_level": row["source_trust_level"],
            "source_file": row["source_file"],
            "source_priority": row["source_priority"],
            "quality_flags": row["quality_flags"],
            "city": row["city"] or row["school_province"] or "",
            "tier": cls._tier_from_profile(row["school_level"], row["school_type"], row["school_nature"]),
            "tag": risk_bucket,
            "risk_bucket": risk_bucket,
            "risk_gap": risk_gap,
            "risk_abs": risk_abs,
            "equivalent_score": equivalent_score,
            "score_gap": equivalent_score - admission_score if admission_score else None,
            "rank_gap": admission_rank - student_rank if student_rank and admission_rank else None,
        }
        item["fit_score"] = cls._fit_score(item, preferred_cities, keywords)
        item["plan_score"] = cls._plan_score(item)
        item["rank"] = 0
        item["p"] = None
        item["p_pct"] = "等位分区间"
        item["utility"] = item["plan_score"]
        item["major_match"] = True
        item["note"] = cls._plan_row_note(item)
        item["source"] = cls._source_label(item)
        item["sources"] = [item["source"]]
        item["source_year"] = item["year"]
        item["source_score"] = item["score"]
        item["source_rank"] = item["rank_value"]
        item["confidence"] = cls._plan_confidence(item)
        item["evidence"] = {
            "source": item["source"],
            "year": item["source_year"],
            "score": item["source_score"],
            "rank": item["source_rank"],
            "equivalent_score": item["equivalent_score"],
            "score_gap": item["score_gap"],
            "rank_gap": item["rank_gap"],
            "confidence": item["confidence"],
            "province": item.get("province", ""),
            "category": item.get("category", ""),
            "batch": item.get("batch", ""),
            "quality_flags": cls._parse_flags(item.get("quality_flags")),
        }
        return item

    @staticmethod
    def _risk_bucket(student_rank: int, admission_rank: int, equivalent_score: int, admission_score: int) -> tuple[str, float, float]:
        if student_rank and admission_rank:
            ratio = (admission_rank - student_rank) / max(1, student_rank)
            if -0.25 <= ratio < 0:
                return "冲", ratio, abs(ratio)
            if 0 <= ratio <= 0.35:
                return "稳", ratio, abs(ratio)
            if 0.35 < ratio <= 0.90:
                return "保", ratio, abs(ratio)
            return "其他", ratio, abs(ratio)
        if equivalent_score and admission_score:
            gap = equivalent_score - admission_score
            if -20 <= gap < 0:
                return "冲", float(gap), abs(float(gap))
            if 0 <= gap <= 30:
                return "稳", float(gap), abs(float(gap))
            if 30 < gap <= 70:
                return "保", float(gap), abs(float(gap))
            return "其他", float(gap), abs(float(gap))
        return "其他", 999999.0, 999999.0

    @staticmethod
    def _fit_score(item: dict[str, Any], preferred_cities: list[str], keywords: list[str]) -> float:
        score = 0.0
        city = str(item.get("city") or "")
        major = str(item.get("sp_name") or "")
        tier = str(item.get("tier") or "")
        if preferred_cities and any(city_name in city for city_name in preferred_cities):
            score += 18
        if keywords and any(kw in major or kw in item.get("school_name", "") for kw in keywords):
            score += 22
        if any(label in tier for label in ("985", "211", "双一流")):
            score += 10
        if item.get("source_type") == "official":
            score += 8
        elif item.get("source_type") == "aggregate":
            score += 4
        return score

    @staticmethod
    def _plan_score(item: dict[str, Any]) -> float:
        bucket_base = {"稳": 70, "保": 62, "冲": 54}.get(item.get("risk_bucket"), 0)
        priority = min(10, int(item.get("source_priority") or 0) / 10)
        recency = max(0, int(item.get("year") or 0) - 2020)
        return bucket_base + float(item.get("fit_score") or 0) + priority + recency

    @staticmethod
    def _balanced_plan_select(candidates: list[dict[str, Any]], max_slots: int) -> list[dict[str, Any]]:
        targets = {"冲": max(1, round(max_slots * 0.22)), "稳": max(1, round(max_slots * 0.50))}
        targets["保"] = max(1, max_slots - targets["冲"] - targets["稳"])
        selected: list[dict[str, Any]] = []
        school_counts: dict[str, int] = {}
        for tag in ("冲", "稳", "保"):
            bucket = [item for item in candidates if item["risk_bucket"] == tag]
            bucket.sort(key=lambda item: (-item["plan_score"], item["risk_abs"], -int(item.get("source_priority") or 0)))
            for item in bucket:
                if sum(1 for row in selected if row["tag"] == tag) >= targets[tag]:
                    break
                school = item.get("school_name") or ""
                if school_counts.get(school, 0) >= 2:
                    continue
                school_counts[school] = school_counts.get(school, 0) + 1
                item = dict(item)
                item["rank"] = len(selected) + 1
                selected.append(item)
                if len(selected) >= max_slots:
                    return selected
        return selected

    @staticmethod
    def _plan_confidence(item: dict[str, Any]) -> str:
        if item.get("source_type") == "official" and item.get("rank_value"):
            return "high"
        if item.get("source_type") in {"official", "aggregate"}:
            return "medium"
        return {"稳": "medium", "保": "medium", "冲": "low"}.get(item.get("tag"), "unknown")

    @staticmethod
    def _plan_row_note(item: dict[str, Any]) -> str:
        parts = [f"{item.get('year') or '-'} 年历史录取"]
        if item.get("batch"):
            parts.append(str(item["batch"]))
        if item.get("risk_bucket"):
            parts.append(f"{item['risk_bucket']}档")
        if item.get("rank_gap") is not None:
            parts.append(f"位次差 {item['rank_gap']}")
        elif item.get("score_gap") is not None:
            parts.append(f"分差 {item['score_gap']}")
        return " · ".join(parts)

    @staticmethod
    def _plan_explain(
        selected: list[dict[str, Any]],
        province: str,
        category: str,
        education_level: str,
        rank: int,
        score: int,
        used_keywords: bool,
    ) -> str:
        if not selected:
            return "等位分已计算，但统一录取库中没有找到落在风险区间内的候选。可以放宽专业/城市偏好或扩大批次。"
        parts = [f"已按等位分和历史录取位次生成 {len(selected)} 个候选。", f"省份：{province}。"]
        if education_level:
            parts.append(f"层次：{education_level}。")
        if category:
            parts.append(f"科类：{category}。")
        if rank:
            parts.append(f"参考位次：{rank}。")
        elif score:
            parts.append(f"参考分数：{score}。")
        if used_keywords:
            parts.append("已叠加专业关键词偏好。")
        return "".join(parts)

    def _query_buckets(
        self,
        conn: sqlite3.Connection,
        province: str,
        category: str,
        education_level: str,
        score: int,
        rank: int,
        keywords: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if self._is_unified:
            return self._query_unified_buckets(conn, province, category, education_level, score, rank, keywords)
        return self._query_legacy_buckets(conn, province, category, education_level, score, rank, keywords)

    def _query_legacy_buckets(
        self,
        conn: sqlite3.Connection,
        province: str,
        category: str,
        education_level: str,
        score: int,
        rank: int,
        keywords: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        base = "province LIKE ? AND (score > 0 OR rank > 0)"
        params: list[Any] = [f"%{province}%"]
        if category:
            category_sql, category_params = self._legacy_category_clause(province, category)
            base += category_sql
            params.extend(category_params)
        if education_level:
            if education_level == "本科":
                base += " AND (batch LIKE ? OR batch LIKE ? OR batch LIKE ?)"
                params.extend(["%本科%", "%本一%", "%本二%"])
            elif education_level == "专科":
                base += " AND (batch LIKE ? OR batch LIKE ?)"
                params.extend(["%专科%", "%高职%"])
        if keywords:
            kw_sql = []
            for kw in keywords:
                kw_sql.append("(major_name LIKE ? OR school_name LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%"])
            base += " AND (" + " OR ".join(kw_sql) + ")"

        if rank > 0:
            return (
                self._rows(conn, base + " AND rank > 0 AND rank < ? AND rank >= ? ORDER BY rank ASC LIMIT 80",
                           params + [rank, max(1, int(rank * 0.85))], "冲"),
                self._rows(conn, base + " AND rank > 0 AND rank >= ? AND rank <= ? ORDER BY rank ASC LIMIT 80",
                           params + [rank, int(rank * 1.30)], "稳"),
                self._rows(conn, base + " AND rank > 0 AND rank > ? AND rank <= ? ORDER BY rank ASC LIMIT 80",
                           params + [int(rank * 1.30), int(rank * 1.60)], "保"),
            )

        if score > 0:
            return (
                self._rows(conn, base + " AND score > ? AND score <= ? ORDER BY score DESC LIMIT 80",
                           params + [score, score + 35], "冲"),
                self._rows(conn, base + " AND score >= ? AND score <= ? ORDER BY score ASC LIMIT 80",
                           params + [score - 25, score + 35], "稳"),
                self._rows(conn, base + " AND score >= ? AND score < ? ORDER BY score ASC LIMIT 80",
                           params + [score - 50, score - 25], "保"),
            )

        return [], [], []

    def _query_unified_buckets(
        self,
        conn: sqlite3.Connection,
        province: str,
        category: str,
        education_level: str,
        score: int,
        rank: int,
        keywords: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        base = "b.province LIKE ? AND (b.score_reliable = 1 OR b.rank_reliable = 1)"
        params: list[Any] = [f"%{province}%"]
        if category:
            category_sql, category_params = self._unified_category_clause(province, category)
            base += category_sql
            params.extend(category_params)
        if education_level:
            base += " AND b.education_level = ?"
            params.append(education_level)
        if keywords:
            kw_sql = []
            for kw in keywords:
                kw_sql.append("(b.major_name LIKE ? OR b.school_name LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%"])
            base += " AND (" + " OR ".join(kw_sql) + ")"

        if rank > 0:
            return (
                self._unified_rows(conn, base + " AND b.rank_reliable = 1 AND b.rank > 0 AND b.rank < ? AND b.rank >= ? ORDER BY b.source_priority DESC, b.rank ASC LIMIT 100",
                                   params + [rank, max(1, int(rank * 0.85))], "冲"),
                self._unified_rows(conn, base + " AND b.rank_reliable = 1 AND b.rank > 0 AND b.rank >= ? AND b.rank <= ? ORDER BY b.source_priority DESC, b.rank ASC LIMIT 120",
                                   params + [rank, int(rank * 1.30)], "稳"),
                self._unified_rows(conn, base + " AND b.rank_reliable = 1 AND b.rank > 0 AND b.rank > ? AND b.rank <= ? ORDER BY b.source_priority DESC, b.rank ASC LIMIT 120",
                                   params + [int(rank * 1.30), int(rank * 1.80)], "保"),
            )

        if score > 0:
            return (
                self._unified_rows(conn, base + " AND b.score_reliable = 1 AND b.score > ? AND b.score <= ? ORDER BY b.source_priority DESC, b.score DESC LIMIT 100",
                                   params + [score, score + 35], "冲"),
                self._unified_rows(conn, base + " AND b.score_reliable = 1 AND b.score >= ? AND b.score <= ? ORDER BY b.source_priority DESC, b.score ASC LIMIT 120",
                                   params + [score - 25, score + 35], "稳"),
                self._unified_rows(conn, base + " AND b.score_reliable = 1 AND b.score >= ? AND b.score < ? ORDER BY b.source_priority DESC, b.score ASC LIMIT 120",
                                   params + [score - 60, score - 25], "保"),
            )

        return [], [], []

    @staticmethod
    def _rows(conn: sqlite3.Connection, where_and_order: str, params: list[Any], tag: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT school_name, major_name, score, rank, year, province, category, source_file
            FROM admission
            WHERE {where_and_order}
            """,
            params,
        ).fetchall()
        return [
            {
                "school_name": r["school_name"],
                "sp_name": r["major_name"],
                "score": r["score"],
                "rank_value": r["rank"],
                "year": r["year"],
                "province": r["province"],
                "category": r["category"],
                "source_file": r["source_file"],
                "tag": tag,
            }
            for r in rows
        ]

    @staticmethod
    def _unified_rows(conn: sqlite3.Connection, where_and_order: str, params: list[Any], tag: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT
                b.school_name,
                b.major_name,
                b.score,
                b.rank,
                b.year,
                b.province,
                b.category,
                b.batch,
                b.education_level,
                b.source_type,
                b.source_trust_level,
                b.source_file,
                b.quality_flags,
                p.city,
                p.province AS school_province,
                p.school_level,
                p.school_type,
                p.school_nature
            FROM admission_best_records b
            LEFT JOIN school_profiles p ON p.school_key = b.school_key
            WHERE {where_and_order}
            """,
            params,
        ).fetchall()
        return [
            {
                "school_name": r["school_name"],
                "sp_name": r["major_name"],
                "score": r["score"],
                "rank_value": r["rank"],
                "year": r["year"],
                "province": r["province"],
                "category": r["category"],
                "batch": r["batch"],
                "education_level": r["education_level"],
                "source_type": r["source_type"],
                "source_trust_level": r["source_trust_level"],
                "source_file": r["source_file"],
                "quality_flags": r["quality_flags"],
                "city": r["city"] or r["school_province"] or "",
                "tier": XuefengAdmissionRepository._tier_from_profile(r["school_level"], r["school_type"], r["school_nature"]),
                "tag": tag,
            }
            for r in rows
        ]

    @staticmethod
    def _select(chong: list[dict[str, Any]], wen: list[dict[str, Any]], bao: list[dict[str, Any]], max_slots: int) -> list[dict[str, Any]]:
        targets = {"冲": max(1, round(max_slots * 0.20)), "稳": max(1, round(max_slots * 0.50))}
        targets["保"] = max(1, max_slots - targets["冲"] - targets["稳"])
        buckets = {"冲": chong, "稳": wen, "保": bao}
        selected: list[dict[str, Any]] = []
        school_counts: dict[str, int] = {}

        for tag in ("冲", "稳", "保"):
            for row in buckets[tag]:
                if sum(1 for item in selected if item["tag"] == tag) >= targets[tag]:
                    break
                school = row["school_name"]
                if school_counts.get(school, 0) >= 2:
                    continue
                school_counts[school] = school_counts.get(school, 0) + 1
                item = dict(row)
                item["rank"] = len(selected) + 1
                item["city"] = item.get("city", "")
                item["tier"] = item.get("tier", "")
                item["p"] = None
                item["p_pct"] = "历史区间"
                item["utility"] = None
                item["major_match"] = True
                item["note"] = XuefengAdmissionRepository._row_note(item)
                item["source"] = XuefengAdmissionRepository._source_label(item)
                item["sources"] = [item["source"]]
                item["source_year"] = item["year"]
                item["source_score"] = item["score"]
                item["source_rank"] = item["rank_value"]
                item["confidence"] = XuefengAdmissionRepository._confidence_for_tag(item["tag"], item.get("source_type"))
                item["evidence"] = {
                    "source": item["source"],
                    "year": item["source_year"],
                    "score": item["source_score"],
                    "rank": item["source_rank"],
                    "confidence": item["confidence"],
                    "province": item.get("province", ""),
                    "category": item.get("category", ""),
                    "batch": item.get("batch", ""),
                    "quality_flags": XuefengAdmissionRepository._parse_flags(item.get("quality_flags")),
                }
                selected.append(item)
                if len(selected) >= max_slots:
                    return selected
        return selected

    @staticmethod
    def _explain(
        selected: list[dict[str, Any]],
        province: str,
        category: str,
        education_level: str,
        score: int,
        rank: int,
        used_keywords: bool,
    ) -> str:
        if not selected:
            return "没有在本地录取库中找到匹配结果。可以放宽专业关键词，或补充位次后重试。"
        parts = [
            f"已基于本地统一录取库生成 {len(selected)} 个候选。",
            f"省份：{province}。",
        ]
        if education_level:
            parts.append(f"层次：{education_level}。")
        if category:
            parts.append(f"科类：{category}。")
        if rank:
            parts.append(f"参考位次：{rank}。")
        elif score:
            parts.append(f"参考分数：{score}。")
        if used_keywords:
            parts.append("已按专业关键词优先筛选。")
        parts.append("当前为历史区间冲稳保，后续接入 gaokao.db 后可升级为概率模型。")
        return "".join(parts)

    def data_source_meta(self) -> dict[str, Any]:
        coverage = self.coverage()
        is_unified = self._is_unified
        return {
            "id": "unified_admission" if is_unified else "xuefeng_admission_clean",
            "name": "统一录取库 unified_admission.db" if is_unified else "xuefeng-agent admission_clean.db",
            "role": "primary",
            "ready": self.ready,
            "db_path": self.status.db_path,
            "source_kind": self.status.source_kind,
            "message": self.status.message,
            "coverage": coverage,
        }

    @staticmethod
    def _confidence_for_tag(tag: str, source_type: str | None = None) -> str:
        if source_type == "official" and tag in {"稳", "保"}:
            return "high"
        if source_type == "official":
            return "medium"
        if source_type == "aggregate" and tag in {"稳", "保"}:
            return "medium"
        return {
            "冲": "low",
            "稳": "medium",
            "保": "medium",
        }.get(tag, "unknown")

    @property
    def _is_unified(self) -> bool:
        return self.status.source_kind == "unified"

    def _coverage_warning(self) -> str:
        if self._is_unified:
            return "当前主数据源是 unified_admission.db：官方数据优先，掌上高考和开源快照作为补充；第三方/低信任记录已保留质量标记。"
        return "当前主数据源是 xuefeng-agent admission_clean.db，本地样本主要覆盖部分省份和 2024-2025 年历史录取记录；这不是全国完整录取库。"

    def _summary_note(self) -> str:
        if self._is_unified:
            return "使用统一录取库 admission_best_records，按历史分数/位次区间生成冲稳保；官方数据优先，第三方和开源数据作为补充。"
        return "使用 xuefeng-agent admission_clean.db，按历史分数/位次区间生成冲稳保；高级概率模型为可选能力，需要 gaokao.db。"

    @staticmethod
    def _normalize_education_level(value: str) -> str:
        text = (value or "").strip()
        if text in {"本科", "本", "undergraduate"}:
            return "本科"
        if text in {"专科", "高职", "高职专科", "vocational"}:
            return "专科"
        return ""

    @staticmethod
    def _category_patterns(province: str, value: str) -> list[str]:
        text = (value or "").strip()
        if not text:
            return []
        if province in COMPREHENSIVE_PROVINCES:
            return ["综合", "普通"]
        if text in {"综合", "普通类", "综合/普通类", "不分文理", "不限", "全部", "文理综合"}:
            return []
        if text in {"理科", "理科大类", "学理科", "物理", "物理类"}:
            if province in PHYSICS_HISTORY_PROVINCES:
                return ["物理"]
            return ["理科"]
        if text in {"文科", "文科大类", "学文科", "历史", "历史类"}:
            if province in PHYSICS_HISTORY_PROVINCES:
                return ["历史"]
            return ["文科"]
        return [text]

    @classmethod
    def _legacy_category_clause(cls, province: str, category: str) -> tuple[str, list[str]]:
        patterns = cls._category_patterns(province, category)
        if not patterns:
            return "", []
        clauses = []
        params: list[str] = []
        for pattern in patterns:
            clauses.append("category LIKE ?")
            params.append(f"%{pattern}%")
        return " AND (" + " OR ".join(clauses) + ")", params

    @classmethod
    def _unified_category_clause(cls, province: str, category: str) -> tuple[str, list[str]]:
        patterns = cls._category_patterns(province, category)
        if not patterns:
            return "", []
        clauses = []
        params: list[str] = []
        for pattern in patterns:
            clauses.append("b.category LIKE ?")
            params.append(f"%{pattern}%")
        return " AND (" + " OR ".join(clauses) + ")", params

    @staticmethod
    def _source_label(item: dict[str, Any]) -> str:
        source_type = item.get("source_type")
        if source_type == "official":
            return "官方考试院数据 / unified_admission.db"
        if source_type == "aggregate":
            return "掌上高考第三方聚合数据 / unified_admission.db"
        if source_type == "open_source":
            return "开源快照清洗数据 / unified_admission.db"
        return "xuefeng-agent admission_clean.db"

    @staticmethod
    def _row_note(item: dict[str, Any]) -> str:
        parts = [f"{item.get('year') or '-'} 年历史投档"]
        if item.get("batch"):
            parts.append(str(item["batch"]))
        if item.get("education_level"):
            parts.append(str(item["education_level"]))
        score = item.get("score")
        rank = item.get("rank_value")
        value = f"{score if score is not None else '-'} 分 / {rank if rank is not None else '-'} 位"
        return " · ".join(parts) + f"：{value}"

    @staticmethod
    def _tier_from_profile(school_level: str | None, school_type: str | None, school_nature: str | None) -> str:
        parts = [p for p in (school_level, school_type, school_nature) if p]
        return " / ".join(parts)

    @staticmethod
    def _parse_flags(value: Any) -> list[str]:
        if not value:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        try:
            import json

            parsed = json.loads(str(value))
        except Exception:
            return [str(value)]
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
        return []
