"""
Application-level orchestration.

The recommendation engine remains deterministic. This layer prepares inputs,
adds Monte Carlo simulation, attaches school life-quality information, and
builds a concise explanation payload for UI or future LLM rendering.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from engine.advisor import FamilyProfile
from engine.data_loader import DataLoader
from engine.monte_carlo import simulate
from engine.profile import StudentProfile, profile_to_query
from engine.recommend import RecommendationEngine

from services.school_life import SchoolLifeRepository, default_life_link


class AdvisorOrchestrator:
    def __init__(
        self,
        data_loader: DataLoader,
        life_repo: SchoolLifeRepository | None = None,
    ):
        self.data_loader = data_loader
        self.engine = RecommendationEngine(data_loader)
        self.life_repo = life_repo or SchoolLifeRepository()

    def recommend(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = self._profile_from_payload(payload)
        query, advisor_output = profile_to_query(profile, self.data_loader)
        recommendations = self.engine.recommend(query)
        summary = self.engine.recommend_summary(recommendations, query)
        coverage = self.engine.coverage_probability(recommendations)
        simulation = simulate(recommendations, n=int(payload.get("simulation_trials", 5000)))

        enriched = []
        for rec in recommendations:
            life = self.life_repo.find(rec.school_name)
            life_payload = life.to_dict() if life else {
                "school_name": rec.school_name,
                "matched_name": "",
                "summary": "",
                "source_url": default_life_link(rec.school_name),
                "confidence": "not_cached",
                "license": "link-only",
            }
            enriched.append({
                "rank": rec.rank,
                "school_name": rec.school_name,
                "city": rec.city,
                "tier": rec.tier,
                "sp_name": rec.sp_name,
                "level3_name": rec.level3_name,
                "major_type": rec.major_type,
                "p": rec.p,
                "p_pct": f"{rec.p * 100:.0f}%",
                "tag": rec.tag,
                "utility": rec.utility,
                "major_match": rec.major_match,
                "note": rec.note,
                "sources": ["gaokao.db", "deterministic_engine"],
                "school_life": life_payload,
            })

        advisor_top = []
        advisor_note = ""
        if advisor_output is not None:
            advisor_note = advisor_output.narrative
            advisor_top = [
                {
                    "major_name": item.major_name,
                    "recommendation": item.recommendation,
                    "fit_score": item.fit_score,
                    "reasons": item.reasons[:2],
                }
                for item in advisor_output.major_advice[:6]
            ]

        return {
            "student_rank": query.student_rank,
            "summary": {
                **summary,
                "coverage": round(coverage, 4),
                "simulation": {
                    "n_trials": simulation.n_trials,
                    "no_admission_prob": round(simulation.no_admission_prob, 4),
                    "tier_probs": simulation.tier_probs,
                    "tag_probs": simulation.tag_probs,
                    "avg_slot": round(simulation.avg_slot, 2),
                    "p50_slot": simulation.p50_slot,
                    "p90_slot": simulation.p90_slot,
                },
            },
            "advisor_note": advisor_note,
            "advisor_top_majors": advisor_top,
            "recommendations": enriched,
            "explanation": self._build_explanation(summary, coverage, simulation.no_admission_prob),
        }

    def _profile_from_payload(self, payload: dict[str, Any]) -> StudentProfile:
        family_payload = payload.get("family_profile")
        family = None
        if isinstance(family_payload, dict):
            family = FamilyProfile(
                economic_tier=family_payload.get("economic_tier", "工薪"),
                parental_background=family_payload.get("parental_background", []),
                career_priority=family_payload.get("career_priority", "未确定"),
                risk_tolerance=family_payload.get("risk_tolerance", "稳健"),
                family_support_years=int(family_payload.get("family_support_years", 6)),
                first_gen_college=bool(family_payload.get("first_gen_college", False)),
                postgrad_willing=family_payload.get("postgrad_willing", "视情况"),
                overseas_plan=family_payload.get("overseas_plan", "无计划"),
            )

        return StudentProfile(
            province_id=int(payload["province_id"]),
            category=payload["category"],
            score=int(payload["score"]),
            min_tier=payload.get("min_tier", "本科"),
            preferred_cities=payload.get("preferred_cities", []),
            major_keywords=payload.get("major_keywords", []),
            w_tier=float(payload.get("w_tier", 0.45)),
            w_city=float(payload.get("w_city", 0.30)),
            w_major=float(payload.get("w_major", 0.25)),
            max_slots=int(payload.get("max_slots", 30)),
            prefer_home_province=bool(payload.get("prefer_home_province", False)),
            xuanke_codes=[str(c) for c in payload.get("xuanke_codes", [])],
            family=family,
        )

    @staticmethod
    def _build_explanation(summary: dict[str, Any], coverage: float, no_admission_prob: float) -> str:
        parts = [
            f"本方案生成 {summary.get('total', 0)} 个志愿项：",
            f"冲 {summary.get('chong', 0)} 个，稳 {summary.get('wen', 0)} 个，保 {summary.get('bao', 0)} 个。",
            f"保底覆盖概率约 {coverage * 100:.1f}%。",
            f"平行志愿模拟未录取概率约 {no_admission_prob * 100:.1f}%。",
        ]
        if summary.get("switch_note"):
            parts.append(summary["switch_note"])
        if summary.get("major_mismatch_count"):
            parts.append(f"有 {summary['major_mismatch_count']} 个候选专业不是严格关键词匹配，用于补足安全结构。")
        return "".join(parts)

