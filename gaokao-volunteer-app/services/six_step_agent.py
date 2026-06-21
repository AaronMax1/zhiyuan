"""Six-step volunteer planning orchestration.

This layer turns the raw recommendation list into a decision workflow:
rank定位 -> 等位分 -> 候选池 -> 冲稳保 -> 排序 -> 章程核验.
It intentionally keeps hard admission evidence in RecommendationService and
leaves score-segment / charter data as explicit pending capabilities.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from services.recommendation_service import RecommendationService
from services.score_segments import ScoreSegmentRepository


REQUIRED_SLOTS = ("province", "score_or_rank", "category", "education_level", "goal")
OPTIONAL_SLOTS = ("major_interest", "major_dislike", "region", "family", "budget", "constraints")


class SixStepAgentService:
    def __init__(
        self,
        recommendations: RecommendationService,
        score_segments: ScoreSegmentRepository | None = None,
        llm_advisor: Any | None = None,
        charter_repo: Any | None = None,
        batch_lines: Any | None = None,
    ):
        self.recommendations = recommendations
        self.score_segments = score_segments
        self.llm_advisor = llm_advisor
        self.charter_repo = charter_repo
        self.batch_lines = batch_lines

    def inspect_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message") or "")
        existing = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
        profile = self.merge_profile(existing, self.extract_profile(message))
        missing = self.missing_slots(profile)
        return {
            "profile": profile,
            "filled_slots": [slot for slot in REQUIRED_SLOTS + OPTIONAL_SLOTS if self._slot_filled(profile, slot)],
            "missing_slots": missing,
            "ready_for_plan": self.ready_for_plan(profile),
            "next_question": self.next_question(missing),
            "agent_message": self.build_agent_message(profile, missing),
        }

    def build_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = self.profile_from_payload(payload)
        batch_control_lines = self.build_batch_control_lines(profile)
        equivalent_scores = self.build_equivalent_scores(profile)
        recommendation_payload = self.recommend_payload(profile, payload)
        if not recommendation_payload.get("rank") and equivalent_scores.get("rank"):
            recommendation_payload["rank"] = int(equivalent_scores["rank"])
        planning_blocked = bool(equivalent_scores.get("blocking") and not recommendation_payload.get("rank"))
        if planning_blocked:
            recommendation = {
                "mode": "locked_until_rank_or_equivalent_score",
                "summary": {"total": 0, "chong": 0, "wen": 0, "bao": 0},
                "recommendations": [],
                "quality_warnings": [],
                "explanation": "缺少用户位次，且等位分未计算成功，暂不生成候选池。",
            }
        else:
            recommendation = self.recommendations.recommend_for_plan(recommendation_payload, equivalent_scores)
        recs = recommendation.get("recommendations", [])
        buckets = self.bucket_recommendations(recs)
        evidence = self.evidence_summary(recs)
        candidate_pool = self.candidate_pool_summary(recs, evidence, equivalent_scores, planning_blocked)
        strategy = self.strategy_summary(profile, buckets, equivalent_scores, planning_blocked)
        plan = {
            "mode": "six_step_agent_plan",
            "profile": profile,
            "steps": self.build_steps(profile, equivalent_scores, recommendation, recs, candidate_pool, strategy, batch_control_lines),
            "batch_control_lines": batch_control_lines,
            "equivalent_scores": equivalent_scores,
            "candidate_pool": candidate_pool,
            "strategy": strategy,
            "volunteer_order": [] if planning_blocked else self.volunteer_order(recs),
            "charter_checks": [] if planning_blocked else self.charter_checks(recs),
            "recommendation": recommendation,
            "quality_warnings": self.quality_warnings(profile, recommendation, equivalent_scores, batch_control_lines),
        }
        if self.charter_repo and plan["charter_checks"]:
            plan["charter_saved"] = self.charter_repo.save_plan_checks(profile, plan["charter_checks"])
        else:
            plan["charter_saved"] = 0
        plan["llm_step_analyses"] = {}
        return plan

    def profile_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = dict(payload.get("profile") or {})
        direct = {
            "province": payload.get("province") or payload.get("province_name"),
            "province_id": payload.get("province_id"),
            "score": payload.get("score"),
            "rank": payload.get("rank"),
            "category": payload.get("category"),
            "education_level": payload.get("education_level"),
            "major_interest": payload.get("major_keywords"),
            "region": payload.get("preferred_cities"),
            "goal": payload.get("goal") or payload.get("career_goal"),
            "family": payload.get("family"),
            "budget": payload.get("budget"),
            "constraints": payload.get("constraints"),
        }
        return self.merge_profile(profile, {k: v for k, v in direct.items() if v not in (None, "", [])})

    def recommend_payload(self, profile: dict[str, Any], original: dict[str, Any]) -> dict[str, Any]:
        keywords = profile.get("major_interest") or original.get("major_keywords") or []
        if isinstance(keywords, str):
            keywords = [x.strip() for x in re.split(r"[,，、\s]+", keywords) if x.strip()]
        return {
            "province": profile.get("province", ""),
            "province_id": int(profile.get("province_id") or original.get("province_id") or 0),
            "category": profile.get("category", ""),
            "education_level": profile.get("education_level", ""),
            "score": int(profile.get("score") or 0),
            "rank": int(profile.get("rank") or 0),
            "major_keywords": keywords,
            "preferred_cities": profile.get("region") or original.get("preferred_cities") or [],
            "max_slots": int(original.get("max_slots") or 30),
            "engine_mode": original.get("engine_mode") or "unified",
        }

    def extract_profile(self, message: str) -> dict[str, Any]:
        profile: dict[str, Any] = {}
        province_hits = [(message.index(province), province) for province in PROVINCES if province in message]
        if province_hits:
            profile["province"] = sorted(province_hits)[0][1]
        score = re.search(r"(\d{3})\s*分", message)
        rank = re.search(r"(\d{4,7})\s*(?:名|位|位次|排名)", message)
        if score:
            profile["score"] = int(score.group(1))
        if rank:
            profile["rank"] = int(rank.group(1))
        if "本科" in message:
            profile["education_level"] = "本科"
        elif "专科" in message or "高职" in message:
            profile["education_level"] = "专科"
        if re.search(r"物理|理科", message):
            profile["category"] = "理科"
        elif re.search(r"历史|文科", message):
            profile["category"] = "文科"
        elif re.search(r"综合|普通类|不分文理", message):
            profile["category"] = "综合"
        majors = []
        for keyword in ("计算机", "电子", "电气", "临床", "口腔", "师范", "法学", "会计", "机械", "自动化", "护理", "铁路", "电力"):
            if keyword in message:
                majors.append(keyword)
        if majors:
            profile["major_interest"] = majors
        cities = [city for city in COMMON_CITIES if city in message]
        if cities:
            profile["region"] = cities
        if re.search(r"就业|赚钱|工资|好工作", message):
            profile["goal"] = "就业优先"
        elif re.search(r"考公|公务员|编制|稳定", message):
            profile["goal"] = "稳定/考公优先"
        elif re.search(r"考研|读研|深造", message):
            profile["goal"] = "深造优先"
        if re.search(r"电力|铁路|医院|医生|教师|老师|做生意|体制", message):
            profile["family"] = message
        return profile

    @staticmethod
    def merge_profile(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in incoming.items():
            if value not in (None, "", []):
                merged[key] = value
        return merged

    def missing_slots(self, profile: dict[str, Any]) -> list[str]:
        return [slot for slot in REQUIRED_SLOTS if not self._slot_filled(profile, slot)]

    def ready_for_plan(self, profile: dict[str, Any]) -> bool:
        missing = set(self.missing_slots(profile))
        has_hard_score = "score_or_rank" not in missing
        has_goal = "goal" not in missing
        filled_count = sum(1 for slot in REQUIRED_SLOTS + OPTIONAL_SLOTS if self._slot_filled(profile, slot))
        return has_hard_score and has_goal and filled_count >= 4

    @staticmethod
    def _slot_filled(profile: dict[str, Any], slot: str) -> bool:
        if slot == "score_or_rank":
            return bool(profile.get("score") or profile.get("rank"))
        return bool(profile.get(slot))

    @staticmethod
    def next_question(missing: list[str]) -> str:
        questions = {
            "province": "先告诉我你是哪个省的考生。",
            "score_or_rank": "分数和全省位次至少给一个；有位次最好，位次比分数更稳。",
            "category": "选科大类是理科大类、文科大类，还是全部科类一起看？",
            "education_level": "这次主要看本科、专科，还是都可以？",
            "goal": "你最看重什么：就业、稳定考公、继续深造，还是城市和学校层次？",
        }
        return questions.get(missing[0], "再补一下专业兴趣、城市偏好和家庭资源，我就能给完整方案。") if missing else "信息够了，可以生成六步志愿方案。"

    def build_agent_message(self, profile: dict[str, Any], missing: list[str]) -> str:
        if missing:
            return self.next_question(missing)
        return "信息基本够了。我会先用位次/分数定位，再给候选池、冲稳保和章程核验清单。"

    def build_steps(
        self,
        profile: dict[str, Any],
        equivalent_scores: dict[str, Any],
        recommendation: dict[str, Any],
        recs: list[dict[str, Any]],
        candidate_pool: dict[str, Any],
        strategy: dict[str, Any],
        batch_control_lines: dict[str, Any],
    ) -> list[dict[str, Any]]:
        has_rank = bool(profile.get("rank"))
        has_score = bool(profile.get("score"))
        eq_status = equivalent_scores.get("status", "missing")
        eq_blocking = bool(equivalent_scores.get("blocking"))
        hard_blocking = bool(eq_blocking and not profile.get("rank"))
        downstream_status = "locked" if hard_blocking else None
        rank_output = {
            "定位方式": "位次" if has_rank else ("分数粗定位" if has_score else "未定位"),
            "用户位次": profile.get("rank") or "",
            "用户分数": profile.get("score") or "",
            "提示": "位次优先，分数只作辅助。" if has_rank else "建议补全省位次；没有位次时用 2025 一分一段按分数换算。",
            "省控线状态": "已匹配" if batch_control_lines.get("lines") else "未匹配",
        }
        eq_rows = equivalent_scores.get("years", [])
        eq_output = {
            "可用年份": "、".join(str(row.get("year")) for row in eq_rows) or "",
            "缺失年份": "、".join(map(str, equivalent_scores.get("missing_years") or [])),
            "目标科类": equivalent_scores.get("category", ""),
            "是否阻断": "是" if eq_blocking else "否",
            "省控线缺口": "；".join(batch_control_lines.get("warnings") or []) or "无",
        }
        return [
            {
                "id": "rank定位",
                "title": "查位次，准确定位",
                "status": "done" if has_rank else ("partial" if has_score else "missing"),
                "summary": "已使用用户提供位次定位。" if has_rank else "当前只有分数，先按分数粗筛；补一分一段表后可自动换算位次。",
                "input": pick(profile, ["province", "province_id", "category", "score", "rank"]),
                "output": rank_output,
                "evidence": [],
            },
            {
                "id": "等位分",
                "title": "换算等位分",
                "status": {"ok": "done", "partial": "partial", "missing": "blocked"}.get(eq_status, "blocked"),
                "summary": equivalent_scores.get("message", "等位分数据不可用。"),
                "input": {"rank": profile.get("rank"), "score": profile.get("score"), "years": [2025, 2024, 2023]},
                "output": eq_output,
                "evidence": [
                    {
                        "year": row.get("year"),
                        "equivalent_score": row.get("equivalent_score"),
                        "cumulative_rank": row.get("cumulative_rank"),
                        "same_score_count": row.get("same_score_count"),
                        "source": row.get("source_type"),
                    }
                    for row in eq_rows
                ],
                "blocking_reason": equivalent_scores.get("message") if eq_blocking else "",
            },
            {
                "id": "筛院校",
                "title": "筛选院校范围",
                "status": downstream_status or ("done" if recs else "empty"),
                "summary": "缺少位次且等位分不可用，先不生成候选范围。" if hard_blocking else f"按位次主锚点和等位分辅助窗口生成 {len(recs)} 个候选；来源和质量标记保留在每条证据里。",
                "input": {"score_window": candidate_pool.get("score_window"), "rank_window": candidate_pool.get("rank_window")},
                "output": pick(candidate_pool, ["total_recommendations", "school_count", "major_count"]),
                "evidence": candidate_pool.get("top_evidence", []),
                "blocking_reason": equivalent_scores.get("message") if hard_blocking else "",
            },
            {
                "id": "冲稳保",
                "title": "确定冲稳保策略",
                "status": downstream_status or ("done" if recs else "empty"),
                "summary": "缺少位次且等位分不可用，冲稳保划分暂不可靠。" if hard_blocking else self.bucket_line(recommendation.get("summary", {})),
                "input": {"risk_model": strategy.get("risk_model")},
                "output": strategy.get("bucket_counts", {}),
                "evidence": strategy.get("rules", []),
                "blocking_reason": equivalent_scores.get("message") if hard_blocking else "",
            },
            {
                "id": "排序志愿",
                "title": "排序志愿",
                "status": downstream_status or ("partial" if recs else "empty"),
                "summary": "缺少位次且等位分不可用，暂不排序。" if hard_blocking else "已叠加风险、专业、城市、学校层次、来源优先级做效用排序。",
                "input": strategy.get("profile_factors", {}),
                "output": {"ordered_count": len(recs), "top": [r.get("school_name") for r in recs[:5]]},
                "evidence": strategy.get("sort_weights", []),
                "blocking_reason": equivalent_scores.get("message") if hard_blocking else "",
            },
            {
                "id": "章程核验",
                "title": "检查招生章程",
                "status": downstream_status or ("pending_web" if recs else "empty"),
                "summary": "缺少位次且等位分不可用，暂不生成章程核验清单。" if hard_blocking else ("已生成待核验清单；需要联网搜索学校招生章程确认选科、单科、体检、学费、校区。" if recs else "没有候选结果，暂不生成章程核验清单。"),
                "input": {"candidate_count": len(recs)},
                "output": {"check_count": min(12, len({(r.get("school_name"), r.get("sp_name")) for r in recs}))},
                "evidence": ["选科要求", "单科成绩", "体检限制", "学费", "校区", "招生章程年份"],
                "blocking_reason": equivalent_scores.get("message") if hard_blocking else "",
            },
        ]

    def build_batch_control_lines(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not self.batch_lines:
            return {
                "ready": False,
                "lines": [],
                "warnings": ["省控线服务未初始化。"],
            }
        return self.batch_lines.for_profile(profile, year=2025)

    def build_equivalent_scores(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not self.score_segments:
            return {
                "status": "missing",
                "province": profile.get("province", ""),
                "category": profile.get("category", ""),
                "rank": profile.get("rank"),
                "score": profile.get("score"),
                "years": [],
                "missing_years": [2025, 2024, 2023],
                "message": "一分一段服务未初始化，无法换算等位分。",
                "blocking": True,
            }
        return self.score_segments.build_equivalent_scores(
            province=profile.get("province", ""),
            province_id=int(profile.get("province_id") or 0) or None,
            category=profile.get("category", ""),
            rank=int(profile.get("rank") or 0) or None,
            score=int(profile.get("score") or 0) or None,
            years=[2025, 2024, 2023],
        )

    @staticmethod
    def bucket_recommendations(recs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        buckets: dict[str, list[dict[str, Any]]] = {"冲": [], "稳": [], "保": []}
        for rec in recs:
            buckets.setdefault(rec.get("tag", "其他"), []).append(rec)
        return buckets

    @staticmethod
    def evidence_summary(recs: list[dict[str, Any]]) -> dict[str, Any]:
        sources = Counter()
        years = Counter()
        for rec in recs:
            source = rec.get("source_type") or rec.get("source") or "unknown"
            sources[source] += 1
            if rec.get("year"):
                years[str(rec["year"])] += 1
        return {
            "source_counts": dict(sources),
            "year_counts": dict(sorted(years.items(), reverse=True)),
        }

    @staticmethod
    def candidate_pool_summary(
        recs: list[dict[str, Any]],
        evidence: dict[str, Any],
        equivalent_scores: dict[str, Any],
        planning_blocked: bool = False,
    ) -> dict[str, Any]:
        if planning_blocked:
            return {
                "total_recommendations": 0,
                "school_count": 0,
                "major_count": 0,
                "evidence": evidence,
                "locked_reason": equivalent_scores.get("message", ""),
            }
        school_count = len({r.get("school_name") for r in recs if r.get("school_name")})
        major_count = len({r.get("sp_name") for r in recs if r.get("sp_name")})
        eq_scores = [int(row["equivalent_score"]) for row in equivalent_scores.get("years", []) if row.get("equivalent_score")]
        score_window = {
            "low": min(eq_scores) - 30,
            "high": max(eq_scores) + 20,
            "rule": "等位分上浮20分、下探30分",
        } if eq_scores else None
        rank = equivalent_scores.get("rank")
        rank_window = {
            "chong_min": max(1, int(rank * 0.75)),
            "bao_max": int(rank * 1.90),
            "rule": "历史位次约 0.75x-1.90x 候选窗口",
        } if rank else None
        return {
            "total_recommendations": len(recs),
            "school_count": school_count,
            "major_count": major_count,
            "evidence": evidence,
            "score_window": score_window,
            "rank_window": rank_window,
            "top_evidence": [
                {
                    "school": r.get("school_name"),
                    "major": r.get("sp_name"),
                    "year": r.get("source_year"),
                    "score": r.get("source_score"),
                    "rank": r.get("source_rank"),
                    "bucket": r.get("tag"),
                }
                for r in recs[:8]
            ],
        }

    @staticmethod
    def strategy_summary(
        profile: dict[str, Any],
        buckets: dict[str, list[dict[str, Any]]],
        equivalent_scores: dict[str, Any],
        planning_blocked: bool = False,
    ) -> dict[str, Any]:
        if planning_blocked:
            return {
                "risk_model": "locked_until_rank_or_equivalent_score",
                "bucket_counts": {"冲": 0, "稳": 0, "保": 0},
                "notes": ["缺少用户位次，且等位分没有换算出来，候选池和冲稳保都不能作为有效方案。"],
                "profile_factors": {},
            }
        return {
            "risk_model": "historical_interval",
            "bucket_counts": {key: len(value) for key, value in buckets.items()},
            "rules": [
                "冲：历史录取位次略优于用户，或历史分数高于等位分 0-20 分。",
                "稳：历史录取位次与用户基本匹配，或等位分高于历史线 0-30 分。",
                "保：历史录取位次明显低于用户，或等位分高于历史线 30-70 分。",
            ],
            "sort_weights": [
                "录取安全：冲/稳/保和位次差",
                "专业匹配：专业关键词命中",
                "城市匹配：偏好城市命中",
                "学校层次：985/211/双一流等标签",
                "来源质量：官方/聚合/开源优先级",
            ],
            "notes": [
                "冲的学校要确认专业组里没有完全不能接受的专业。",
                "稳是主力，不要只看学校名，要看专业和城市。",
                "保底要足够保守，尤其本科/专科临界分数段。",
            ] + profile_advice(profile),
            "profile_factors": {
                "goal": profile.get("goal", ""),
                "family": profile.get("family", ""),
                "region": profile.get("region", []),
                "major_interest": profile.get("major_interest", []),
            },
        }

    @staticmethod
    def volunteer_order(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered = []
        for index, rec in enumerate(recs, start=1):
            ordered.append(
                {
                    "order": index,
                    "tag": rec.get("tag", ""),
                    "school_name": rec.get("school_name", ""),
                    "major_name": rec.get("sp_name", ""),
                    "city": rec.get("city", ""),
                    "source_year": rec.get("source_year"),
                    "source_score": rec.get("source_score"),
                    "source_rank": rec.get("source_rank"),
                    "equivalent_score": rec.get("equivalent_score"),
                    "score_gap": rec.get("score_gap"),
                    "rank_gap": rec.get("rank_gap"),
                    "plan_score": rec.get("plan_score"),
                    "source": rec.get("source", ""),
                    "evidence_level": rec.get("evidence_level", {}),
                    "reason": build_reason(rec),
                }
            )
        return ordered

    @staticmethod
    def charter_checks(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen = set()
        checks = []
        for rec in recs[:12]:
            key = (rec.get("school_name"), rec.get("sp_name"))
            if key in seen:
                continue
            seen.add(key)
            checks.append(
                {
                    "school_name": rec.get("school_name", ""),
                    "major_name": rec.get("sp_name", ""),
                    "status": "pending_web_check",
                    "must_check": ["选科要求", "单科成绩", "体检限制", "学费", "校区", "招生章程年份"],
                    "source_hint": f"{rec.get('school_name', '')} 本科招生网 招生章程",
                    "search_url": charter_search_url(rec.get("school_name", "")),
                }
            )
        return checks

    @staticmethod
    def quality_warnings(
        profile: dict[str, Any],
        recommendation: dict[str, Any],
        equivalent_scores: dict[str, Any],
        batch_control_lines: dict[str, Any],
    ) -> list[str]:
        warnings = list(recommendation.get("quality_warnings") or [])
        if not profile.get("rank"):
            warnings.append("当前没有用户位次；只用分数会受年份难度影响，建议补全省位次。")
        if equivalent_scores.get("status") != "ok":
            warnings.append(equivalent_scores.get("message", "等位分数据不完整。"))
        warnings.extend(batch_control_lines.get("warnings") or [])
        warnings.append("招生章程核验尚未接入联网工具，最终填报前必须人工核对学校官方章程。")
        return warnings

    @staticmethod
    def bucket_line(summary: dict[str, Any]) -> str:
        return f"冲 {summary.get('chong', 0)} 个，稳 {summary.get('wen', 0)} 个，保 {summary.get('bao', 0)} 个。"


def build_reason(rec: dict[str, Any]) -> str:
    bits = []
    if rec.get("tag"):
        bits.append(f"{rec['tag']}档")
    if rec.get("source_year"):
        bits.append(f"参考 {rec['source_year']} 年")
    if rec.get("source_rank"):
        bits.append(f"历史位次 {rec['source_rank']}")
    if rec.get("source_score"):
        bits.append(f"历史分数 {rec['source_score']}")
    if rec.get("equivalent_score"):
        bits.append(f"等位分 {rec['equivalent_score']}")
    if rec.get("rank_gap") is not None:
        bits.append(f"位次差 {rec['rank_gap']}")
    elif rec.get("score_gap") is not None:
        bits.append(f"分差 {rec['score_gap']}")
    if rec.get("source"):
        bits.append(rec["source"])
    return "；".join(str(x) for x in bits if x)


def pick(data: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: data.get(key) for key in keys if key in data}


def profile_advice(profile: dict[str, Any]) -> list[str]:
    goal = str(profile.get("goal") or "")
    majors = " ".join(str(x) for x in profile.get("major_interest") or [])
    family = str(profile.get("family") or "")
    advice = []
    if "就业" in goal:
        advice.append("就业优先时，专业和城市产业比单纯学校名更重要。")
    if "考公" in goal or "稳定" in goal:
        advice.append("稳定/考公优先时，优先核对法学、财会、汉语言、师范、医学等方向和岗位匹配度。")
    if "深造" in goal:
        advice.append("深造优先时，学校层次、学科平台和保研/考研氛围权重应提高。")
    if any(word in majors for word in ("计算机", "电子", "电气", "自动化")):
        advice.append("工科热门专业要重点看专业组内调剂风险、校区和培养方向，避免只看专业大类名称。")
    if any(word in family for word in ("电力", "铁路", "医院", "教师", "体制")):
        advice.append("家庭资源明确时，可以把行业院校和对应专业上调，但仍要保留足够保底。")
    return advice


def charter_search_url(school_name: str) -> str:
    from urllib.parse import quote

    query = f"{school_name} 本科招生网 2026 招生章程"
    return "https://www.bing.com/search?q=" + quote(query)


PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]

COMMON_CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "武汉", "成都",
    "重庆", "西安", "天津", "长沙", "郑州", "济南", "青岛", "合肥", "福州",
    "厦门", "南昌", "石家庄", "沈阳", "大连", "哈尔滨", "长春", "昆明",
    "贵阳", "南宁", "海口", "兰州", "银川", "西宁", "乌鲁木齐",
]
