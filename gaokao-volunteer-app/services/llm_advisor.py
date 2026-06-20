"""Request-scoped LLM advisor for six-step volunteer planning."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMStatus:
    ready: bool
    model: str
    base_url: str
    message: str


class LLMAdvisorService:
    def __init__(self) -> None:
        self.timeout = 60.0

    @property
    def status(self) -> LLMStatus:
        return LLMStatus(False, "", "", "LLM key is configured only in the browser and sent per request")

    def summarize_plan(self, plan: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.analyze_step("overview", plan, config)

    def analyze_step(self, step: str, plan: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime = self._runtime_config(config or {})
        if not runtime["api_key"]:
            return self.rule_step_summary(step, plan)
        if not runtime["base_url"] or not runtime["model"]:
            fallback = self.rule_step_summary(step, plan)
            fallback["mode"] = "rule_fallback_after_llm_config_missing"
            fallback["error"] = "LLM Base URL or model is missing"
            return fallback
        prompt = build_step_prompt(step, plan)
        payload = {
            "model": runtime["model"],
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是高考志愿填报顾问。必须基于用户给定的结构化数据工作，"
                        "不能编造学校、专业、分数、位次、招生章程结论。"
                        "如果数据不足，明确说明缺口。输出中文，直接给可执行结论。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 1200,
        }
        if step == "candidate_pool":
            payload["response_format"] = {"type": "json_object"}
        try:
            req = urllib.request.Request(
                runtime["base_url"] + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {runtime['api_key']}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=runtime["timeout"]) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            result = {"mode": "llm", "ready": True, "model": runtime["model"], "step": step, "summary": content}
            if step == "candidate_pool":
                result.update(parse_candidate_filter(content, plan))
            return result
        except (Exception, socket.timeout, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            fallback = self.rule_step_summary(step, plan)
            fallback["mode"] = "rule_fallback_after_llm_error"
            fallback["error"] = str(exc)
            return fallback

    def _runtime_config(self, config: dict[str, Any]) -> dict[str, Any]:
        api_key = str(config.get("api_key") or config.get("key") or "").strip()
        base_url = str(config.get("base_url") or "").strip().rstrip("/")
        model = str(config.get("model") or "").strip()
        try:
            timeout = float(config.get("timeout") or self.timeout)
        except (TypeError, ValueError):
            timeout = self.timeout
        return {
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
            "timeout": max(3.0, min(timeout, 120.0)),
        }

    def rule_step_summary(self, step: str, plan: dict[str, Any]) -> dict[str, Any]:
        profile = plan.get("profile", {})
        eq = plan.get("equivalent_scores", {})
        strategy = plan.get("strategy", {})
        pool = plan.get("candidate_pool", {})
        order = plan.get("volunteer_order", [])
        checks = plan.get("charter_checks", [])
        province = profile.get("province") or "当前省份"
        category = eq.get("category") or profile.get("category") or ""
        counts = strategy.get("bucket_counts") or {}
        if step == "candidate_pool":
            lines = [
                f"{province}{category}当前候选池 {pool.get('total_recommendations', 0)} 个，覆盖 {pool.get('school_count', 0)} 所学校、{pool.get('major_count', 0)} 个专业。",
                "筛选依据是等位分窗口、位次窗口、专业关键词和城市偏好。若候选少，先放宽专业或城市；若候选太散，优先保留近三年有记录且证据等级更高的项。",
            ]
        elif step == "strategy":
            lines = [
                f"当前冲稳保结构：冲 {counts.get('冲', 0)}、稳 {counts.get('稳', 0)}、保 {counts.get('保', 0)}。",
                "稳应作为主体，保底要覆盖可接受学校和专业；冲的项必须确认专业组内没有无法接受的专业。",
            ]
        elif step == "order":
            top = "、".join(f"{item.get('school_name')}-{item.get('major_name')}" for item in order[:5]) or "暂无"
            lines = [
                f"当前可排序候选 {len(order)} 个，前排候选：{top}。",
                "排序应先按个人偏好，再用风险和证据等级校验；不要只按学校名或分数高低排序。",
            ]
        elif step == "charter":
            lines = [
                f"当前生成 {len(checks)} 条招生章程核验任务。",
                "需要人工核对选科、单科成绩、体检限制、学费、校区和招生章程年份；没有联网实证前不能视为最终结论。",
            ]
        else:
            lines = [
                f"{province}{category}方案已按六步数据链生成。",
                f"当前冲稳保：冲 {counts.get('冲', 0)}、稳 {counts.get('稳', 0)}、保 {counts.get('保', 0)}。",
            ]
        return {"mode": "rule", "ready": False, "model": "", "step": step, "summary": "\n\n".join(lines)}


def build_step_prompt(step: str, plan: dict[str, Any]) -> str:
    instructions = {
        "candidate_pool": build_candidate_filter_instruction(),
        "strategy": (
            "你负责第4步：确定冲稳保策略。请检查冲稳保比例是否合理，"
            "给出需要增加冲/稳/保哪一类，以及风险解释。"
        ),
        "order": (
            "你负责第5步：排序志愿。请基于当前候选、用户目标、专业和城市偏好，"
            "给出排序原则和前几个志愿的排序理由。不要新增候选。"
        ),
        "charter": (
            "你负责第6步：招生章程核验。请生成核验重点、每类风险如何查、"
            "哪些候选最需要先核验。不能声称已经完成章程核验。"
        ),
        "overview": "请基于下面 JSON 生成一段高考志愿顾问总览。",
    }.get(step, "请基于下面 JSON 分析当前步骤。")
    slim = {
        "profile": plan.get("profile"),
        "equivalent_scores": plan.get("equivalent_scores"),
        "candidate_pool": plan.get("candidate_pool"),
        "strategy": plan.get("strategy"),
        "top_recommendations": (plan.get("volunteer_order") or [])[:15],
        "raw_recommendations": compact_recommendations(plan, 80 if step == "candidate_pool" else 15),
        "charter_checks": (plan.get("charter_checks") or [])[:12],
        "quality_warnings": plan.get("quality_warnings"),
    }
    return (
        instructions
        + "\n要求：必须引用已有数据，不要新增 JSON 里没有的学校、专业、分数、位次；"
        "结论按 3-6 条短条目输出，最后列出下一步动作。\n\n"
        + json.dumps(slim, ensure_ascii=False)
    )


def build_candidate_filter_instruction() -> str:
    return (
        "你负责第3步：筛选院校范围。数据库已经召回候选池，你只能从 raw_recommendations 中选择，"
        "绝对不能新增学校或专业。请按用户目标、专业关键词、城市偏好、风险、证据等级筛选。"
        "必须输出 JSON 对象，格式："
        "{\"summary\":\"中文总结\","
        "\"keep_keys\":[\"候选key\"],"
        "\"drop\":[{\"key\":\"候选key\",\"reason\":\"剔除原因\"}],"
        "\"warnings\":[\"风险提醒\"]}。"
        "keep_keys 建议保留 12-30 个；若候选本来少，可全部保留。"
    )


def compact_recommendations(plan: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = plan.get("recommendation", {}).get("recommendations") or []
    compact = []
    for index, item in enumerate(rows[:limit], start=1):
        key = candidate_key(item)
        compact.append({
            "key": key,
            "index": index,
            "school_name": item.get("school_name"),
            "major_name": item.get("sp_name") or item.get("major_name"),
            "city": item.get("city"),
            "tier": item.get("tier"),
            "category": item.get("category"),
            "year": item.get("source_year") or item.get("year"),
            "score": item.get("source_score") or item.get("score"),
            "rank": item.get("source_rank") or item.get("rank_value"),
            "equivalent_score": item.get("equivalent_score"),
            "tag": item.get("tag"),
            "score_gap": item.get("score_gap"),
            "rank_gap": item.get("rank_gap"),
            "evidence_level": (item.get("evidence_level") or {}).get("label"),
        })
    return compact


def parse_candidate_filter(content: str, plan: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"structured": False}
    rows = plan.get("recommendation", {}).get("recommendations") or []
    valid_keys = {candidate_key(item) for item in rows}
    keep_keys = [key for key in parsed.get("keep_keys", []) if key in valid_keys]
    drop = [
        {"key": item.get("key"), "reason": str(item.get("reason") or "")}
        for item in parsed.get("drop", [])
        if isinstance(item, dict) and item.get("key") in valid_keys
    ]
    summary = str(parsed.get("summary") or content)
    warnings = [str(item) for item in parsed.get("warnings", []) if item]
    return {
        "structured": True,
        "summary": summary,
        "keep_keys": keep_keys,
        "drop": drop,
        "warnings": warnings,
    }


def candidate_key(item: dict[str, Any]) -> str:
    parts = [
        item.get("school_name") or "",
        item.get("sp_name") or item.get("major_name") or "",
        str(item.get("source_year") or item.get("year") or ""),
        str(item.get("category") or ""),
    ]
    return "|".join(parts)
