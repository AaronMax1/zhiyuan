"""Adapter for the local admissions database.

The production MVP is scoped to Hebei candidates. The primary source is the
separate Hebei exam-authority historical admissions database:
data-pipeline/output/hebei_lnwc_loggedin.db.
"""

from __future__ import annotations

import gzip
import json
import os
import re
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
        self.hebei_lnwc_db_path = os.path.join(self.data_dir, "hebei_lnwc_loggedin.db")
        self.pipeline_hebei_lnwc_db_path = os.path.join(self.workspace_dir, "data-pipeline", "output", "hebei_lnwc_loggedin.db")
        self.hebei_plan_db_path = os.path.join(self.data_dir, "hebei_2026_plan.db")
        self.pipeline_hebei_plan_db_path = os.path.join(self.workspace_dir, "data-pipeline", "output", "hebei_2026_plan.db")
        self.unified_db_path = os.path.join(self.data_dir, "unified_admission.db")
        self.pipeline_unified_db_path = os.path.join(self.workspace_dir, "data-pipeline", "output", "unified_admission.db")
        self.db_path = os.path.join(self.data_dir, "admission_clean.db")
        self.gz_path = os.path.join(self.data_dir, "admission_clean.db.gz")
        self.status = self._prepare()

    def _prepare(self) -> XuefengStatus:
        for path in (self.hebei_lnwc_db_path, self.pipeline_hebei_lnwc_db_path):
            if os.path.exists(path):
                return XuefengStatus(
                    ready=True,
                    db_path=path,
                    gz_path=self.gz_path,
                    source_kind="hebei_lnwc",
                    message="hebei_lnwc_loggedin.db ready",
                )
        for path in (self.unified_db_path, self.pipeline_unified_db_path):
            if os.path.exists(path):
                return XuefengStatus(
                    ready=False,
                    db_path=path,
                    gz_path=self.gz_path,
                    source_kind="unified",
                    message="河北专项模式需要 hebei_lnwc_loggedin.db；unified_admission.db 不作为默认主数据源",
                )
        if os.path.exists(self.db_path):
            return XuefengStatus(
                ready=False,
                db_path=self.db_path,
                gz_path=self.gz_path,
                source_kind="legacy_xuefeng",
                message="河北专项模式需要 hebei_lnwc_loggedin.db；admission_clean.db 不作为默认主数据源",
            )
        if not os.path.exists(self.gz_path):
            return XuefengStatus(
                ready=False,
                db_path=self.pipeline_hebei_lnwc_db_path,
                gz_path=self.gz_path,
                message="missing hebei_lnwc_loggedin.db",
            )
        try:
            tmp_path = self.db_path + ".tmp"
            with gzip.open(self.gz_path, "rb") as gz:
                with open(tmp_path, "wb") as f:
                    shutil.copyfileobj(gz, f)
            os.replace(tmp_path, self.db_path)
            return XuefengStatus(
                ready=False,
                db_path=self.db_path,
                gz_path=self.gz_path,
                source_kind="legacy_xuefeng",
                message="decompressed admission_clean.db.gz, but Hebei scoped mode requires hebei_lnwc_loggedin.db",
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
            if self._is_hebei_lnwc:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, MIN(year) AS min_year, MAX(year) AS max_year FROM hebei_lnwc_loggedin"
                ).fetchone()
                province_rows = [{"province": "河北", "records": row["n"] if row else 0}]
                years = conn.execute(
                    "SELECT year, COUNT(*) AS n FROM hebei_lnwc_loggedin GROUP BY year ORDER BY year"
                ).fetchall()
                source_counts_payload = [{"source_type": "hebei_exam_authority_lnwc", "records": row["n"] if row else 0}]
                level_rows = conn.execute(
                    """
                    SELECT batch_name, category_name, COUNT(*) AS n
                    FROM hebei_lnwc_loggedin
                    GROUP BY batch_name, category_name
                    ORDER BY batch_name, category_name
                    """
                ).fetchall()
                return {
                    "ready": True,
                    "source_kind": self.status.source_kind,
                    "record_count": row["n"] if row else 0,
                    "year_min": row["min_year"] if row else None,
                    "year_max": row["max_year"] if row else None,
                    "province_count": 1,
                    "top_provinces": province_rows,
                    "years": [{"year": r["year"], "records": r["n"]} for r in years],
                    "source_counts": source_counts_payload,
                    "batch_category_counts": [
                        {"batch": r["batch_name"], "category": r["category_name"], "records": r["n"]}
                        for r in level_rows
                    ],
                    "quality_warnings": [self._coverage_warning()],
                }
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
        raw_keywords = [kw for kw in (keywords or []) if kw]
        keywords = self._normalize_major_keywords(category, raw_keywords)
        education_level = self._normalize_education_level(education_level)
        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            chong, wen, bao = self._query_buckets(conn, province, category, education_level, score, rank, keywords)
            if not (chong or wen or bao) and keywords:
                chong, wen, bao = self._query_buckets(conn, province, category, education_level, score, rank, [])

        selected = self._select(chong, wen, bao, max_slots)
        if self._is_hebei_lnwc:
            self._attach_hebei_stability(selected)
            self._attach_hebei_plan_info(selected)
        return {
            "mode": "hebei_lnwc_primary" if self._is_hebei_lnwc else ("unified_primary" if self._is_unified else "xuefeng_primary"),
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
        constraints: str = "",
        budget: str = "",
        max_slots: int = 80,
    ) -> dict[str, Any]:
        if not self.ready:
            raise RuntimeError(self.status.message)
        if self._is_hebei_lnwc:
            return self._recommend_hebei_for_plan(
                province=province,
                category=category,
                education_level=education_level,
                equivalent_scores=equivalent_scores,
                rank=rank,
                score=score,
                keywords=keywords,
                preferred_cities=preferred_cities,
                constraints=constraints,
                budget=budget,
                max_slots=max_slots,
            )
        if not self._is_unified:
            return self.recommend(province, category, education_level, score, rank, keywords, max_slots)

        raw_keywords = [kw for kw in (keywords or []) if kw]
        keywords = self._normalize_major_keywords(category, raw_keywords)
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
        self._attach_hebei_stability(selected)
        self._attach_hebei_plan_info(selected)
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
        plan_bonus = 0
        status = str(item.get("plan_match_status") or "")
        if status == "official_matched":
            plan_bonus += 6
        elif status.startswith("official_matched"):
            plan_bonus += 4
        if item.get("plan_count"):
            plan_bonus += min(4, int(item.get("plan_count") or 0))
        return bucket_base + float(item.get("fit_score") or 0) + priority + recency + plan_bonus

    @classmethod
    def _apply_hebei_hard_filters(
        cls,
        candidates: list[dict[str, Any]],
        constraints: str,
        budget: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rules = cls._parse_constraint_rules(constraints, budget)
        kept: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        reason_counts: dict[str, int] = {}
        for item in candidates:
            reasons = cls._hard_filter_reasons(item, rules)
            item["hard_filter_reasons"] = reasons
            if reasons:
                removed.append({
                    "school_name": item.get("school_name", ""),
                    "major_name": item.get("sp_name") or item.get("major_name") or "",
                    "tag": item.get("tag", ""),
                    "plan_count": item.get("plan_count"),
                    "tuition_text": item.get("tuition_text"),
                    "subject_requirement": item.get("subject_requirement"),
                    "reasons": reasons,
                })
                for reason in reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
                continue
            item["hard_filter_reasons"] = []
            kept.append(item)
        return kept, {
            "enabled": bool(rules["reject_private"] or rules["reject_coop"] or rules["tuition_budget"] or rules["body_constraints"]),
            "tuition_budget": rules["tuition_budget"],
            "reject_private": rules["reject_private"],
            "reject_coop": rules["reject_coop"],
            "body_constraints": rules["body_constraints"],
            "before": len(candidates),
            "after": len(kept),
            "removed_count": len(removed),
            "reason_counts": reason_counts,
            "removed_samples": removed[:20],
        }

    @staticmethod
    def _parse_constraint_rules(constraints: str, budget: str) -> dict[str, Any]:
        text = f"{constraints or ''} {budget or ''}"
        reject_private = any(word in text for word in ("不接受民办", "不要民办", "不考虑民办", "只要公办"))
        reject_coop = any(word in text for word in ("不接受中外", "不要中外", "不考虑中外", "不要合作办学", "不接受合作办学"))
        body_constraints = [word for word in ("色弱", "色盲", "近视", "视力", "身高", "口吃") if word in text]
        tuition_budget = 0
        for match in re.finditer(r"(\d{4,6})\s*(?:元|以内|以下|内|预算|学费)?", text):
            value = int(match.group(1))
            if 1000 <= value <= 100000:
                tuition_budget = value if not tuition_budget else min(tuition_budget, value)
        return {
            "reject_private": reject_private,
            "reject_coop": reject_coop,
            "body_constraints": body_constraints,
            "tuition_budget": tuition_budget,
        }

    @classmethod
    def _hard_filter_reasons(cls, item: dict[str, Any], rules: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        school = str(item.get("school_name") or "")
        major = str(item.get("sp_name") or item.get("major_name") or "")
        remarks = str(item.get("plan_remarks") or "")
        combined = f"{school} {major} {remarks}"
        if rules["reject_private"] and ("[民办]" in school or "民办" in combined):
            reasons.append("不接受民办")
        if rules["reject_coop"] and any(word in combined for word in ("中外合作", "合作办学", "国际本科", "中美", "中英", "中澳", "中加")):
            reasons.append("不接受中外合作")
        tuition_budget = int(rules.get("tuition_budget") or 0)
        tuition = int(item.get("tuition") or 0)
        if tuition_budget and tuition and tuition > tuition_budget:
            reasons.append("学费超预算")
        for body_word in rules.get("body_constraints") or []:
            if body_word in remarks or body_word in major:
                reasons.append(f"身体条件风险：{body_word}")
        return reasons

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
        if self._is_hebei_lnwc:
            return self._query_hebei_buckets(conn, province, category, education_level, score, rank, keywords)
        if self._is_unified:
            return self._query_unified_buckets(conn, province, category, education_level, score, rank, keywords)
        return self._query_legacy_buckets(conn, province, category, education_level, score, rank, keywords)

    def _query_hebei_buckets(
        self,
        conn: sqlite3.Connection,
        province: str,
        category: str,
        education_level: str,
        score: int,
        rank: int,
        keywords: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        if province and province != "河北":
            return [], [], []
        base, params = self._hebei_base_filter(category, education_level, keywords)
        if rank > 0:
            return (
                self._hebei_rows(conn, base + " AND min_rank > 0 AND min_rank < ? AND min_rank >= ? ORDER BY year DESC, min_rank ASC LIMIT 100",
                                 params + [rank, max(1, int(rank * 0.75))], "冲"),
                self._hebei_rows(conn, base + " AND min_rank > 0 AND min_rank >= ? AND min_rank <= ? ORDER BY year DESC, min_rank ASC LIMIT 140",
                                 params + [rank, int(rank * 1.35)], "稳"),
                self._hebei_rows(conn, base + " AND min_rank > 0 AND min_rank > ? AND min_rank <= ? ORDER BY year DESC, min_rank ASC LIMIT 140",
                                 params + [int(rank * 1.35), int(rank * 1.90)], "保"),
            )
        if score > 0:
            return (
                self._hebei_rows(conn, base + " AND min_score > ? AND min_score <= ? ORDER BY year DESC, min_score DESC LIMIT 100",
                                 params + [score, score + 35], "冲"),
                self._hebei_rows(conn, base + " AND min_score >= ? AND min_score <= ? ORDER BY year DESC, min_score ASC LIMIT 140",
                                 params + [score - 25, score + 35], "稳"),
                self._hebei_rows(conn, base + " AND min_score >= ? AND min_score < ? ORDER BY year DESC, min_score ASC LIMIT 140",
                                 params + [score - 60, score - 25], "保"),
            )
        return [], [], []

    def _recommend_hebei_for_plan(
        self,
        province: str,
        category: str,
        education_level: str,
        equivalent_scores: dict[str, Any],
        rank: int = 0,
        score: int = 0,
        keywords: list[str] | None = None,
        preferred_cities: list[str] | None = None,
        constraints: str = "",
        budget: str = "",
        max_slots: int = 80,
    ) -> dict[str, Any]:
        raw_keywords = [kw for kw in (keywords or []) if kw]
        keywords = self._normalize_major_keywords(category, raw_keywords)
        preferred_cities = [city for city in (preferred_cities or []) if city]
        education_level = self._normalize_education_level(education_level)
        eq_by_year = {
            int(row["year"]): int(row["equivalent_score"])
            for row in equivalent_scores.get("years", [])
            if row.get("year") and row.get("equivalent_score")
        }
        if not eq_by_year and score:
            eq_by_year = {0: score}
        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = self._query_hebei_plan_rows(conn, province, category, education_level, eq_by_year, rank, keywords, max_slots * 50)
            if keywords and len(rows) < max_slots * 2:
                broadened = self._query_hebei_plan_rows(conn, province, category, education_level, eq_by_year, rank, [], max_slots * 50)
                seen = {(row["school_name"], row["major_name"], row["year"]) for row in rows}
                rows.extend(row for row in broadened if (row["school_name"], row["major_name"], row["year"]) not in seen)
        candidates = [self._hebei_plan_candidate(row, eq_by_year, rank, preferred_cities, keywords) for row in rows]
        candidates = [item for item in candidates if item.get("risk_bucket") in {"冲", "稳", "保"}]
        self._attach_hebei_plan_info(candidates)
        candidates, hard_filter = self._apply_hebei_hard_filters(candidates, constraints, budget)
        for item in candidates:
            item["plan_score"] = self._plan_score(item)
            item["utility"] = item["plan_score"]
        candidates.sort(key=lambda item: (
            {"稳": 0, "保": 1, "冲": 2}.get(item["risk_bucket"], 9),
            -item["plan_score"],
            item.get("risk_abs", 999999),
            -int(item.get("year") or 0),
        ))
        candidate_pool = []
        for index, item in enumerate(candidates, start=1):
            pool_item = dict(item)
            pool_item["rank"] = index
            candidate_pool.append(pool_item)
        selected = self._balanced_plan_select(candidates, max_slots)
        self._attach_hebei_stability(selected)
        return {
            "mode": "six_step_plan_hebei_lnwc",
            "data_source": self.data_source_meta(),
            "student_rank": rank or None,
            "summary": {
                "total": len(selected),
                "chong": sum(1 for r in selected if r["tag"] == "冲"),
                "wen": sum(1 for r in selected if r["tag"] == "稳"),
                "bao": sum(1 for r in selected if r["tag"] == "保"),
                "candidate_pool_before_select": len(candidates),
                "note": "使用河北考试院历年录取位次和河北一分一段等位分生成候选池。",
            },
            "hard_filter": hard_filter,
            "advisor_note": "",
            "advisor_top_majors": [],
            "candidate_pool_recommendations": candidate_pool,
            "recommendations": selected,
            "quality_warnings": [self._coverage_warning()],
            "explanation": self._plan_explain(selected, "河北", category, education_level, rank, score, bool(raw_keywords)),
        }

    def _query_hebei_plan_rows(
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
        if province and province != "河北":
            return []
        base, params = self._hebei_base_filter(category, education_level, keywords)
        if eq_by_year and 0 not in eq_by_year:
            placeholders = ",".join("?" for _ in eq_by_year)
            base += f" AND year IN ({placeholders})"
            params.extend(sorted(eq_by_year))
        windows = []
        window_params: list[Any] = []
        score_values = list(eq_by_year.values())
        score_mid = int(sum(score_values) / len(score_values)) if score_values else 0
        if score_values:
            windows.append("(min_score BETWEEN ? AND ?)")
            window_params.extend([max(0, min(score_values) - 60), max(score_values) + 35])
        if rank:
            windows.append("(min_rank BETWEEN ? AND ?)")
            window_params.extend([max(1, int(rank * 0.70)), int(rank * 1.90)])
        if not windows:
            return []
        base += " AND (" + " OR ".join(windows) + ")"
        params.extend(window_params)
        return conn.execute(
            f"""
            SELECT year, batch_name, category_name, school_code, school_name,
                   major_code, major_name, min_score, avg_score, min_rank,
                   volunteer_type, source_file
            FROM hebei_lnwc_loggedin
            WHERE {base}
            ORDER BY year DESC,
                     ABS(COALESCE(min_score, ?) - ?) ASC,
                     min_rank ASC
            LIMIT ?
            """,
            params + [score_mid, score_mid, limit],
        ).fetchall()

    @classmethod
    def _hebei_plan_candidate(
        cls,
        row: sqlite3.Row,
        eq_by_year: dict[int, int],
        student_rank: int,
        preferred_cities: list[str],
        keywords: list[str],
    ) -> dict[str, Any]:
        year = int(row["year"] or 0)
        equivalent_score = eq_by_year.get(year) or (next(iter(eq_by_year.values())) if eq_by_year else 0)
        admission_score = int(row["min_score"] or 0)
        admission_rank = int(row["min_rank"] or 0)
        risk_bucket, risk_gap, risk_abs = cls._risk_bucket(student_rank, admission_rank, equivalent_score, admission_score)
        item = cls._hebei_row_to_item(row, risk_bucket)
        item.update({
            "risk_bucket": risk_bucket,
            "risk_gap": risk_gap,
            "risk_abs": risk_abs,
            "equivalent_score": equivalent_score,
            "score_gap": equivalent_score - admission_score if equivalent_score and admission_score else None,
            "rank_gap": admission_rank - student_rank if student_rank and admission_rank else None,
        })
        item["fit_score"] = cls._fit_score(item, preferred_cities, keywords)
        item["plan_score"] = cls._plan_score(item)
        item["utility"] = item["plan_score"]
        item["note"] = cls._plan_row_note(item)
        item["confidence"] = cls._plan_confidence(item)
        item["evidence"] = cls._hebei_evidence(item)
        return item

    @staticmethod
    def _hebei_base_filter(category: str, education_level: str, keywords: list[str]) -> tuple[str, list[Any]]:
        base = "volunteer_type = '一志愿'"
        params: list[Any] = []
        category_name = XuefengAdmissionRepository._hebei_category_name(category)
        if category_name:
            base += " AND category_name = ?"
            params.append(category_name)
        level = XuefengAdmissionRepository._normalize_education_level(education_level)
        if level == "本科":
            base += " AND batch_name = '本科批'"
        elif level == "专科":
            base += " AND batch_name = '专科批'"
        if keywords:
            kw_sql = []
            for kw in keywords:
                kw_sql.append("(major_name LIKE ? OR school_name LIKE ?)")
                params.extend([f"%{kw}%", f"%{kw}%"])
            base += " AND (" + " OR ".join(kw_sql) + ")"
        return base, params

    @staticmethod
    def _normalize_major_keywords(category: str, keywords: list[str]) -> list[str]:
        category_name = XuefengAdmissionRepository._hebei_category_name(category)
        normalized: list[str] = []
        for raw in keywords:
            kw = str(raw or "").strip()
            if not kw:
                continue
            if category_name == "历史科目组合" and kw == "电子":
                normalized.extend(["电子信息", "电子科学", "电子工程", "微电子", "集成电路"])
                continue
            normalized.append(kw)
        return list(dict.fromkeys(normalized))

    @staticmethod
    def _hebei_category_name(category: str) -> str:
        text = str(category or "")
        if "物理" in text or "理科" in text:
            return "物理科目组合"
        if "历史" in text or "文科" in text:
            return "历史科目组合"
        return ""

    @staticmethod
    def _hebei_rows(conn: sqlite3.Connection, where_and_order: str, params: list[Any], tag: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            f"""
            SELECT year, batch_name, category_name, school_code, school_name,
                   major_code, major_name, min_score, avg_score, min_rank,
                   volunteer_type, source_file
            FROM hebei_lnwc_loggedin
            WHERE {where_and_order}
            """,
            params,
        ).fetchall()
        return [XuefengAdmissionRepository._hebei_row_to_item(row, tag) for row in rows]

    @staticmethod
    def _hebei_row_to_item(row: sqlite3.Row, tag: str) -> dict[str, Any]:
        item = {
            "school_name": row["school_name"],
            "sp_name": row["major_name"],
            "score": int(row["min_score"] or 0),
            "rank_value": int(row["min_rank"] or 0) or None,
            "year": int(row["year"] or 0),
            "province": "河北",
            "category": row["category_name"],
            "batch": row["batch_name"],
            "education_level": "本科" if "本科" in str(row["batch_name"]) else "专科",
            "school_code": row["school_code"],
            "major_code": row["major_code"],
            "avg_score": int(row["avg_score"] or 0) or None,
            "volunteer_type": row["volunteer_type"],
            "source_type": "official",
            "source_trust_level": "hebei_exam_authority_loggedin",
            "source_file": row["source_file"],
            "source_priority": 100,
            "quality_flags": json.dumps(["hebei_lnwc_loggedin"], ensure_ascii=False),
            "city": "",
            "tier": "",
            "tag": tag,
            "rank": 0,
            "p": None,
            "p_pct": "河北历年录取位次",
            "utility": None,
            "major_match": True,
        }
        item["note"] = XuefengAdmissionRepository._row_note(item)
        item["source"] = XuefengAdmissionRepository._source_label(item)
        item["sources"] = [item["source"]]
        item["source_year"] = item["year"]
        item["source_score"] = item["score"]
        item["source_rank"] = item["rank_value"]
        item["confidence"] = XuefengAdmissionRepository._confidence_for_tag(item["tag"], item.get("source_type"))
        item["evidence"] = XuefengAdmissionRepository._hebei_evidence(item)
        return item

    @staticmethod
    def _hebei_evidence(item: dict[str, Any]) -> dict[str, Any]:
        evidence = {
            "source": item["source"],
            "year": item["source_year"],
            "score": item["source_score"],
            "rank": item["source_rank"],
            "confidence": item["confidence"],
            "province": "河北",
            "category": item.get("category", ""),
            "batch": item.get("batch", ""),
            "school_code": item.get("school_code", ""),
            "major_code": item.get("major_code", ""),
            "quality_flags": XuefengAdmissionRepository._parse_flags(item.get("quality_flags")),
        }
        if item.get("plan_match_status"):
            evidence["plan_source"] = item.get("plan_source", "")
            evidence["plan_match_status"] = item.get("plan_match_status", "")
            evidence["tuition"] = item.get("tuition_text") or item.get("tuition")
            evidence["plan_count"] = item.get("plan_count")
        if item.get("stability"):
            evidence["stability"] = item.get("stability")
        return evidence

    def _attach_hebei_stability(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        keys = {
            (
                str(item.get("batch") or ""),
                str(item.get("category") or ""),
                str(item.get("school_code") or ""),
                str(item.get("major_code") or ""),
            )
            for item in items
            if item.get("school_code") and item.get("major_code")
        }
        if not keys:
            return
        placeholders = ",".join("(?,?,?,?)" for _ in keys)
        params: list[Any] = []
        for key in keys:
            params.extend(key)
        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT batch_name, category_name, school_code, major_code,
                       COUNT(DISTINCT year) AS years_count,
                       MIN(year) AS first_year,
                       MAX(year) AS latest_year,
                       MIN(min_rank) AS best_rank,
                       MAX(min_rank) AS worst_rank,
                       ROUND(AVG(min_rank), 0) AS avg_rank,
                       MIN(CASE WHEN year=2025 THEN min_rank END) AS rank_2025,
                       MIN(CASE WHEN year=2024 THEN min_rank END) AS rank_2024,
                       MIN(CASE WHEN year=2023 THEN min_rank END) AS rank_2023
                FROM hebei_lnwc_loggedin
                WHERE volunteer_type='一志愿'
                  AND (batch_name, category_name, school_code, major_code) IN ({placeholders})
                GROUP BY batch_name, category_name, school_code, major_code
                """,
                params,
            ).fetchall()
        stability_map = {
            (row["batch_name"], row["category_name"], row["school_code"], row["major_code"]): dict(row)
            for row in rows
        }
        with sqlite3.connect(self.status.db_path) as conn:
            conn.row_factory = sqlite3.Row
            for item in items:
                key = (
                    str(item.get("batch") or ""),
                    str(item.get("category") or ""),
                    str(item.get("school_code") or ""),
                    str(item.get("major_code") or ""),
                )
                stats = stability_map.get(key)
                if stats and int(stats.get("years_count") or 0) >= 2:
                    payload = self._stability_payload(stats)
                else:
                    fallback = self._fetch_hebei_name_stability(conn, item)
                    payload = self._stability_payload(fallback) if fallback else (self._stability_payload(stats) if stats else {"label": "数据不足", "years_count": 0, "risk": "high"})
                item["stability"] = payload
                item["stability_label"] = item["stability"]["label"]
                item["stability_risk"] = item["stability"]["risk"]
                if isinstance(item.get("evidence"), dict):
                    item["evidence"]["stability"] = item["stability"]

    @staticmethod
    def _fetch_hebei_name_stability(conn: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any] | None:
        if not item.get("school_name") or not item.get("sp_name"):
            return None
        row = conn.execute(
            """
            SELECT batch_name, category_name, school_name, major_name,
                   COUNT(DISTINCT year) AS years_count,
                   MIN(year) AS first_year,
                   MAX(year) AS latest_year,
                   MIN(min_rank) AS best_rank,
                   MAX(min_rank) AS worst_rank,
                   ROUND(AVG(min_rank), 0) AS avg_rank,
                   MIN(CASE WHEN year=2025 THEN min_rank END) AS rank_2025,
                   MIN(CASE WHEN year=2024 THEN min_rank END) AS rank_2024,
                   MIN(CASE WHEN year=2023 THEN min_rank END) AS rank_2023
            FROM hebei_lnwc_loggedin
            WHERE volunteer_type='一志愿'
              AND batch_name=?
              AND category_name=?
              AND school_name=?
              AND major_name=?
            GROUP BY batch_name, category_name, school_name, major_name
            """,
            (item.get("batch"), item.get("category"), item.get("school_name"), item.get("sp_name")),
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _stability_payload(stats: dict[str, Any]) -> dict[str, Any]:
        years_count = int(stats.get("years_count") or 0)
        best_rank = int(stats.get("best_rank") or 0)
        worst_rank = int(stats.get("worst_rank") or 0)
        avg_rank = int(float(stats.get("avg_rank") or 0))
        rank_2025 = int(stats.get("rank_2025") or 0)
        rank_2024 = int(stats.get("rank_2024") or 0)
        rank_2023 = int(stats.get("rank_2023") or 0)
        spread_ratio = (worst_rank - best_rank) / max(1, avg_rank) if avg_rank else 0.0
        if years_count >= 3 and spread_ratio <= 0.20:
            label, risk = "三年稳定", "low"
        elif years_count >= 3 and spread_ratio <= 0.45:
            label, risk = "三年有波动", "medium"
        elif years_count >= 2:
            label, risk = "波动较大", "medium_high"
        else:
            label, risk = "数据不足", "high"
        trend = "趋势不明"
        if rank_2025 and rank_2024:
            if rank_2025 < rank_2024 * 0.90:
                trend = "变热"
            elif rank_2025 > rank_2024 * 1.10:
                trend = "变冷"
            else:
                trend = "基本稳定"
        return {
            "label": label,
            "risk": risk,
            "years_count": years_count,
            "best_rank": best_rank or None,
            "worst_rank": worst_rank or None,
            "avg_rank": avg_rank or None,
            "spread_ratio": round(spread_ratio, 3),
            "trend": trend,
            "rank_by_year": {
                "2025": rank_2025 or None,
                "2024": rank_2024 or None,
                "2023": rank_2023 or None,
            },
        }

    def _attach_hebei_plan_info(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        plan_path = self._hebei_plan_path
        if not plan_path:
            for item in items:
                self._set_missing_plan_info(item, "reserved_no_plan_db")
            return
        keys = {
            (
                str(item.get("batch") or ""),
                str(item.get("category") or ""),
                str(item.get("school_code") or ""),
                str(item.get("major_code") or ""),
            )
            for item in items
            if item.get("school_code") and item.get("major_code")
        }
        batch_category_pairs = {
            (
                str(item.get("batch") or ""),
                str(item.get("category") or ""),
            )
            for item in items
            if item.get("batch") and item.get("category")
        }
        if not keys and not batch_category_pairs:
            for item in items:
                self._set_missing_plan_info(item, "missing_school_or_major_code")
            return
        exact_rows: list[sqlite3.Row] = []
        fallback_rows: list[sqlite3.Row] = []
        with sqlite3.connect(plan_path) as conn:
            conn.row_factory = sqlite3.Row
            if keys:
                placeholders = ",".join("(?,?,?,?)" for _ in keys)
                params: list[Any] = []
                for key in keys:
                    params.extend(key)
                exact_rows = conn.execute(
                    f"""
                    SELECT *
                    FROM hebei_2026_plan
                    WHERE (batch_name, category_name, school_code, major_code) IN ({placeholders})
                    """,
                    params,
                ).fetchall()
            if batch_category_pairs:
                placeholders = ",".join("(?,?)" for _ in batch_category_pairs)
                params = []
                for key in batch_category_pairs:
                    params.extend(key)
                fallback_rows = conn.execute(
                    f"""
                    SELECT *
                    FROM hebei_2026_plan
                    WHERE (batch_name, category_name) IN ({placeholders})
                    """,
                    params,
                ).fetchall()
        plan_map = {
            (row["batch_name"], row["category_name"], row["school_code"], row["major_code"]): row
            for row in exact_rows
        }
        plan_by_school_major_code = {
            (
                row["batch_name"],
                row["category_name"],
                self._normalize_plan_school_text(row["school_name"]),
                row["major_code"],
            ): row
            for row in fallback_rows
            if row["school_name"] and row["major_code"]
        }
        plan_by_school_major_name = {
            (
                row["batch_name"],
                row["category_name"],
                self._normalize_plan_school_text(row["school_name"]),
                self._normalize_plan_major_text(row["major_name"]),
            ): row
            for row in fallback_rows
            if row["school_name"] and row["major_name"]
        }
        for item in items:
            key = (
                str(item.get("batch") or ""),
                str(item.get("category") or ""),
                str(item.get("school_code") or ""),
                str(item.get("major_code") or ""),
            )
            row = plan_map.get(key)
            match_status = "official_matched"
            if not row:
                name_code_key = (
                    str(item.get("batch") or ""),
                    str(item.get("category") or ""),
                    self._normalize_plan_school_text(str(item.get("school_name") or "")),
                    str(item.get("major_code") or ""),
                )
                row = plan_by_school_major_code.get(name_code_key)
                match_status = "official_matched_by_school_major_code"
            if not row:
                name_major_key = (
                    str(item.get("batch") or ""),
                    str(item.get("category") or ""),
                    self._normalize_plan_school_text(str(item.get("school_name") or "")),
                    self._normalize_plan_major_text(str(item.get("sp_name") or item.get("major_name") or "")),
                )
                row = plan_by_school_major_name.get(name_major_key)
                match_status = "official_matched_by_school_major_name"
            if row:
                self._set_plan_info(item, row)
                item["plan_match_status"] = match_status
                item["evidence"] = XuefengAdmissionRepository._hebei_evidence(item)
            else:
                self._set_missing_plan_info(item, "reserved_waiting_official_plan")

    @staticmethod
    def _normalize_plan_school_text(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"\[[^\]]*\]", "", text)
        text = re.sub(r"（[^）]*）", "", text)
        text = re.sub(r"\([^)]*\)", "", text)
        return re.sub(r"\s+", "", text)

    @staticmethod
    def _normalize_plan_major_text(value: str) -> str:
        return re.sub(r"\s+", "", str(value or "").strip())

    @property
    def _hebei_plan_path(self) -> str:
        for path in (self.hebei_plan_db_path, self.pipeline_hebei_plan_db_path):
            if os.path.exists(path):
                return path
        return ""

    @staticmethod
    def _set_plan_info(item: dict[str, Any], row: sqlite3.Row) -> None:
        item["plan_year"] = row["year"]
        item["plan_count"] = row["plan_count"]
        item["tuition"] = row["tuition"]
        item["tuition_text"] = row["tuition_text"] or (f"{row['tuition']}元/年" if row["tuition"] else "")
        item["duration"] = row["duration"]
        item["campus"] = row["campus"]
        item["subject_requirement"] = row["subject_requirement"]
        item["plan_remarks"] = row["remarks"]
        item["plan_source"] = row["source_system"]
        item["plan_source_url"] = row["source_url"]
        item["plan_confidence"] = row["confidence"]
        item["plan_is_mock"] = bool(row["is_mock"])
        item["plan_match_status"] = "mock_matched" if row["is_mock"] else "official_matched"
        item["evidence"] = XuefengAdmissionRepository._hebei_evidence(item)

    @staticmethod
    def _set_missing_plan_info(item: dict[str, Any], status: str) -> None:
        item.setdefault("plan_year", 2026)
        item.setdefault("plan_count", None)
        item.setdefault("tuition", None)
        item.setdefault("tuition_text", "")
        item.setdefault("duration", "")
        item.setdefault("campus", "")
        item.setdefault("subject_requirement", "")
        item.setdefault("plan_remarks", "未匹配到河北考试院2026招生计划；需人工核对计划数、学费、学制和选科要求。")
        item.setdefault("plan_source", "reserved_hebei_2026_plan")
        item.setdefault("plan_source_url", "")
        item.setdefault("plan_confidence", "missing")
        item.setdefault("plan_is_mock", False)
        item["plan_match_status"] = status
        item["evidence"] = XuefengAdmissionRepository._hebei_evidence(item)

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
            f"已基于河北考试院历年录取库生成 {len(selected)} 个候选。" if province == "河北" else f"已基于本地录取库生成 {len(selected)} 个候选。",
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
        plan_coverage = self.hebei_plan_coverage()
        if self._is_hebei_lnwc:
            source_id = "hebei_lnwc_loggedin"
            source_name = "河北考试院历年录取库 hebei_lnwc_loggedin.db"
        elif self._is_unified:
            source_id = "unified_admission"
            source_name = "统一录取库 unified_admission.db"
        else:
            source_id = "xuefeng_admission_clean"
            source_name = "xuefeng-agent admission_clean.db"
        is_unified = self._is_unified
        return {
            "id": source_id,
            "name": source_name,
            "role": "primary",
            "ready": self.ready,
            "db_path": self.status.db_path,
            "source_kind": self.status.source_kind,
            "message": self.status.message,
            "coverage": coverage,
            "plan_coverage": plan_coverage,
        }

    def hebei_plan_coverage(self) -> dict[str, Any]:
        path = self._hebei_plan_path
        if not path:
            return {
                "ready": False,
                "db_path": "",
                "record_count": 0,
                "official_count": 0,
                "mock_count": 0,
                "batch_category_counts": [],
                "message": "未找到河北2026招生计划库。",
            }
        try:
            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n,
                           SUM(CASE WHEN is_mock=0 THEN 1 ELSE 0 END) AS official_n,
                           SUM(CASE WHEN is_mock=1 THEN 1 ELSE 0 END) AS mock_n,
                           MIN(year) AS min_year,
                           MAX(year) AS max_year,
                           COUNT(NULLIF(tuition_text, '')) AS tuition_text_n,
                           COUNT(plan_count) AS plan_count_n,
                           COUNT(NULLIF(subject_requirement, '')) AS subject_n
                    FROM hebei_2026_plan
                    """
                ).fetchone()
                batch_rows = conn.execute(
                    """
                    SELECT batch_name, category_name, COUNT(*) AS n,
                           COUNT(DISTINCT school_code) AS school_count,
                           COUNT(NULLIF(tuition_text, '')) AS tuition_text_n,
                           COUNT(plan_count) AS plan_count_n
                    FROM hebei_2026_plan
                    GROUP BY batch_name, category_name
                    ORDER BY batch_name, category_name
                    """
                ).fetchall()
            total = int(row["n"] or 0) if row else 0
            official_count = int(row["official_n"] or 0) if row else 0
            return {
                "ready": total > 0,
                "db_path": path,
                "record_count": total,
                "official_count": official_count,
                "mock_count": int(row["mock_n"] or 0) if row else 0,
                "year_min": row["min_year"] if row else None,
                "year_max": row["max_year"] if row else None,
                "tuition_text_count": int(row["tuition_text_n"] or 0) if row else 0,
                "plan_count_count": int(row["plan_count_n"] or 0) if row else 0,
                "subject_requirement_count": int(row["subject_n"] or 0) if row else 0,
                "batch_category_counts": [
                    {
                        "batch": r["batch_name"],
                        "category": r["category_name"],
                        "records": r["n"],
                        "schools": r["school_count"],
                        "tuition_text_count": r["tuition_text_n"],
                        "plan_count_count": r["plan_count_n"],
                    }
                    for r in batch_rows
                ],
                "message": f"河北考试院2026招生计划库可用，官方记录 {official_count} 条。" if official_count else "河北2026招生计划库可用，但当前不是官方记录。",
            }
        except Exception as exc:
            return {
                "ready": False,
                "db_path": path,
                "record_count": 0,
                "official_count": 0,
                "mock_count": 0,
                "batch_category_counts": [],
                "message": f"河北2026招生计划库读取失败：{exc}",
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

    @property
    def _is_hebei_lnwc(self) -> bool:
        return self.status.source_kind == "hebei_lnwc"

    def _coverage_warning(self) -> str:
        if self._is_hebei_lnwc:
            return "当前主数据源是河北考试院登录查询历年录取库，并已接入河北考试院2026招生计划库；仅服务河北考生，主推荐默认只使用一志愿记录。"
        if self._is_unified:
            return "当前主数据源是 unified_admission.db：官方数据优先，掌上高考和开源快照作为补充；第三方/低信任记录已保留质量标记。"
        return "当前主数据源是 xuefeng-agent admission_clean.db，本地样本主要覆盖部分省份和 2024-2025 年历史录取记录；这不是全国完整录取库。"

    def _summary_note(self) -> str:
        if self._is_hebei_lnwc:
            return "使用河北考试院 hebei_lnwc_loggedin.db，按一志愿历年录取位次/分数区间和近三年稳定性生成冲稳保。"
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
        if item.get("source_trust_level") == "hebei_exam_authority_loggedin":
            return "河北考试院历年录取查询 / hebei_lnwc_loggedin.db"
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
