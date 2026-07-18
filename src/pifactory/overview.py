from __future__ import annotations

import json
from typing import Any

from .llm import LLMError, LLMRouter
from .utils import clean_space, truncate, unique_strings


OVERVIEW_POLICY_VERSION = "v8-separated-15-25-official-brief-1"
PLACEHOLDER_MARKERS = (
    "翻译暂不可用",
    "translation unavailable",
    "中文标题暂不可用",
    "internal error",
)


def _clean_for_overview(value: Any) -> str:
    text = clean_space(value)
    if any(marker.casefold() in text.casefold() for marker in PLACEHOLDER_MARKERS):
        return ""
    return text


def _published_date(item: dict[str, Any]) -> str:
    return clean_space(
        item.get("online_date")
        or item.get("first_publication_date")
        or item.get("availability_date")
        or item.get("published_date")
        or item.get("year")
    )


def select_overview_items(items: list[dict[str, Any]], *, minimum: int = 15, maximum: int = 25) -> list[dict[str, Any]]:
    """Select a ranked, source-diverse 15-25 item overview set when available.

    Input is already relevance/quality ranked. We preserve that ordering while
    preventing one publisher or journal from dominating the briefing.
    """
    maximum = max(1, min(25, maximum))
    minimum = max(1, min(maximum, minimum))
    if len(items) <= maximum:
        return list(items)

    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for item in items:
        source = clean_space(item.get("journal") or item.get("publisher") or item.get("source") or "unknown").casefold()
        if source_counts.get(source, 0) < 3:
            selected.append(item)
            source_counts[source] = source_counts.get(source, 0) + 1
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
    analysis = (item.get("analysis") or {}).get("analysis") or {}
    return {
        "paper_id": item.get("paper_id"),
        "paper_type": item.get("paper_type") or "research",
        "priority_tier": item.get("priority_tier"),
        "quality_score": item.get("quality_score"),
        "title": _clean_for_overview(item.get("title")),
        "title_zh": _clean_for_overview(item.get("title_zh")),
        "authors": (item.get("authors") or [])[:12],
        "journal": item.get("journal"),
        "published_date": _published_date(item),
        "abstract": truncate(_clean_for_overview(item.get("abstract")), 4200),
        "structured_analysis": {key: truncate(_clean_for_overview(value), 900) for key, value in analysis.items()},
        "evidence_level": item.get("evidence_level"),
    }


def _news_payload(item: dict[str, Any]) -> dict[str, Any]:
    analysis_block = item.get("analysis") or {}
    analysis = analysis_block.get("analysis") or {}
    return {
        "news_id": item.get("news_id"),
        "priority_tier": item.get("priority_tier"),
        "quality_score": item.get("quality_score"),
        "title": _clean_for_overview(item.get("title")),
        "title_zh": _clean_for_overview(item.get("title_zh")),
        "publisher": item.get("publisher") or item.get("source"),
        "published_date": item.get("published_date"),
        "source_assessment": analysis_block.get("source_assessment"),
        "brief_en": truncate(_clean_for_overview(analysis_block.get("brief_en")), 1800),
        "brief_zh": truncate(_clean_for_overview(item.get("content_zh")), 900),
        "structured_analysis": {key: truncate(_clean_for_overview(value), 700) for key, value in analysis.items()},
        "content_status": item.get("content_status"),
    }


def _overview_validator(valid_ids: set[str], kind: str):
    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        required = (
            ["headline_zh", "lead_zh", "key_findings_zh", "trend_or_risk_zh", "caveats_zh", "headline_en", "brief_en", "source_ids"]
        )
        for key in required:
            if key not in data:
                return False, f"missing {key}"
        if len(clean_space(data.get("headline_zh"))) < 8:
            return False, "Chinese headline too short"
        if len(clean_space(data.get("lead_zh"))) < 45:
            return False, "Chinese lead too short"
        findings = data.get("key_findings_zh")
        if not isinstance(findings, list) or not 3 <= len(findings) <= 6:
            return False, "key_findings_zh must contain 3-6 items"
        if any(len(clean_space(item)) < 20 for item in findings):
            return False, "finding too short"
        source_ids = data.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            return False, "source_ids missing"
        if any(clean_space(item) not in valid_ids for item in source_ids):
            return False, "unknown source id"
        all_text = json.dumps(data, ensure_ascii=False).casefold()
        if any(marker.casefold() in all_text for marker in PLACEHOLDER_MARKERS):
            return False, "placeholder leaked into overview"
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
        parts.append("核心要点：" + "；".join(findings))
    trend = clean_space(data.get("trend_or_risk_zh"))
    caveats = clean_space(data.get("caveats_zh"))
    if trend:
        parts.append(trend)
    if caveats:
        parts.append("证据提醒：" + caveats)
    return clean_space(" ".join(parts))


def _literature_fallback(profile: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]:
    name = profile.get("display_name_zh") or profile.get("profile_id")
    findings: list[str] = []
    ids: list[str] = []
    for paper in papers[:5]:
        analysis = (paper.get("analysis") or {}).get("analysis") or {}
        key = "main_results" if paper.get("paper_type") != "review" else "consensus_and_key_conclusions"
        text = _clean_for_overview(analysis.get(key))
        if text:
            findings.append(truncate(text, 150))
            ids.append(clean_space(paper.get("paper_id")))
    if not findings:
        findings = ["本期未获得足够的可核验摘要或开放正文，页面仅保留通过相关性审核的书目信息。"] * 3
    while len(findings) < 3:
        findings.append("现有证据数量有限，尚不足以形成稳定的跨研究结论。")
    data = {
        "headline_zh": f"{name}本期文献进展",
        "lead_zh": f"本期文献简报基于{len(papers)}篇入选研究与综述，优先呈现证据较完整且公共卫生意义较高的结果。",
        "key_findings_zh": findings[:5],
        "trend_or_risk_zh": "研究趋势需结合后续连续监测判断，单周结果不应被解释为长期变化。",
        "caveats_zh": "部分条目可能仅有摘要，预印本和观察性研究的结论需要谨慎解释。",
        "headline_en": f"Recent literature on {profile.get('display_name_en') or profile.get('profile_id')}",
        "brief_en": f"This literature briefing includes {len(papers)} selected papers. It prioritizes directly supported findings and explicitly limits interpretation when only abstracts are available.",
        "source_ids": [x for x in ids if x],
        "status": "deterministic_fallback",
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
    for article in news[:5]:
        analysis = (article.get("analysis") or {}).get("analysis") or {}
        text = _clean_for_overview(analysis.get("event"))
        if text:
            findings.append(truncate(text, 150))
            ids.append(clean_space(article.get("news_id")))
    if not findings:
        findings = ["本期未获得足够的原始报道正文，未根据标题扩写新闻事件。"] * 3
    while len(findings) < 3:
        findings.append("可核验新闻数量有限，暂不对传播范围或风险趋势作扩大解释。")
    data = {
        "headline_zh": f"{name}本期新闻动态",
        "lead_zh": f"本期新闻简报基于{len(news)}条抓获有效正文并通过相关性审核的报道，优先采用官方和可信机构信息。",
        "key_findings_zh": findings[:5],
        "trend_or_risk_zh": "风险判断仅依据报道正文中已确认的信息，疑似事件和媒体推测均单独标识。",
        "caveats_zh": "不同来源可能报道同一事件，最终判断应以卫生主管部门和后续实验室确认信息为准。",
        "headline_en": f"Recent public-health reporting on {profile.get('display_name_en') or profile.get('profile_id')}",
        "brief_en": f"This news briefing includes {len(news)} body-verified reports. It separates confirmed developments from suspected events and unresolved claims.",
        "source_ids": [x for x in ids if x],
        "status": "deterministic_fallback",
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
        data.update(
            {
                "status": f"{result.provider}:{result.model}",
                "input_count": len(records),
                "policy_version": OVERVIEW_POLICY_VERSION,
            }
        )
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
    eligible = [item for item in news if item.get("content_status") in {"full", "partial"}]
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
        data.update(
            {
                "status": f"{result.provider}:{result.model}",
                "input_count": len(records),
                "policy_version": OVERVIEW_POLICY_VERSION,
            }
        )
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
    literature = build_literature_overview(
        profile,
        papers,
        llm,
        prompts_dir,
        minimum=minimum,
        maximum=maximum,
    )
    news_brief = build_news_overview(
        profile,
        news,
        llm,
        prompts_dir,
        minimum=minimum,
        maximum=maximum,
    )
    return {
        "literature": literature,
        "news": news_brief,
        "zh": clean_space(f"{literature.get('headline_zh')}；{news_brief.get('headline_zh')}"),
        "en": clean_space(f"{literature.get('headline_en')}; {news_brief.get('headline_en')}"),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
