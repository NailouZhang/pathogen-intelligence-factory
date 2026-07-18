from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLMError, LLMRouter
from .utils import clean_space, split_sentences, unique_strings


OVERVIEW_POLICY_VERSION = "v9-editorial-chinese-no-ellipsis-1"
PLACEHOLDER_MARKERS = (
    "翻译暂不可用",
    "translation unavailable",
    "中文标题暂不可用",
    "internal error",
)
ELLIPSIS_MARKERS = ("...", "…", "......")


def _clean_for_overview(value: Any) -> str:
    text = clean_space(value)
    if any(marker.casefold() in text.casefold() for marker in PLACEHOLDER_MARKERS):
        return ""
    return text


def _clip_complete_sentences(value: Any, limit: int) -> str:
    """Clip without an ellipsis or an incomplete trailing sentence."""
    text = _clean_for_overview(value)
    if len(text) <= limit:
        return text
    output: list[str] = []
    used = 0
    for sentence in split_sentences(text, max_sentences=200):
        sentence = clean_space(sentence)
        if not sentence:
            continue
        if used + len(sentence) > limit:
            break
        output.append(sentence)
        used += len(sentence)
    if output:
        return clean_space(" ".join(output))
    # For a single very long sentence, use a complete clause boundary and add a
    # terminal stop rather than an ellipsis.
    piece = text[:limit]
    for delimiter in ("。", ". ", "；", "; ", "，", ", "):
        index = piece.rfind(delimiter)
        if index >= max(40, limit // 2):
            piece = piece[: index + len(delimiter)].strip()
            break
    piece = piece.rstrip(" ,;，；:：")
    if piece and piece[-1] not in "。.!?！？":
        piece += "。" if re.search(r"[\u4e00-\u9fff]", piece) else "."
    return piece


def _published_date(item: dict[str, Any]) -> str:
    return clean_space(
        item.get("online_date")
        or item.get("first_publication_date")
        or item.get("availability_date")
        or item.get("published_date")
        or item.get("year")
    )


def _contains_ellipsis(value: Any) -> bool:
    text = clean_space(value)
    return any(marker in text for marker in ELLIPSIS_MARKERS)


def _is_chinese(value: Any, minimum_chars: int = 12, ratio: float = 0.28) -> bool:
    text = clean_space(value)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))
    return chinese >= minimum_chars and (not letters or chinese / letters >= ratio)


def select_overview_items(items: list[dict[str, Any]], *, minimum: int = 15, maximum: int = 25) -> list[dict[str, Any]]:
    """Select 15-25 ranked items while limiting one-source dominance."""
    maximum = max(1, min(25, maximum))
    minimum = max(1, min(maximum, minimum))
    if len(items) <= maximum:
        return list(items)

    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in items:
        source = clean_space(item.get("journal") or item.get("publisher") or item.get("source") or "unknown").casefold()
        item_type = clean_space(item.get("paper_type") or item.get("source_assessment") or "other").casefold()
        if source_counts.get(source, 0) < 3 and type_counts.get(item_type, 0) < max(8, maximum):
            selected.append(item)
            source_counts[source] = source_counts.get(source, 0) + 1
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
        else:
            deferred.append(item)
        if len(selected) >= maximum:
            break
    if len(selected) < minimum:
        for item in deferred:
            if item not in selected:
                selected.append(item)
            if len(selected) >= minimum:
                break
    return selected[:maximum]


def _paper_payload(item: dict[str, Any]) -> dict[str, Any]:
    analysis_en = (item.get("analysis") or {}).get("analysis") or {}
    analysis_zh = item.get("analysis_zh") or {}
    return {
        "paper_id": item.get("paper_id"),
        "paper_type": item.get("paper_type") or "research",
        "priority_tier": item.get("priority_tier"),
        "quality_score": item.get("quality_score"),
        "title_en": _clean_for_overview(item.get("title")),
        "title_zh": _clean_for_overview(item.get("title_zh")),
        "authors": (item.get("authors") or [])[:12],
        "journal": item.get("journal"),
        "published_date": _published_date(item),
        "abstract_en": _clip_complete_sentences(item.get("abstract"), 2600),
        "abstract_zh": _clip_complete_sentences(item.get("abstract_zh"), 1800),
        "analysis_en": {key: _clip_complete_sentences(value, 520) for key, value in analysis_en.items()},
        "analysis_zh": {key: _clip_complete_sentences(value, 520) for key, value in analysis_zh.items()},
        "evidence_level": item.get("evidence_level"),
        "publication_types": item.get("publication_types") or [],
    }


def _news_payload(item: dict[str, Any]) -> dict[str, Any]:
    analysis_block = item.get("analysis") or {}
    analysis_en = analysis_block.get("analysis") or {}
    analysis_zh = item.get("analysis_zh") or {}
    return {
        "news_id": item.get("news_id"),
        "priority_tier": item.get("priority_tier"),
        "quality_score": item.get("quality_score"),
        "title_en": _clean_for_overview(item.get("title")),
        "title_zh": _clean_for_overview(item.get("title_zh")),
        "publisher": item.get("publisher") or item.get("source"),
        "published_date": item.get("published_date"),
        "source_assessment": analysis_block.get("source_assessment"),
        "content_status": item.get("content_status"),
        "brief_en": _clip_complete_sentences(analysis_block.get("brief_en"), 1400),
        "brief_zh": _clip_complete_sentences(item.get("content_zh"), 900),
        "analysis_en": {key: _clip_complete_sentences(value, 480) for key, value in analysis_en.items()},
        "analysis_zh": {key: _clip_complete_sentences(value, 480) for key, value in analysis_zh.items()},
    }


def _overview_validator(valid_ids: set[str], kind: str):
    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        required = [
            "headline_zh", "lead_zh", "key_findings_zh", "trend_or_risk_zh",
            "caveats_zh", "headline_en", "brief_en", "source_ids",
        ]
        for key in required:
            if key not in data:
                return False, f"missing {key}"
        if not _is_chinese(data.get("headline_zh"), 8, 0.45):
            return False, "headline_zh is not sufficiently Chinese"
        if not _is_chinese(data.get("lead_zh"), 35, 0.45):
            return False, "lead_zh is not sufficiently Chinese"
        findings = data.get("key_findings_zh")
        if not isinstance(findings, list) or not 3 <= len(findings) <= 6:
            return False, "key_findings_zh must contain 3-6 items"
        if any(not _is_chinese(item, 18, 0.38) for item in findings):
            return False, "finding is not sufficiently Chinese"
        if not _is_chinese(data.get("trend_or_risk_zh"), 18, 0.38):
            return False, "trend_or_risk_zh is not sufficiently Chinese"
        if not _is_chinese(data.get("caveats_zh"), 15, 0.35):
            return False, "caveats_zh is not sufficiently Chinese"
        all_text = json.dumps(data, ensure_ascii=False)
        if any(marker.casefold() in all_text.casefold() for marker in PLACEHOLDER_MARKERS):
            return False, "placeholder leaked into overview"
        if _contains_ellipsis(all_text):
            return False, "ellipsis or incomplete compression is not allowed"
        source_ids = unique_strings(data.get("source_ids") or [])
        if len(source_ids) < min(3, len(valid_ids)):
            return False, "not enough source ids"
        if any(clean_space(item) not in valid_ids for item in source_ids):
            return False, "unknown source id"
        if kind == "literature" and len(clean_space(data.get("brief_en"))) < 100:
            return False, "literature English brief too short"
        if kind == "news" and len(clean_space(data.get("brief_en"))) < 80:
            return False, "news English brief too short"
        return True, "ok"

    return validator


def _compose_zh(data: dict[str, Any]) -> str:
    parts = [clean_space(data.get("lead_zh"))]
    findings = [clean_space(x) for x in data.get("key_findings_zh") or [] if clean_space(x)]
    if findings:
        parts.append("核心发现：" + "；".join(findings))
    trend = clean_space(data.get("trend_or_risk_zh"))
    caveats = clean_space(data.get("caveats_zh"))
    if trend:
        parts.append(trend)
    if caveats:
        parts.append("证据提醒：" + caveats)
    value = clean_space(" ".join(parts))
    for marker in ELLIPSIS_MARKERS:
        value = value.replace(marker, "")
    return value


def _literature_fallback(profile: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]:
    name = profile.get("display_name_zh") or profile.get("profile_id")
    findings: list[str] = []
    ids: list[str] = []
    for paper in papers:
        analysis_zh = paper.get("analysis_zh") or {}
        key = "main_results" if paper.get("paper_type") != "review" else "consensus_and_key_conclusions"
        text = _clean_for_overview(analysis_zh.get(key) or paper.get("summary_zh") or paper.get("abstract_zh"))
        if text and _is_chinese(text, 12, 0.30):
            findings.append(_clip_complete_sentences(text, 190))
            ids.append(clean_space(paper.get("paper_id")))
        if len(findings) >= 5:
            break
    if not findings:
        findings = [
            "本期入选文献均已完成相关性核验，但现有中文证据不足以形成可靠的跨研究综合结论。",
            "研究方向覆盖流行病学、临床、宿主生态或分子监测，具体结果应结合下方单篇精读查看。",
            "本期综合报道未使用未翻译英文内容，也未用占位文本替代中文结论。",
        ]
    literature_fillers = [
        "现有证据数量有限，暂不将单篇发现扩大解释为稳定趋势。",
        "不同研究设计和人群之间仍需更多可比数据，当前结论应结合证据等级理解。",
        "本期研究热点不能替代长期监测，后续仍需关注独立验证和多中心证据。",
    ]
    for filler in literature_fillers:
        if len(findings) >= 3:
            break
        if filler not in findings:
            findings.append(filler)
    data = {
        "headline_zh": f"{name}本期文献研究呈现多方向进展",
        "lead_zh": f"本期文献简报综合{len(papers)}篇研究与综述，按照科研新闻报道方式提炼证据最强的结果、共同趋势及需要谨慎解释的结论。",
        "key_findings_zh": findings[:5],
        "trend_or_risk_zh": "单周文献可反映近期研究热点，但不能单独代表长期流行趋势或直接改变临床与公共卫生决策。",
        "caveats_zh": "部分条目仅有摘要，观察性研究、非随机研究和预印本的证据强度应低于经过充分验证的研究。",
        "headline_en": f"Recent literature on {profile.get('display_name_en') or profile.get('profile_id')}",
        "brief_en": f"This literature briefing integrates {len(papers)} selected papers and separates primary findings, review-level consensus, research trends, and evidence limitations.",
        "source_ids": [x for x in unique_strings(ids) if x],
        "status": "deterministic_chinese_fallback",
        "input_count": len(papers),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
    data["zh"] = _compose_zh(data)
    data["en"] = data["brief_en"]
    return data


def _news_fallback(profile: dict[str, Any], news: list[dict[str, Any]]) -> dict[str, Any]:
    name = profile.get("display_name_zh") or profile.get("profile_id")
    findings: list[str] = []
    ids: list[str] = []
    for article in news:
        analysis_zh = article.get("analysis_zh") or {}
        text = _clean_for_overview(analysis_zh.get("event") or article.get("content_zh"))
        if text and _is_chinese(text, 10, 0.30):
            findings.append(_clip_complete_sentences(text, 180))
            ids.append(clean_space(article.get("news_id")))
        if len(findings) >= 5:
            break
    if not findings:
        findings = [
            "本期尚未获得足够的可核验新闻正文或实质性新闻摘要，因此未根据标题扩写事件。",
            "新闻来源抓取状态已单独记录，接口或网页解析失败不会被误报为没有公共卫生事件。",
            "后续风险判断应以卫生主管部门、实验室确认和原始报道更新为准。",
        ]
    news_fillers = [
        "可核验新闻数量有限，暂不扩大解释传播范围或风险变化。",
        "报道之间的病例口径和事件时间可能不同，最终信息应以主管部门更新为准。",
        "当前材料不足以确认新增传播链，风险等级不因单条媒体报道自动上调。",
    ]
    for filler in news_fillers:
        if len(findings) >= 3:
            break
        if filler not in findings:
            findings.append(filler)
    data = {
        "headline_zh": f"{name}本期公共卫生新闻动态",
        "lead_zh": f"本期新闻简报综合{len(news)}条获得有效正文或实质性摘要的报道，按照官方新闻通报方式区分已确认事件、风险影响、应对措施和未决信息。",
        "key_findings_zh": findings[:5],
        "trend_or_risk_zh": "风险判断只采用原始报道或实质性摘要中的明确信息，疑似事件、媒体推测和尚未证实的传播链均单独标记。",
        "caveats_zh": "同一事件可能被多个媒体转载，最终病例数、地点和传播状态应以主管部门后续通报为准。",
        "headline_en": f"Recent public-health reporting on {profile.get('display_name_en') or profile.get('profile_id')}",
        "brief_en": f"This news briefing integrates {len(news)} reports with verified body text or substantive syndicated summaries and separates confirmed developments from unresolved claims.",
        "source_ids": [x for x in unique_strings(ids) if x],
        "status": "deterministic_chinese_fallback",
        "input_count": len(news),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
    data["zh"] = _compose_zh(data)
    data["en"] = data["brief_en"]
    return data


def build_literature_overview(
    profile: dict[str, Any],
    papers: list[dict[str, Any]],
    llm: LLMRouter,
    prompts_dir: Any,
    *,
    minimum: int = 15,
    maximum: int = 25,
) -> dict[str, Any]:
    selected = select_overview_items(papers, minimum=minimum, maximum=maximum)
    fallback = _literature_fallback(profile, selected)
    if not selected or not llm.available:
        return fallback
    records = [_paper_payload(item) for item in selected]
    valid_ids = {clean_space(item.get("paper_id")) for item in records if clean_space(item.get("paper_id"))}
    system = (prompts_dir / "literature_overview.md").read_text(encoding="utf-8")
    prompt = json.dumps(
        {
            "policy_version": OVERVIEW_POLICY_VERSION,
            "pathogen_zh": profile.get("display_name_zh"),
            "pathogen_en": profile.get("display_name_en"),
            "input_count": len(records),
            "records": records,
        },
        ensure_ascii=False,
    )
    try:
        result = llm.json_task(
            system=system,
            prompt=prompt,
            validator=_overview_validator(valid_ids, "literature"),
            temperature=0.05,
            max_models_per_provider=2,
        )
        data = dict(result.data)
        data.update({
            "status": f"{result.provider}:{result.model}",
            "input_count": len(records),
            "policy_version": OVERVIEW_POLICY_VERSION,
        })
        data["zh"] = _compose_zh(data)
        data["en"] = clean_space(data.get("brief_en"))
        return data
    except LLMError:
        return fallback


def build_news_overview(
    profile: dict[str, Any],
    news: list[dict[str, Any]],
    llm: LLMRouter,
    prompts_dir: Any,
    *,
    minimum: int = 15,
    maximum: int = 25,
) -> dict[str, Any]:
    eligible = [item for item in news if item.get("content_status") in {"full", "partial", "syndicated_summary"}]
    selected = select_overview_items(eligible, minimum=minimum, maximum=maximum)
    fallback = _news_fallback(profile, selected)
    if not selected or not llm.available:
        return fallback
    records = [_news_payload(item) for item in selected]
    valid_ids = {clean_space(item.get("news_id")) for item in records if clean_space(item.get("news_id"))}
    system = (prompts_dir / "news_overview.md").read_text(encoding="utf-8")
    prompt = json.dumps(
        {
            "policy_version": OVERVIEW_POLICY_VERSION,
            "pathogen_zh": profile.get("display_name_zh"),
            "pathogen_en": profile.get("display_name_en"),
            "input_count": len(records),
            "records": records,
        },
        ensure_ascii=False,
    )
    try:
        result = llm.json_task(
            system=system,
            prompt=prompt,
            validator=_overview_validator(valid_ids, "news"),
            temperature=0.05,
            max_models_per_provider=2,
        )
        data = dict(result.data)
        data.update({
            "status": f"{result.provider}:{result.model}",
            "input_count": len(records),
            "policy_version": OVERVIEW_POLICY_VERSION,
        })
        data["zh"] = _compose_zh(data)
        data["en"] = clean_space(data.get("brief_en"))
        return data
    except LLMError:
        return fallback


def build_overviews(
    profile: dict[str, Any],
    papers: list[dict[str, Any]],
    news: list[dict[str, Any]],
    llm: LLMRouter,
    prompts_dir: Any,
    *,
    minimum: int = 15,
    maximum: int = 25,
) -> dict[str, Any]:
    literature = build_literature_overview(profile, papers, llm, prompts_dir, minimum=minimum, maximum=maximum)
    news_brief = build_news_overview(profile, news, llm, prompts_dir, minimum=minimum, maximum=maximum)
    return {
        "literature": literature,
        "news": news_brief,
        "zh": clean_space(f"{literature.get('headline_zh')}；{news_brief.get('headline_zh')}"),
        "en": clean_space(f"{literature.get('headline_en')}; {news_brief.get('headline_en')}"),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
