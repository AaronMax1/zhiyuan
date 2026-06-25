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
        self.timeout = 120.0

    @property
    def status(self) -> LLMStatus:
        return LLMStatus(False, "", "", "LLM key is configured only in the browser and sent per request")

    def summarize_plan(self, plan: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.analyze_step("overview", plan, config)

    def analyze_step(
        self,
        step: str,
        plan: dict[str, Any],
        config: dict[str, Any] | None = None,
        filter_context: str = "",
    ) -> dict[str, Any]:
        runtime = self._runtime_config(config or {})
        if not runtime["api_key"]:
            return self.rule_step_summary(step, plan)
        if not runtime["base_url"] or not runtime["model"]:
            fallback = self.rule_step_summary(step, plan)
            fallback["mode"] = "rule_fallback_after_llm_config_missing"
            fallback["error"] = "LLM Base URL or model is missing"
            return fallback
        prompt = build_step_prompt(step, plan, filter_context=filter_context)
        payload = {
            "model": runtime["model"],
            "messages": [
                {
                    "role": "system",
                    "content": build_analysis_system_prompt(step),
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

    def chat_step(
        self,
        step: str,
        plan: dict[str, Any],
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime = self._runtime_config(config or {})
        user_message = str(user_message or "").strip()
        if not user_message:
            return {"mode": "rule", "ready": False, "step": step, "summary": "请先输入你想追问的问题。"}
        if not runtime["api_key"] or not runtime["base_url"] or not runtime["model"]:
            return self.rule_chat_reply(step, plan, user_message)
        messages = [
            {
                "role": "system",
                "content": build_skill_system_prompt(),
            },
            {
                "role": "user",
                "content": build_chat_context_prompt(step, plan),
            },
        ]
        for item in compact_chat_history(history or [], max_items=20, max_total_chars=12000):
            role = "assistant" if item.get("role") == "assistant" else "user"
            content = str(item.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        payload = {
            "model": runtime["model"],
            "messages": messages,
            "temperature": 0.35,
            "max_tokens": 1400,
        }
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
            return {"mode": "llm", "ready": True, "model": runtime["model"], "step": step, "summary": content}
        except (Exception, socket.timeout, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            fallback = self.rule_chat_reply(step, plan, user_message)
            fallback["mode"] = "rule_fallback_after_llm_error"
            fallback["error"] = str(exc)
            return fallback

    def chat_major_direction(
        self,
        context: dict[str, Any],
        user_message: str,
        history: list[dict[str, Any]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        runtime = self._runtime_config(config or {})
        user_message = str(user_message or "").strip()
        if not user_message:
            return {"mode": "rule", "ready": False, "summary": "请先输入你想讨论的专业方向问题。"}
        if not runtime["api_key"] or not runtime["base_url"] or not runtime["model"]:
            raise RuntimeError("专业方向咨询需要先配置 AI Base URL、Model 和 Key。")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是河北高考志愿专业方向顾问，只帮助确定专业方向，不做院校排序。"
                    "只能基于上下文里的科类、层次、目标、已选专业和可选专业讨论，不要编造不存在的专业。"
                    "必须用多专家视角回答：产业趋势、就业/薪酬、考公考编、升学深造、家庭预算与风险。"
                    "对未来5-10年趋势要给方向和风险，不要只列热门词。"
                    "最后给可执行清单：优先选、谨慎选、除非强兴趣否则不建议。"
                    "回答控制在900字以内。"
                ),
            },
            {
                "role": "user",
                "content": "下面是专业选择上下文：\n" + json.dumps(compact_major_context(context), ensure_ascii=False),
            },
        ]
        for item in compact_chat_history(history or [], max_items=20, max_total_chars=12000):
            role = "assistant" if item.get("role") == "assistant" else "user"
            content = str(item.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})
        payload = {
            "model": runtime["model"],
            "messages": messages,
            "temperature": 0.35,
            "max_tokens": 900,
            "reasoning_effort": "low",
        }
        started_at = __import__("time").time()
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
            return {"mode": "llm", "ready": True, "model": runtime["model"], "summary": content}
        except (Exception, socket.timeout, TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            elapsed = __import__("time").time() - started_at
            raise RuntimeError(f"专业方向 AI 调用失败：{exc}（耗时 {elapsed:.1f}s，timeout {runtime['timeout']:.0f}s）") from exc

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

    def rule_chat_reply(self, step: str, plan: dict[str, Any], user_message: str) -> dict[str, Any]:
        base = self.rule_step_summary(step, plan)["summary"]
        recommendations = compact_recommendations(plan, 8)
        top = "；".join(
            f"{item.get('tag')}-{item.get('school_name')}-{item.get('major_name')}({item.get('rank') or '-'}位)"
            for item in recommendations[:5]
        )
        lines = [
            "当前未配置可用 AI，先按规则给你一个答复。",
            base,
            f"你问的是：{user_message}",
        ]
        if top:
            lines.append(f"可先围绕这些候选继续比较：{top}。")
        lines.append("下一步建议：优先核对分数位次是否一致、硬条件过滤是否符合本人情况、保底是否足够、候选是否近三年稳定，以及未匹配计划项是否需要人工核对。")
        return {"mode": "rule", "ready": False, "model": "", "step": step, "summary": "\n\n".join(lines)}


def build_step_prompt(step: str, plan: dict[str, Any], filter_context: str = "") -> str:
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
        "raw_recommendations": compact_recommendations(plan, 180 if step == "candidate_pool" else 15, use_full_pool=(step == "candidate_pool")),
        "major_keyword_guardrails": major_keyword_guardrails(plan),
        "charter_checks": (plan.get("charter_checks") or [])[:12],
        "quality_warnings": plan.get("quality_warnings"),
    }
    if step == "candidate_pool" and filter_context:
        slim["user_followup_context"] = filter_context[:4000]
    return (
        instructions
        + "\n要求：必须引用已有数据，不要新增 JSON 里没有的学校、专业、分数、位次；"
        "结论按 3-6 条短条目输出，最后列出下一步动作。\n\n"
        + json.dumps(slim, ensure_ascii=False)
    )


def build_skill_system_prompt() -> str:
    return (
        CONSULTANT_SKILL_PROMPT
        + "\n聊天规则：先回答用户本轮问题，再指出关键取舍；不要每轮重复复述完整画像。"
        "每次回答不超过6条要点，最后给一个可执行下一步。"
    )


def build_analysis_system_prompt(step: str) -> str:
    extra = ""
    if step == "candidate_pool":
        extra = "第3步必须优先完成筛选，输出必须是合法 JSON，不要输出 Markdown。"
    elif step == "strategy":
        extra = "第4步重点检查冲稳保结构和保底厚度。"
    elif step == "order":
        extra = "第5步重点解释排序取舍，不要新增候选。"
    elif step == "charter":
        extra = "第6步重点列出必须人工核验的招生章程风险，不能声称已经核验完成。"
    return CONSULTANT_SKILL_PROMPT + "\n分析规则：" + extra


CONSULTANT_SKILL_PROMPT = (
    "你是面向河北考生、报考全国院校的高考志愿填报顾问。"
    "你的任务不是替用户拍板，而是把学校、专业、城市、家庭资源、就业路径和风险边界讲清楚。\n"
    "咨询方法论："
    "1. 位次优先，分数只做辅助；先看用户给出的河北位次，再看等位分和历年录取。"
    "2. 普通家庭更重视就业确定性、专业壁垒、行业入口和试错成本；有明确家庭资源时，要把资源是否能接住专业路径说清楚。"
    "3. 理工方向重点看专业壁垒、学校行业认可、城市实习和产业机会；历史/文科方向重点看学校平台、城市资源、考公考编/法学/师范等路径约束。"
    "4. 医学、师范、电气、铁路、法学、计算机、电子信息等方向要讲清培养周期、地域绑定、资格证/考研/就业门槛。"
    "5. 可以借鉴张雪峰式关注点：就业结果、城市产业、学校层次、专业壁垒、家庭资源、预算和调剂风险；但不要冒充任何真人，不要用夸张营销话术或冒犯用户的表达。\n"
    "硬规则："
    "1. 只能基于用户给出的 plan JSON、候选池和系统数据讨论，不能编造学校、专业、分数、位次、计划数、学费或章程结论。"
    "2. 如果用户要求新增候选，只能建议回到第3步扩大条件、调整偏好，或等待数据库/2026计划补齐。"
    "3. 河北当前主数据是历年录取，一志愿优先；征集志愿不能当常规志愿依据。"
    "4. 当前已接入河北考试院2026普通本科批/专科批招生计划；plan_count、tuition、duration、subject_requirement、plan_match_status 可作为官方计划依据。"
    "5. 如果 plan_match_status 不是 official_matched 开头，要明确提示该候选的2026计划仍需人工核对；不能把缺失字段编出来。"
    "6. 必须提示分数位次一致性、近三年稳定性、保底是否充足、专业调剂和招生章程核验风险。"
    "6. 输出中文，直接给可执行结论；如果数据不足，明确说缺什么。"
)


def build_chat_context_prompt(step: str, plan: dict[str, Any]) -> str:
    step_names = {
        "candidate_pool": "第3步：筛选院校范围",
        "strategy": "第4步：确定冲稳保策略",
        "order": "第5步：排序志愿",
        "charter": "第6步：检查招生章程",
    }
    slim = {
        "current_step": step_names.get(step, step),
        "profile": plan.get("profile"),
        "score_rank_check": plan.get("score_rank_check"),
        "equivalent_scores": plan.get("equivalent_scores"),
        "candidate_pool": plan.get("candidate_pool"),
        "strategy": plan.get("strategy"),
        "top_recommendations": compact_recommendations(plan, 30),
        "plan_coverage": (plan.get("data_scope") or {}).get("plan_coverage"),
        "quality_warnings": plan.get("quality_warnings"),
        "charter_checks": (plan.get("charter_checks") or [])[:12],
    }
    return (
        "下面是当前志愿方案上下文。后续对话都必须基于这些数据；不要新增不存在的学校或专业。\n"
        + json.dumps(slim, ensure_ascii=False)
    )


def build_candidate_filter_instruction() -> str:
    return (
        "你负责第3步：确认专业与院校范围。数据库已经召回候选池，你只能从 raw_recommendations 中选择，"
        "绝对不能新增学校或专业。请按用户目标、专业关键词、城市偏好、就业导向、家庭资源、预算限制、"
        "近三年稳定性、硬条件过滤结果、2026官方计划数/学费/选科匹配状态、冲稳保比例和保底厚度筛选。"
        "如果用户没有选择专业偏好，不要认为信息缺失；应先根据核心诉求、选科大类、家庭资源、城市偏好和候选池实际专业，归纳2-4个适合方向，再从中筛选。"
        "硬条件过滤已经剔除的候选不能重新加入；如果用户想恢复，需要提示回到表单修改限制条件。"
        "必须遵守 major_keyword_guardrails：历史/文科候选里的电子商务、商务英语、管理科学等不能被解释成工科计算机、电子信息、电子科学或通信类。"
        "如果 raw_recommendations 没有真正匹配用户专业关键词的专业，要明确说明专业池缺口，再选择可接受的替代方向或建议调整选科/专业偏好。"
        "筛选结果要给后续冲稳保、排序和章程核验使用；不要只选冲，也不要只选名气大的学校。"
        "如果用户后续对话提出新偏好，必须按 user_followup_context 重新调整保留项。"
        "必须输出 JSON 对象，格式："
        "{\"summary\":\"中文总结\","
        "\"keep_keys\":[\"候选key\"],"
        "\"drop\":[{\"key\":\"候选key\",\"reason\":\"剔除原因\"}],"
        "\"warnings\":[\"风险提醒\"]}。"
        "keep_keys 建议保留 20-40 个；若候选本来少，可全部保留。"
    )


def compact_chat_history(history: list[dict[str, Any]], max_items: int = 20, max_total_chars: int = 12000) -> list[dict[str, str]]:
    compact: list[dict[str, str]] = []
    total = 0
    for item in reversed(history[-max_items:]):
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        content = content[:600]
        if total + len(content) > max_total_chars:
            remaining = max_total_chars - total
            if remaining <= 200:
                break
            content = content[:remaining]
        compact.append({"role": role, "content": content})
        total += len(content)
        if total >= max_total_chars:
            break
    return list(reversed(compact))


def rule_major_direction_reply(context: dict[str, Any], user_message: str) -> str:
    profile = context.get("profile") or {}
    category = str(profile.get("category") or "")
    goal = str(profile.get("goal") or "")
    selected = context.get("selected_majors") or []
    options = context.get("major_options") or []
    option_names = [str(item.get("name") or "") for item in options if item.get("name")]
    category_text = category or "未指定科类"
    goal_text = goal or "未指定"
    is_history = "历史" in category or "文科" in category
    is_physics = "物理" in category or "理科" in category
    trend_groups = major_trend_groups(is_history, is_physics, option_names)
    lines = [f"先说明：当前没有配置可用的大模型，所以这是本地专家规则版回复。当前科类：{category_text}；目标：{goal_text}。"]
    if selected:
        lines.append("你已选择：" + "、".join(str(x) for x in selected[:12]) + "。下面会判断这些方向是否值得继续保留。")
    else:
        lines.append("你还没选择专业。建议先从下面的“优先选”里选 3-6 个，再保留 1-2 个备选方向。")
    lines.extend([
        "",
        "1. 产业趋势专家视角：未来5-10年更看重“AI+行业”的复合能力，而不是单纯专业名字。能和数据、智能化、医疗健康、先进制造、能源、财务合规、公共治理结合的方向更有韧性。",
        "2. 就业/薪酬专家视角：优先选有明确岗位入口、技能可迁移、实习机会多的专业；谨慎选择岗位入口窄、强依赖考研或强依赖学校层次的专业。",
        "3. 考公考编专家视角：如果追求稳定，专业名称要贴近岗位目录。法学、汉语言文学、会计/财务、思想政治/马克思主义、师范类通常比泛管理、电子商务、新闻传播更稳。",
        "4. 升学深造专家视角：未来前景好的专业不等于本科直接好就业。法学、医学、心理、经济金融、部分管理类往往需要考研或证书加持；普通家庭要评估时间成本。",
        "5. 家庭预算与风险专家视角：优先公办、低学费、岗位路径清楚的方向；中外合作、高学费、强销售属性或强地域绑定专业要谨慎。",
        "",
        "优先选：",
    ])
    for group in trend_groups["priority"]:
        lines.append(f"- {group}")
    lines.append("谨慎选：")
    for group in trend_groups["caution"]:
        lines.append(f"- {group}")
    lines.append("除非强兴趣否则不建议优先：")
    for group in trend_groups["avoid"]:
        lines.append(f"- {group}")
    lines.extend([
        "",
        "落地建议：先别问“哪个专业最火”，而是按这三个问题筛：1. 这个专业本科毕业有没有清晰岗位？2. 不考研能不能接受？3. 你的家庭资源、城市和性格能不能接住这条路径？",
        "下一步：在页面里先选 3-8 个专业方向，再回主流程用位次、2026招生计划、学费和保底厚度做院校专业筛选。",
    ])
    return "\n".join(lines)


def compact_major_context(context: dict[str, Any]) -> dict[str, Any]:
    profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
    options = context.get("major_options") if isinstance(context.get("major_options"), list) else []
    selected = context.get("selected_majors") if isinstance(context.get("selected_majors"), list) else []
    return {
        "profile": {
            "category": profile.get("category", ""),
            "education_level": profile.get("education_level", ""),
            "goal": profile.get("goal", ""),
        },
        "selected_majors": [str(item) for item in selected[:20]],
        "major_options": [
            {
                "name": item.get("name", ""),
                "school_count": item.get("school_count", 0),
            }
            for item in options
            if isinstance(item, dict) and item.get("name")
        ],
    }


def major_trend_groups(is_history: bool, is_physics: bool, option_names: list[str]) -> dict[str, list[str]]:
    available = set(option_names)

    def has(name: str) -> bool:
        return not available or name in available

    if is_history:
        priority = []
        for text, names in [
            ("法学：适合考公、律所、企业合规，但普通本科要准备法考/考研，不能只看热度。", ["法学"]),
            ("汉语言文学/汉语国际教育：考公、教师、内容/行政路径清楚，薪资上限取决于城市和平台。", ["汉语言文学", "汉语国际教育"]),
            ("会计学/财务管理/审计学/大数据与会计：企业财务、审计、税务、考公都有入口，AI会替代低端记账但不会替代合规判断。", ["会计学", "财务管理", "审计学", "大数据与会计"]),
            ("师范类：稳定性强，但要看地区教师编制、学科需求和是否愿意长期从教。", ["学前教育", "小学教育", "思想政治教育"]),
            ("护理/康复/医学技术类：人口老龄化支撑需求，但工作强度和职业环境要提前接受。", ["护理", "护理学", "康复治疗学"]),
        ]:
            if any(has(name) for name in names):
                priority.append(text)
        caution = [
            "经济学/金融学/工商管理：不是不能选，但普通本科就业分化大，最好绑定财会、数据分析、考公或家里资源。",
            "新闻传播/网络与新媒体：内容行业变化快，要有写作、运营、视频、数据工具能力，不能只靠专业名称。",
            "英语/商务英语：单纯语言优势下降，必须叠加法律、外贸、教育、跨境电商或技术文档方向。",
        ]
        avoid = [
            "电子商务：名字听起来新，但本科岗位常偏运营/销售，专业壁垒不如财会、法学、计算机类清晰。",
            "旅游管理/酒店管理/会展：周期性和服务属性强，除非热爱行业或有明确资源。",
            "市场营销/人力资源管理：就业入口宽但替代性强，更依赖个人能力和实习平台。",
        ]
        return {"priority": priority or ["法学、汉语言、财会、师范、护理/康复里先选 3-5 个可接受方向。"], "caution": caution, "avoid": avoid}

    if is_physics:
        priority = [
            "计算机科学与技术/软件工程/数据科学：AI时代不是过时，而是要求更高；适合数学和自学能力强的学生。",
            "电子信息/通信/集成电路/自动化：先进制造、智能硬件、车企、通信和半导体长期有需求，但课程硬。",
            "电气工程及其自动化：电网、电力设备、新能源方向稳定性较强，适合追求确定性的家庭。",
            "医学类/口腔/临床/医学影像：人口老龄化支撑长期需求，但培养周期长、读研和规培成本高。",
            "机械/车辆/智能制造：传统名称不等于没前景，关键看是否能叠加自动化、机器人、新能源车和工业软件。",
        ]
        caution = [
            "土木工程：基建地产周期变化后分化明显，除非学校平台、地区机会或细分方向很明确。",
            "生物工程/生物科学：前沿但本科就业入口窄，通常需要深造。",
            "金融工程/经济统计：数学要求高，就业更看学校层次、城市和实习。",
        ]
        avoid = [
            "管理科学、信息管理等泛交叉专业：名字像技术，但岗位常不如计算机/电子信息明确。",
            "环境工程/材料类：不是没有前景，但本科就业和薪资分化大，适合能接受读研的人。",
            "小计划、学费高、名称很新但岗位不清楚的专业：先放备选，不要当主线。",
        ]
        return {"priority": priority, "caution": caution, "avoid": avoid}

    return {
        "priority": ["先确定物理/历史大类，再比较对应专业。没有科类时只能给宽泛建议。"],
        "caution": ["综合类选择要避免只看热门名称，要回到岗位入口、学费、城市和升学成本。"],
        "avoid": ["岗位入口不清、学费高、自己不了解课程内容的专业不要优先。"],
    }


def compact_recommendations(plan: dict[str, Any], limit: int, use_full_pool: bool = False) -> list[dict[str, Any]]:
    recommendation = plan.get("recommendation", {})
    if use_full_pool:
        rows = recommendation.get("candidate_pool_recommendations") or recommendation.get("recommendations") or []
    else:
        rows = recommendation.get("recommendations") or []
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
            "stability": item.get("stability"),
            "plan_count": item.get("plan_count"),
            "tuition": item.get("tuition_text"),
            "duration": item.get("duration"),
            "subject_requirement": item.get("subject_requirement"),
            "plan_match_status": item.get("plan_match_status"),
            "hard_filter_reasons": item.get("hard_filter_reasons"),
        })
    return compact


def major_keyword_guardrails(plan: dict[str, Any]) -> dict[str, Any]:
    profile = plan.get("profile") or {}
    category = str(profile.get("category") or "")
    keywords = profile.get("major_interest") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    rows = (plan.get("recommendation") or {}).get("candidate_pool_recommendations") or []
    majors = [str(item.get("sp_name") or item.get("major_name") or "") for item in rows]
    true_cs_keywords = ("计算机", "软件工程", "网络工程", "人工智能", "数据科学", "信息安全")
    true_electronic_keywords = ("电子信息", "电子科学", "通信工程", "微电子", "集成电路", "光电信息", "信息工程")
    has_true_cs = any(any(word in major for word in true_cs_keywords) for major in majors)
    has_true_electronic = any(any(word in major for word in true_electronic_keywords) for major in majors)
    ambiguous = [major for major in majors if any(word in major for word in ("电子商务", "商务英语", "管理科学"))][:20]
    notes = []
    if any(word in category for word in ("历史", "文科")):
        notes.append("当前是历史/文科大类，不能按物理工科计算机/电子信息口径解释候选。")
    if any("计算机" in str(keyword) for keyword in keywords) and not has_true_cs:
        notes.append("候选池未提供真正的计算机类专业，不能把管理/商务/外语类专业包装成计算机。")
    if any("电子" in str(keyword) for keyword in keywords) and not has_true_electronic:
        notes.append("候选池未提供真正的电子信息/通信/微电子类专业，电子商务不等于电子信息。")
    return {
        "category": category,
        "user_keywords": keywords,
        "has_true_computer_major": has_true_cs,
        "has_true_electronic_info_major": has_true_electronic,
        "ambiguous_major_examples": ambiguous,
        "notes": notes,
    }


def parse_candidate_filter(content: str, plan: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"structured": False}
    recommendation = plan.get("recommendation", {})
    rows = recommendation.get("candidate_pool_recommendations") or recommendation.get("recommendations") or []
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
