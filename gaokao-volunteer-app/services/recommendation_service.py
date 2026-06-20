"""Unified recommendation service."""

from __future__ import annotations

import os
from typing import Any

from engine.data_loader import DataLoader
from services.orchestrator import AdvisorOrchestrator
from services.school_life import SchoolLifeRepository, default_life_link
from services.xuefeng_data import XuefengAdmissionRepository
from services.evidence import attach_evidence_level


class RecommendationService:
    def __init__(self, app_dir: str):
        self.app_dir = os.path.abspath(app_dir)
        self.data_dir = os.path.join(self.app_dir, "data")
        self.gaokao_db_path = os.path.join(self.data_dir, "gaokao.db")
        self.life_repo = SchoolLifeRepository()
        self.primary_repo = XuefengAdmissionRepository(self.data_dir)
        self.advisor_orchestrator: AdvisorOrchestrator | None = None
        self.advisor_init_error = ""
        self._init_advisor()

    def _init_advisor(self) -> None:
        if not os.path.exists(self.gaokao_db_path):
            return
        try:
            data_loader = DataLoader(self.gaokao_db_path)
            self.advisor_orchestrator = AdvisorOrchestrator(data_loader, self.life_repo)
        except Exception as exc:
            self.advisor_init_error = str(exc)

    @property
    def ready(self) -> bool:
        return self.primary_repo.ready or self.advisor_orchestrator is not None

    def status(self) -> dict[str, Any]:
        advisor_ready = self.advisor_orchestrator is not None
        primary_meta = self.primary_repo.data_source_meta()
        primary_mode = "unified_primary" if primary_meta.get("source_kind") == "unified" else "xuefeng_primary"
        return {
            "ready": self.ready,
            "mode": primary_mode if self.primary_repo.ready else ("advisor_only" if advisor_ready else "not_ready"),
            "primary_data_source": primary_meta,
            "optional_engines": {
                "gaokao_advisor": {
                    "ready": advisor_ready,
                    "db_path": self.gaokao_db_path,
                    "db_exists": os.path.exists(self.gaokao_db_path),
                    "init_error": self.advisor_init_error,
                    "role": "optional_advanced_engine",
                }
            },
            "quality_warnings": primary_meta.get("coverage", {}).get("quality_warnings", []),
        }

    def recommend(self, payload: dict[str, Any]) -> dict[str, Any]:
        engine_mode = payload.get("engine_mode") or "unified"
        if engine_mode == "advisor":
            if self.advisor_orchestrator is None:
                raise RuntimeError("gaokao-advisor advanced engine is not ready; use engine_mode=unified or provide data/gaokao.db")
            result = self.advisor_orchestrator.recommend(payload)
            return self._normalize_advisor_result(result)

        if not self.primary_repo.ready:
            if self.advisor_orchestrator is not None:
                result = self.advisor_orchestrator.recommend(payload)
                return self._normalize_advisor_result(result)
            raise RuntimeError(self.primary_repo.status.message)

        province = payload.get("province") or payload.get("province_name") or ""
        if not province:
            raise ValueError("province is required")

        result = self.primary_repo.recommend(
            province=province,
            category=payload.get("category", ""),
            education_level=payload.get("education_level", ""),
            score=int(payload.get("score") or 0),
            rank=int(payload.get("rank") or 0),
            keywords=payload.get("major_keywords", []),
            max_slots=int(payload.get("max_slots", 30)),
        )
        result["recommendations"] = [self._attach_life(item) for item in result.get("recommendations", [])]
        result["engine"] = {
            "id": "unified",
            "name": "unified historical interval recommender",
            "advanced": False,
        }
        return result

    def recommend_for_plan(self, payload: dict[str, Any], equivalent_scores: dict[str, Any]) -> dict[str, Any]:
        if not self.primary_repo.ready:
            return self.recommend(payload)
        province = payload.get("province") or payload.get("province_name") or ""
        if not province:
            raise ValueError("province is required")
        result = self.primary_repo.recommend_for_plan(
            province=province,
            category=payload.get("category", ""),
            education_level=payload.get("education_level", ""),
            equivalent_scores=equivalent_scores,
            score=int(payload.get("score") or 0),
            rank=int(payload.get("rank") or 0),
            keywords=payload.get("major_keywords", []),
            preferred_cities=payload.get("preferred_cities", []),
            max_slots=int(payload.get("max_slots", 30)),
        )
        result["recommendations"] = [self._attach_life(item) for item in result.get("recommendations", [])]
        result["engine"] = {
            "id": "six_step_plan_unified",
            "name": "equivalent-score six-step planner",
            "advanced": False,
        }
        return result

    def _normalize_advisor_result(self, result: dict[str, Any]) -> dict[str, Any]:
        result = dict(result)
        result["mode"] = "gaokao_advisor_engine"
        result["engine"] = {
            "id": "gaokao_advisor",
            "name": "gaokao-advisor deterministic probability engine",
            "advanced": True,
        }
        result["data_source"] = {
            "id": "gaokao_db",
            "name": "gaokao-advisor gaokao.db",
            "role": "optional_advanced_engine",
            "ready": True,
            "db_path": self.gaokao_db_path,
        }
        result["quality_warnings"] = result.get("quality_warnings", [])
        normalized = []
        for item in result.get("recommendations", []):
            item = dict(item)
            item.setdefault("source", "gaokao-advisor gaokao.db")
            item.setdefault("sources", [item["source"], "gaokao-advisor engine"])
            item.setdefault("source_year", item.get("year") or "multi_year_model")
            item.setdefault("source_score", item.get("score"))
            item.setdefault("source_rank", item.get("rank_value") or item.get("student_rank"))
            item.setdefault("confidence", self._advisor_confidence(item.get("p")))
            item["evidence"] = {
                "source": item["source"],
                "year": item["source_year"],
                "score": item["source_score"],
                "rank": item["source_rank"],
                "confidence": item["confidence"],
            }
            normalized.append(self._attach_life(item))
        result["recommendations"] = normalized
        return result

    def _attach_life(self, item: dict[str, Any]) -> dict[str, Any]:
        if item.get("school_life"):
            return attach_evidence_level(item)
        life = self.life_repo.find(item.get("school_name", ""))
        item["school_life"] = life.to_dict() if life else {
            "school_name": item.get("school_name", ""),
            "matched_name": "",
            "summary": "",
            "source_url": default_life_link(item.get("school_name", "")),
            "confidence": "not_cached",
            "license": "link-only",
        }
        return attach_evidence_level(item)

    @staticmethod
    def _advisor_confidence(probability: Any) -> str:
        try:
            p = float(probability)
        except (TypeError, ValueError):
            return "unknown"
        if p >= 0.75:
            return "high"
        if p >= 0.45:
            return "medium"
        return "low"
