"""Evidence grading helpers for admissions and planning outputs."""

from __future__ import annotations

from typing import Any


LEVELS = {
    "A": "省考试院/学校官方数据",
    "B": "学校官网/官方招生网",
    "C": "权威教育媒体或公共服务平台",
    "D": "第三方聚合数据",
    "E": "开源快照、OCR或模型推理",
}


def evidence_level_for_record(item: dict[str, Any]) -> dict[str, str]:
    source_type = str(item.get("source_type") or item.get("source") or "")
    source_trust = str(item.get("source_trust_level") or "")
    source = str(item.get("source") or "")
    flags = item.get("quality_flags") or item.get("evidence", {}).get("quality_flags") or []
    if isinstance(flags, str):
        flags_text = flags
    else:
        flags_text = ",".join(str(flag) for flag in flags)

    if source_type == "official" or source_trust == "official":
        level = "A"
    elif "招生网" in source or "官网" in source:
        level = "B"
    elif source_type in {"aggregate", "gaokao_advisor"}:
        level = "C" if source_type == "gaokao_advisor" else "D"
    elif source_type in {"open_source", "local_vision_dxsbb_2025"}:
        level = "E"
    else:
        level = "D"

    if "low_trust_source" in flags_text and level in {"A", "B", "C", "D"}:
        level = "E" if source_type == "open_source" else "D"
    if "third_party_aggregate_source" in flags_text and level in {"A", "B", "C"}:
        level = "D"

    return {
        "level": level,
        "label": f"{level}级证据",
        "description": LEVELS[level],
    }


def attach_evidence_level(item: dict[str, Any]) -> dict[str, Any]:
    grade = evidence_level_for_record(item)
    item["evidence_level"] = grade
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        evidence["level"] = grade["level"]
        evidence["level_label"] = grade["label"]
        evidence["level_description"] = grade["description"]
    return item
