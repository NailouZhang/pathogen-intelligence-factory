from __future__ import annotations

import copy
import re
from typing import Any

from .utils import clean_space

PRIVATE_KEYS = {
    "source_status", "review_vocabulary_lifecycle", "wechat_content_budget",
    "analysis_quality", "retrieval_funnel_audit", "content_audit", "llm_attempts",
    "relevance_review_cache_key", "relevance_review_stop_reason", "_wechat_budget",
    "_wechat_field_limits", "selection_policy_explanation", "qualification_notice",
    "evidence_boundary", "translation_fallback_notice", "wechat_budget_notice",
}

OPERATIONAL_PATTERNS = tuple(re.compile(p, re.I) for p in (
    r"完整资格清单", r"Top\s*50.*(?:删除阈值|深度主报告)", r"完整审计.*data/audit",
    r"公众号正文.*(?:精简|篇幅)", r"微信公众号篇幅说明", r"显示长度保护",
    r"精简末位", r"省略补充", r"未在正文展开", r"极端篇幅兜底",
    r"证据边界\s*[:：]", r"translation_status", r"english_fallback",
    r"中文翻译状态不影响", r"资格由来源、日期", r"本期新闻资格由来源、日期、正文身份",
    r"本期重点文献按发表日期", r"研究结果与综述证据分别核验",
    r"部分证据来自摘要、观察性研究", r"结论应结合研究设计和证据等级",
    r"中文字段不可用时", r"中文翻译不完整时会以英文证据",
    r"selection policy", r"Top 50 means", r"complete qualification", r"full audit", r"character limit",
    r"fallback.*translation", r"evidence boundary", r"ranked by publication date.*relevance.*evidence",
    r"translation completeness.*does not affect", r"English evidence.*Chinese display",
))


def is_operational_text(value: Any) -> bool:
    text = clean_space(value)
    return bool(text and any(pattern.search(text) for pattern in OPERATIONAL_PATTERNS))


def sanitize_public_text(value: Any) -> str:
    text = clean_space(value)
    if not text:
        return ""
    parts = re.split(r"(?<=[。！？!?])\s+|(?<=\.)\s+(?=[A-Z])", text)
    kept = [part.strip() for part in parts if part.strip() and not is_operational_text(part)]
    return clean_space(" ".join(kept))


def _sanitize(value: Any, key: str = "") -> Any:
    if key in PRIVATE_KEYS or key.startswith("_"):
        return None
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for child_key, child in value.items():
            cleaned = _sanitize(child, str(child_key))
            if cleaned not in (None, "", [], {}):
                output[str(child_key)] = cleaned
        return output
    if isinstance(value, list):
        output = []
        for child in value:
            cleaned = _sanitize(child, key)
            if cleaned not in (None, "", [], {}):
                output.append(cleaned)
        return output
    if isinstance(value, str):
        return sanitize_public_text(value)
    return value


def build_display_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Return the only structure public renderers are allowed to consume."""
    cleaned = _sanitize(copy.deepcopy(issue))
    return cleaned if isinstance(cleaned, dict) else {}
