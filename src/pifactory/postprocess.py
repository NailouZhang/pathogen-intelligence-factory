from __future__ import annotations

import re
from typing import Any

from rapidfuzz.fuzz import ratio, token_set_ratio

from .utils import clean_space, split_sentences


INCOMPLETE_MARKERS = (
    "...",
    "……",
    "…",
    "之前关联的替换",
    "previously associated replacement",
    "translation unavailable",
    "翻译暂不可用",
)

BANNED_EDITORIAL_SENTENCES = (
    "无法根据提供的证据可靠地确定主要共识",
    "无法可靠地确定主要共识",
    "现有中文证据不足以形成可靠的跨研究综合结论",
    "输入证据未报告",
    "未能确定主要共识",
    "the main consensus could not be reliably determined",
    "could not be reliably determined",
)

RESEARCH_FIELDS = [
    "research_question_and_background",
    "study_design_and_population",
    "methods",
    "main_results",
    "interpretation_and_novelty",
    "scientific_and_public_health_significance",
    "limitations_and_evidence_strength",
]

REVIEW_FIELDS = [
    "scope_and_question",
    "evidence_base_and_review_method",
    "consensus_and_key_conclusions",
    "controversies_and_evidence_gaps",
    "research_and_practice_implications",
]

NEWS_FIELDS = [
    "time",
    "location_and_population",
    "event",
    "scale_impact_and_risk",
    "response_status_and_uncertainty",
]

FIELD_ROLES = {
    "research_question_and_background": ["objective", "background", "general"],
    "study_design_and_population": ["design_population"],
    "methods": ["methods"],
    "main_results": ["results"],
    "interpretation_and_novelty": ["interpretation", "conclusion"],
    "scientific_and_public_health_significance": ["implications", "conclusion"],
    "limitations_and_evidence_strength": ["limitations"],
    "scope_and_question": ["objective", "background", "general"],
    "evidence_base_and_review_method": ["methods", "design_population"],
    "consensus_and_key_conclusions": ["results", "conclusion"],
    "controversies_and_evidence_gaps": ["limitations", "interpretation"],
    "research_and_practice_implications": ["implications", "conclusion"],
    "time": ["general"],
    "location_and_population": ["general"],
    "event": ["general"],
    "scale_impact_and_risk": ["general"],
    "response_status_and_uncertainty": ["general"],
}

FIELD_DEFAULTS = {
    "research_question_and_background": "原始证据仅说明了研究主题，未明确报告更具体的知识缺口或研究假设。",
    "study_design_and_population": "原始证据未完整报告研究设计、研究地点或研究对象构成。",
    "methods": "原始证据未完整报告实验、检测、统计或计算方法。",
    "main_results": "原始证据未提供可独立核验的主要结果。",
    "interpretation_and_novelty": "原始证据未单独报告作者对结果的解释或创新点。",
    "scientific_and_public_health_significance": "原始证据未单独报告可直接支持的科研或公共卫生启示。",
    "limitations_and_evidence_strength": "当前解读主要依据摘要或可获得的开放正文，未报告的局限不作推断。",
    "scope_and_question": "原始证据仅说明了综述主题，未完整界定综述范围和核心问题。",
    "evidence_base_and_review_method": "原始证据未完整报告数据库、检索日期、纳入标准或证据数量。",
    "consensus_and_key_conclusions": "原始证据未提供足以独立概括的稳定共识，具体观点见文章摘要和下方其他要素。",
    "controversies_and_evidence_gaps": "原始证据未明确列出争议、异质性或证据缺口。",
    "research_and_practice_implications": "原始证据未单独报告可直接转化的研究或实践建议。",
    "time": "报道时间可见于文献元数据，事件发生时间未在正文中明确报告。",
    "location_and_population": "正文未明确报告具体地点或涉及人群。",
    "event": "正文提供了与目标病原相关的事件信息，但确认状态需以原始来源为准。",
    "scale_impact_and_risk": "正文未提供可独立核验的病例规模、影响范围或风险等级。",
    "response_status_and_uncertainty": "正文未完整报告官方应对措施，相关不确定性需以原始来源后续更新为准。",
}


def _semantic_key(text: str) -> str:
    value = clean_space(text).casefold()
    value = re.sub(r"\[[A-Z]\d+(?:\s*,\s*[A-Z]\d+)*\]", "", value)
    value = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value)
    return value


def sentence_similarity(left: str, right: str) -> float:
    a = _semantic_key(left)
    b = _semantic_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 35 and (a in b or b in a):
        return 0.96
    return max(ratio(a, b), token_set_ratio(a, b)) / 100.0


def complete_text(value: Any, *, max_chars: int = 760) -> tuple[str, bool]:
    """Return complete sentences only; never emit an ellipsis or cut a sentence."""
    text = clean_space(value)
    changed = False
    # Known corruption tails are cut at their first occurrence so a complete
    # prefix remains publishable. Ellipsis markers are converted to sentence
    # boundaries rather than removed, preventing adjacent clauses from being
    # fused and then rejected as one contaminated sentence.
    for marker in ("之前关联的替换", "previously associated replacement"):
        if marker in text:
            text = text.split(marker, 1)[0]
            changed = True
    for marker in ("......", "……", "...", "…"):
        if marker in text:
            text = text.replace(marker, "。" if re.search(r"[\u4e00-\u9fff]", text) else ".")
            changed = True
    for marker in ("translation unavailable", "翻译暂不可用"):
        if marker.casefold() in text.casefold():
            text = ""
            changed = True
    text = clean_space(text)
    if not text:
        return "", changed

    # ``utils.split_sentences`` intentionally ignores very short fragments for
    # evidence extraction. Public-text repair must also preserve short complete
    # sentences, so it uses a local punctuation-aware splitter.
    sentence_parts = re.findall(r".+?[.!?。！？](?:\s+|$)|.+$", text)
    sentences = [clean_space(x) for x in sentence_parts if clean_space(x)][:80]
    output: list[str] = []
    used = 0
    for sentence in sentences:
        sentence = sentence.strip(" ,;，；:：")
        if not sentence:
            continue
        if any(phrase.casefold() in sentence.casefold() for phrase in BANNED_EDITORIAL_SENTENCES):
            changed = True
            continue
        if sentence[-1] not in "。.!?！？":
            sentence += "。" if re.search(r"[\u4e00-\u9fff]", sentence) else "."
            changed = True
        if output and used + len(sentence) > max_chars:
            changed = True
            break
        if not output and len(sentence) > max_chars:
            # A single long sentence is retained rather than cut. The caller can
            # ask the LLM to rewrite it on the next run, but the published text
            # remains syntactically complete.
            output.append(sentence)
            break
        output.append(sentence)
        used += len(sentence)
    return clean_space(" ".join(output)), changed


def contains_cross_field_overlap(analysis: dict[str, Any], fields: list[str], threshold: float = 0.88) -> tuple[bool, str]:
    values = {field: clean_space(analysis.get(field)) for field in fields}
    for index, field in enumerate(fields):
        if not values[field]:
            continue
        for other in fields[index + 1:]:
            if not values[other]:
                continue
            if sentence_similarity(values[field], values[other]) >= threshold:
                return True, f"{field} and {other} are substantially duplicated"
            left_sentences = split_sentences(values[field], max_sentences=20)
            right_sentences = split_sentences(values[other], max_sentences=20)
            for left in left_sentences:
                for right in right_sentences:
                    if len(clean_space(left)) >= 28 and sentence_similarity(left, right) >= 0.92:
                        return True, f"{field} and {other} reuse the same sentence"
    return False, "ok"


def _evidence_groups(payload: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for row in payload.get("evidence") or []:
        role = clean_space(row.get("role")) or "general"
        evidence_id = clean_space(row.get("id"))
        text = clean_space(row.get("text"))
        if evidence_id and text:
            groups.setdefault(role, []).append((evidence_id, text))
    return groups


def _replacement_from_evidence(
    field: str,
    groups: dict[str, list[tuple[str, str]]],
    used_sentences: list[str],
    used_evidence: set[str],
) -> tuple[str, list[str]]:
    for role in FIELD_ROLES.get(field, ["general"]):
        for evidence_id, text in groups.get(role, []):
            if evidence_id in used_evidence:
                continue
            candidate, _ = complete_text(text, max_chars=420)
            if not candidate:
                continue
            if any(sentence_similarity(candidate, seen) >= 0.88 for seen in used_sentences):
                continue
            return candidate, [evidence_id]
    default = FIELD_DEFAULTS[field]
    default, _ = complete_text(default, max_chars=420)
    return default, []


def deduplicate_structured_analysis(
    data: dict[str, Any],
    payload: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    """Enforce one rhetorical purpose per field after the LLM returns.

    The prompt remains the first line of defence. This deterministic pass then
    removes exact and near-duplicate sentences, repairs incomplete endings, and
    fills an emptied field only from evidence assigned to that field's role.
    """
    fields = RESEARCH_FIELDS if kind == "research" else REVIEW_FIELDS if kind == "review" else NEWS_FIELDS
    analysis = dict(data.get("analysis") or {})
    evidence_ids = dict(data.get("evidence_ids") or {})
    groups = _evidence_groups(payload)
    used_sentences: list[str] = []
    used_evidence: set[str] = set()
    audit = {
        "removed_duplicate_sentences": 0,
        "repaired_incomplete_fields": [],
        "evidence_replacements": [],
        "policy": "v10-exclusive-rhetorical-fields-1",
    }

    for field in fields:
        original = clean_space(analysis.get(field))
        completed, changed = complete_text(original, max_chars=760 if kind != "news" else 620)
        if changed:
            audit["repaired_incomplete_fields"].append(field)
        unique_sentences: list[str] = []
        for sentence in split_sentences(completed, max_sentences=12):
            sentence, _ = complete_text(sentence, max_chars=760)
            if not sentence:
                continue
            if any(sentence_similarity(sentence, seen) >= 0.88 for seen in used_sentences):
                audit["removed_duplicate_sentences"] += 1
                continue
            if any(sentence_similarity(sentence, kept) >= 0.94 for kept in unique_sentences):
                audit["removed_duplicate_sentences"] += 1
                continue
            unique_sentences.append(sentence)
        value = clean_space(" ".join(unique_sentences))
        refs = [clean_space(x) for x in evidence_ids.get(field) or [] if clean_space(x)]
        if not value or (used_sentences and any(sentence_similarity(value, seen) >= 0.88 for seen in used_sentences)):
            value, refs = _replacement_from_evidence(field, groups, used_sentences, used_evidence)
            audit["evidence_replacements"].append(field)
        analysis[field] = value
        evidence_ids[field] = refs
        used_sentences.extend(split_sentences(value, max_sentences=12))
        used_evidence.update(refs)

    data["analysis"] = analysis
    data["evidence_ids"] = evidence_ids
    data["postprocess_audit"] = audit
    summary, _ = complete_text(data.get("summary_en") or data.get("brief_en"), max_chars=1900)
    if kind == "news":
        data["brief_en"] = summary
        data["summary_en"] = summary
    else:
        data["summary_en"] = summary
    return data


def sanitize_editorial_block(data: dict[str, Any]) -> dict[str, Any]:
    """Remove model-internal reservation sentences before public rendering."""
    cleaned = dict(data)
    for key in ("headline_zh", "lead_zh", "trend_or_risk_zh", "caveats_zh", "headline_en", "brief_en"):
        cleaned[key], _ = complete_text(cleaned.get(key), max_chars=2200)
    findings: list[str] = []
    for finding in cleaned.get("key_findings_zh") or []:
        value, _ = complete_text(finding, max_chars=520)
        if not value:
            continue
        if any(sentence_similarity(value, existing) >= 0.90 for existing in findings):
            continue
        findings.append(value)
    cleaned["key_findings_zh"] = findings
    return cleaned
