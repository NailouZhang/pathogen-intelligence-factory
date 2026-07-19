from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from .llm import LLMError, LLMRouter
from .postprocess import BANNED_EDITORIAL_SENTENCES, sanitize_editorial_block, sentence_similarity
from .utils import clean_space, split_sentences, unique_strings


OVERVIEW_POLICY_VERSION = "v15.2-qualified-news-independent-translation-1"
PLACEHOLDER_MARKERS = (
    "翻译暂不可用",
    "translation unavailable",
    "中文标题暂不可用",
    "internal error",
)
ELLIPSIS_MARKERS = ("...", "…", "......", "……")


def _clean_for_overview(value: Any) -> str:
    text = clean_space(value)
    if any(marker.casefold() in text.casefold() for marker in PLACEHOLDER_MARKERS):
        return ""
    if any(marker.casefold() in text.casefold() for marker in BANNED_EDITORIAL_SENTENCES):
        return ""
    return text


def _clip_complete_sentences(value: Any, limit: int) -> str:
    text = _clean_for_overview(value)
    for marker in ELLIPSIS_MARKERS:
        text = text.replace(marker, "")
    if len(text) <= limit:
        if text and text[-1] not in "。.!?！？":
            text += "。" if re.search(r"[\u4e00-\u9fff]", text) else "."
        return clean_space(text)
    output: list[str] = []
    used = 0
    for sentence in split_sentences(text, max_sentences=200):
        sentence = clean_space(sentence).strip(" ,;，；:：")
        if not sentence:
            continue
        if sentence[-1] not in "。.!?！？":
            sentence += "。" if re.search(r"[\u4e00-\u9fff]", sentence) else "."
        if output and used + len(sentence) > limit:
            break
        output.append(sentence)
        used += len(sentence)
    return clean_space(" ".join(output))


def _published_date(item: dict[str, Any]) -> str:
    return clean_space(
        item.get("availability_date")
        or item.get("online_date")
        or item.get("first_publication_date")
        or item.get("published_date")
        or item.get("print_date")
        or item.get("year")
    )


def _parse_date(value: Any) -> date | None:
    text = clean_space(value)
    if not text:
        return None
    match = re.search(r"(19|20)\d{2}(?:[-/]\d{1,2}(?:[-/]\d{1,2})?)?", text)
    if not match:
        return None
    token = match.group(0).replace("/", "-")
    try:
        if len(token) == 4:
            return date(int(token), 1, 1)
        if len(token) == 7:
            return datetime.strptime(token, "%Y-%m").date()
        return datetime.strptime(token[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _contains_ellipsis(value: Any) -> bool:
    text = clean_space(value)
    return any(marker in text for marker in ELLIPSIS_MARKERS)


def _is_chinese(value: Any, minimum_chars: int = 12, ratio: float = 0.28) -> bool:
    text = clean_space(value)
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", text))
    return chinese >= minimum_chars and (not letters or chinese / letters >= ratio)


def _selection_score(item: dict[str, Any], window_start: date | None, window_end: date | None, kind: str) -> float:
    score = float(item.get("quality_score") or 0)
    tier = clean_space(item.get("priority_tier")).upper()
    score += {"A": 55, "B": 25, "C": 5}.get(tier, 0)
    published = _parse_date(_published_date(item))
    if published and window_start and window_end:
        if window_start <= published <= window_end:
            age = max(0, (window_end - published).days)
            score += 120 - age * 8
        elif published > window_end:
            score -= 70
        else:
            score -= min(80, max(0, (window_start - published).days) * 2)
    elif published:
        score += min(25, max(0, published.year - 2020))
    else:
        score -= 35
    evidence = clean_space(item.get("evidence_level"))
    score += {"E2": 22, "E1": 12, "E0": -12}.get(evidence, 0)
    score += min(24, 6 * len(unique_strings(item.get("sources") or item.get("retrieval_sources") or [])))
    score += min(20, 5 * len(unique_strings(item.get("retrieval_concepts") or [])))
    if kind == "literature":
        ptype = clean_space(item.get("paper_type")).casefold()
        publication_types = " ".join(item.get("publication_types") or []).casefold()
        if "meta-analysis" in publication_types or "systematic review" in publication_types:
            score += 25
        elif ptype == "research":
            score += 12
    else:
        if item.get("official"):
            score += 35
        status = clean_space(item.get("content_status"))
        score += {"full": 24, "partial": 14, "syndicated_summary": 6}.get(status, 0)
    return score


def select_overview_items(
    items: list[dict[str, Any]],
    *,
    minimum: int = 15,
    maximum: int = 25,
    window_start: date | str | None = None,
    window_end: date | str | None = None,
    kind: str = "literature",
) -> list[dict[str, Any]]:
    """Select 15-25 recent, high-quality and source-diverse records.

    Selection is never based on incoming order. Papers published inside the
    active reporting window receive the strongest editorial weight. Older or
    undated records can only fill a shortfall when fewer than ``minimum`` recent
    records are available.
    """
    maximum = max(1, min(25, maximum))
    minimum = max(1, min(maximum, minimum))
    start = _parse_date(window_start) if not isinstance(window_start, date) else window_start
    end = _parse_date(window_end) if not isinstance(window_end, date) else window_end
    ranked = sorted(
        items,
        key=lambda item: (
            _selection_score(item, start, end, kind),
            _parse_date(_published_date(item)) or date(1900, 1, 1),
            clean_space(item.get("title")),
        ),
        reverse=True,
    )
    if len(ranked) <= maximum:
        output = []
        for item in ranked:
            enriched = dict(item)
            enriched["overview_selection_score"] = round(_selection_score(item, start, end, kind), 3)
            published = _parse_date(_published_date(item))
            enriched["overview_recent_window"] = bool(start and end and published and start <= published <= end)
            output.append(enriched)
        return output

    recent = [item for item in ranked if start and end and (d := _parse_date(_published_date(item))) and start <= d <= end]
    fallback = [item for item in ranked if item not in recent]
    pool = recent + fallback
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in pool:
        source = clean_space(item.get("journal") or item.get("publisher") or item.get("source") or "unknown").casefold()
        item_type = clean_space(item.get("paper_type") or item.get("source_assessment") or "other").casefold()
        if source_counts.get(source, 0) < 4 and type_counts.get(item_type, 0) < max(10, maximum):
            enriched = dict(item)
            enriched["overview_selection_score"] = round(_selection_score(item, start, end, kind), 3)
            enriched["overview_recent_window"] = bool(item in recent)
            selected.append(enriched)
            source_counts[source] = source_counts.get(source, 0) + 1
            type_counts[item_type] = type_counts.get(item_type, 0) + 1
        else:
            deferred.append(item)
        if len(selected) >= maximum:
            break
    if len(selected) < minimum:
        for item in deferred:
            if len(selected) >= minimum:
                break
            enriched = dict(item)
            enriched["overview_selection_score"] = round(_selection_score(item, start, end, kind), 3)
            enriched["overview_recent_window"] = bool(item in recent)
            selected.append(enriched)
    return selected[:maximum]


def _paper_payload(item: dict[str, Any]) -> dict[str, Any]:
    analysis_en = item.get("elements_en") or item.get("analysis_en") or (item.get("analysis") or {}).get("analysis") or {}
    analysis_zh = item.get("elements_zh") or item.get("analysis_zh") or {}
    return {
        "paper_id": item.get("paper_id"),
        "paper_type": item.get("paper_type") or "research",
        "priority_tier": item.get("priority_tier"),
        "quality_score": item.get("quality_score"),
        "editorial_selection_score": item.get("overview_selection_score"),
        "published_inside_window": item.get("overview_recent_window"),
        "title_en": _clean_for_overview(item.get("title")),
        "title_zh": _clean_for_overview(item.get("title_zh")),
        "authors": (item.get("authors") or [])[:12],
        "journal": item.get("journal"),
        "published_date": _published_date(item),
        "abstract_en": _clip_complete_sentences(item.get("abstract"), 3000),
        "abstract_zh": _clip_complete_sentences(item.get("abstract_zh"), 2200),
        "analysis_en": {key: _clip_complete_sentences(value, 620) for key, value in analysis_en.items()},
        "analysis_zh": {key: _clip_complete_sentences(value, 620) for key, value in analysis_zh.items()},
        "evidence_level": item.get("evidence_level"),
        "publication_types": item.get("publication_types") or [],
    }


def _news_payload(item: dict[str, Any]) -> dict[str, Any]:
    analysis_block = item.get("analysis") or {}
    analysis_en = item.get("elements_en") or item.get("analysis_en") or analysis_block.get("analysis") or {}
    analysis_zh = item.get("elements_zh") or item.get("analysis_zh") or {}
    return {
        "news_id": item.get("news_id"),
        "priority_tier": item.get("priority_tier"),
        "quality_score": item.get("quality_score"),
        "editorial_selection_score": item.get("overview_selection_score"),
        "published_inside_window": item.get("overview_recent_window"),
        "title_en": _clean_for_overview(item.get("title")),
        "title_zh": _clean_for_overview(item.get("title_zh")),
        "publisher": item.get("publisher") or item.get("source"),
        "published_date": item.get("published_date"),
        "source_assessment": analysis_block.get("source_assessment"),
        "content_status": item.get("content_status"),
        "source_qualified": bool(item.get("source_qualified")),
        "analysis_ready": bool(item.get("analysis_ready")),
        "translation_complete": bool(item.get("translation_complete")),
        "translation_status": item.get("translation_status"),
        "display_ready": bool(item.get("display_ready")),
        "brief_en": _clip_complete_sentences(analysis_block.get("brief_en"), 1600),
        "brief_zh": _clip_complete_sentences(item.get("content_zh"), 1100),
        "analysis_en": {key: _clip_complete_sentences(value, 560) for key, value in analysis_en.items()},
        "analysis_zh": {key: _clip_complete_sentences(value, 560) for key, value in analysis_zh.items()},
    }


def _overview_validator(valid_ids: set[str], kind: str):
    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        required = [
            "headline_zh", "lead_zh", "key_findings_zh", "trend_or_risk_zh",
            "caveats_zh", "headline_en", "lead_en", "key_findings_en",
            "trend_or_risk_en", "caveats_en", "brief_en", "source_ids",
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
        if any(marker.casefold() in all_text.casefold() for marker in PLACEHOLDER_MARKERS + BANNED_EDITORIAL_SENTENCES):
            return False, "internal reservation or placeholder leaked into overview"
        if _contains_ellipsis(all_text):
            return False, "ellipsis or incomplete compression is not allowed"
        if any(clean_space(value).endswith(("，", ",", "；", ";", "：", ":")) for value in [data.get("lead_zh"), data.get("trend_or_risk_zh"), data.get("caveats_zh")]):
            return False, "incomplete trailing clause"
        for index, left in enumerate(findings):
            for right in findings[index + 1:]:
                if sentence_similarity(left, right) >= 0.90:
                    return False, "key findings are duplicated"
        findings_en = data.get("key_findings_en")
        if not isinstance(findings_en, list) or not 3 <= len(findings_en) <= 6:
            return False, "key_findings_en must contain 3-6 items"
        for key in ("headline_en", "lead_en", "trend_or_risk_en", "caveats_en", "brief_en"):
            if not isinstance(data.get(key), str) or len(clean_space(data.get(key))) < (20 if key == "headline_en" else 60):
                return False, f"{key} is missing or too short"
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
        parts.append("核心进展：" + "；".join(findings))
    trend = clean_space(data.get("trend_or_risk_zh"))
    caveats = clean_space(data.get("caveats_zh"))
    if trend:
        parts.append(trend)
    if caveats:
        parts.append("证据边界：" + caveats)
    return clean_space(" ".join(parts))


def _literature_fallback(profile: dict[str, Any], papers: list[dict[str, Any]]) -> dict[str, Any]:
    name = profile.get("display_name_zh") or profile.get("profile_id")
    findings: list[str] = []
    ids: list[str] = []
    for paper in papers:
        analysis_zh = paper.get("analysis_zh") or {}
        key = "main_results" if paper.get("paper_type") != "review" else "consensus_and_key_conclusions"
        text = _clean_for_overview(analysis_zh.get(key) or paper.get("summary_zh") or paper.get("abstract_zh"))
        if text and _is_chinese(text, 12, 0.30):
            clipped = _clip_complete_sentences(text, 230)
            if clipped and not any(sentence_similarity(clipped, x) >= 0.90 for x in findings):
                findings.append(clipped + (f" [{paper.get('paper_id')}]" if paper.get("paper_id") else ""))
                ids.append(clean_space(paper.get("paper_id")))
        if len(findings) >= 5:
            break
    while len(findings) < 3 and papers:
        paper = papers[len(findings) % len(papers)]
        title = _clean_for_overview(paper.get("title_zh") or paper.get("title"))
        date_text = _published_date(paper)
        findings.append(f"{date_text or '本期'}发表的《{title}》进入本期重点文献清单，详细证据见下方单篇解读。 [{paper.get('paper_id')}]" )
        ids.append(clean_space(paper.get("paper_id")))
    data = {
        "headline_zh": f"{name}本期文献呈现多方向研究进展",
        "lead_zh": f"本期重点文献按发表日期、相关性、证据等级和研究质量综合排序，研究结果与综述证据分别核验后形成以下进展。",
        "key_findings_zh": findings[:5] or ["本期未形成可公开发布的重点文献结论。"],
        "trend_or_risk_zh": "本期研究方向以入选文献实际覆盖的临床、流行病学、宿主生态、诊断或分子监测主题为准。",
        "caveats_zh": "部分证据来自摘要、观察性研究或叙述性综述，结论应结合研究设计和证据等级理解。",
        "headline_en": f"Recent {profile.get('display_name_en') or profile.get('profile_id')} literature",
        "lead_en": "This literature brief prioritizes publications in the active reporting window and ranks them by relevance, evidence availability, study quality, recency and source convergence.",
        "key_findings_en": [
            _clip_complete_sentences(
                ((paper.get("elements_en") or paper.get("analysis_en") or (paper.get("analysis") or {}).get("analysis") or {}).get(
                    "main_results" if paper.get("paper_type") != "review" else "consensus_and_key_conclusions"
                ) or paper.get("abstract") or paper.get("title")),
                320,
            ) + (f" [{paper.get('paper_id')}]" if paper.get("paper_id") else "")
            for paper in papers[:5]
        ] or ["No literature item passed all publication, relevance, content and analysis gates."],
        "trend_or_risk_en": "The direction of this week's research is defined by the clinical, epidemiological, ecological, diagnostic and molecular topics actually represented by the eligible publications.",
        "caveats_en": "Some evidence is abstract-only, observational or derived from narrative reviews; conclusions should be interpreted in light of study design and evidence strength.",
        "brief_en": "This literature brief prioritizes papers published in the active reporting window and ranks them by relevance, evidence availability, study quality, recency, and independent-source convergence. Detailed study-specific evidence is retained in the article cards below.",
        "source_ids": unique_strings(ids),
        "status": "deterministic_editorial_fallback",
        "input_count": len(papers),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
    data["zh"] = _compose_zh(data)
    data["en"] = data["brief_en"]
    return data


def _news_fallback(profile: dict[str, Any], news: list[dict[str, Any]]) -> dict[str, Any]:
    name_zh = profile.get("display_name_zh") or profile.get("profile_id")
    name_en = profile.get("display_name_en") or profile.get("profile_id")
    all_ids = unique_strings(clean_space(article.get("news_id")) for article in news)
    findings_zh: list[str] = []
    findings_en: list[str] = []

    for article in news:
        analysis_en = article.get("elements_en") or article.get("analysis_en") or (article.get("analysis") or {}).get("analysis") or {}
        analysis_zh = article.get("elements_zh") or article.get("analysis_zh") or {}
        english = _clean_for_overview(
            analysis_en.get("event") or (article.get("analysis") or {}).get("brief_en") or article.get("title")
        )
        chinese = _clean_for_overview(analysis_zh.get("event") or article.get("content_zh"))
        selected_zh = chinese if chinese and _is_chinese(chinese, 8, 0.20) else english
        if selected_zh:
            clipped = _clip_complete_sentences(selected_zh, 260)
            if clipped and not any(sentence_similarity(clipped, existing) >= 0.90 for existing in findings_zh):
                findings_zh.append(clipped + (f" [{article.get('news_id')}]" if article.get("news_id") else ""))
        if english:
            clipped_en = _clip_complete_sentences(english, 320)
            if clipped_en and not any(sentence_similarity(clipped_en, existing) >= 0.90 for existing in findings_en):
                findings_en.append(clipped_en + (f" [{article.get('news_id')}]" if article.get("news_id") else ""))
        if len(findings_zh) >= 5 and len(findings_en) >= 5:
            break

    has_valid_news = bool(news)
    if has_valid_news and not findings_zh:
        findings_zh = [
            _clip_complete_sentences(clean_space(item.get("title_zh") or item.get("title")), 220)
            + (f" [{item.get('news_id')}]" if item.get("news_id") else "")
            for item in news[:5]
            if clean_space(item.get("title_zh") or item.get("title"))
        ]
    if has_valid_news and not findings_en:
        findings_en = [
            _clip_complete_sentences(clean_space(item.get("title")), 260)
            + (f" [{item.get('news_id')}]" if item.get("news_id") else "")
            for item in news[:5]
            if clean_space(item.get("title"))
        ]

    if has_valid_news:
        zh_empty = "Qualified news exists, but no compact event field was available; see the verified reports below."
        en_empty = "Qualified news exists, but no compact event field was available; see the verified reports below."
        lead_zh = "本期新闻资格由来源、日期、正文身份和相关性终审决定；中文翻译状态不影响有效新闻的保留。中文字段不可用时，本区域使用已核验英文内容填充。"
        lead_en = "News eligibility is determined by source, date, body identity and final relevance. Translation completeness does not determine whether an eligible report is retained."
    else:
        zh_empty = "本期未获得通过来源、日期、正文身份和相关性终审的有效新闻。"
        en_empty = "No news report passed the source, date, body-identity and final-relevance gates in this reporting window."
        lead_zh = "本期没有新闻记录通过完整的来源、日期、正文身份和相关性终审。"
        lead_en = "No news record passed the complete source, date, body-identity and relevance workflow."

    data = {
        "headline_zh": f"{name_zh}本期公共卫生新闻动态",
        "lead_zh": lead_zh,
        "key_findings_zh": findings_zh[:5] or [zh_empty],
        "trend_or_risk_zh": "风险判断仅依据入选来源已经确认的信息，不把媒体推测升级为官方结论。",
        "caveats_zh": "中文翻译不完整时会以英文证据填充中文显示位置，并在后台记录translation_status=english_fallback。",
        "headline_en": f"Recent {name_en} news",
        "lead_en": lead_en,
        "key_findings_en": findings_en[:5] or [en_empty],
        "trend_or_risk_en": "Risk statements are limited to information confirmed by eligible sources and do not upgrade media speculation into official conclusions.",
        "caveats_en": "When Chinese translation is incomplete, verified English evidence fills the Chinese display slot and is audited as translation_status=english_fallback.",
        "brief_en": " ".join(findings_en[:5]) if findings_en else en_empty,
        "source_ids": all_ids,
        "status": "deterministic_editorial_fallback",
        "input_count": len(news),
        "qualified_news_count": len(news),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
    data["zh"] = _compose_zh(data)
    data["en"] = data["brief_en"]
    return data


def build_literature_overview(
    profile: dict[str, Any], papers: list[dict[str, Any]], llm: LLMRouter, prompts_dir: Any,
    *, minimum: int = 15, maximum: int = 25, window_start: date | str | None = None, window_end: date | str | None = None,
) -> dict[str, Any]:
    selected = select_overview_items(papers, minimum=minimum, maximum=maximum, window_start=window_start, window_end=window_end, kind="literature")
    fallback = _literature_fallback(profile, selected)
    if not selected or not llm.available:
        return fallback
    records = [_paper_payload(item) for item in selected]
    valid_ids = {clean_space(item.get("paper_id")) for item in records if clean_space(item.get("paper_id"))}
    system = (prompts_dir / "literature_overview.md").read_text(encoding="utf-8")
    prompt = json.dumps({
        "policy_version": OVERVIEW_POLICY_VERSION,
        "pathogen_zh": profile.get("display_name_zh"),
        "pathogen_en": profile.get("display_name_en"),
        "window_start": str(window_start or ""),
        "window_end": str(window_end or ""),
        "selection_rule": "recent-publication-first, then quality, evidence, relevance, source diversity and hotspot significance; never input order",
        "input_count": len(records),
        "records": records,
    }, ensure_ascii=False)
    try:
        result = llm.json_task(system=system, prompt=prompt, provider_order=getattr(llm, "provider_order", lambda purpose: None)("overview"), validator=_overview_validator(valid_ids, "literature"), temperature=0.03, max_models_per_provider=2)
        data = sanitize_editorial_block(dict(result.data))
        if len(data.get("key_findings_zh") or []) < 3:
            return fallback
        data.update({"status": f"{result.provider}:{result.model}", "input_count": len(records), "policy_version": OVERVIEW_POLICY_VERSION})
        data["zh"] = _compose_zh(data)
        data["en"] = clean_space(data.get("brief_en"))
        return data
    except LLMError:
        return fallback


def build_news_overview(
    profile: dict[str, Any], news: list[dict[str, Any]], llm: LLMRouter, prompts_dir: Any,
    *, minimum: int = 15, maximum: int = 25, window_start: date | str | None = None, window_end: date | str | None = None,
) -> dict[str, Any]:
    eligible = [
        item for item in news
        if (
            item.get("source_qualified")
            or ("source_qualified" not in item and item.get("content_status") in {"full", "partial", "syndicated_summary"})
        )
        and (
            item.get("analysis_ready")
            or ("analysis_ready" not in item and bool((item.get("analysis") or {}).get("analysis")))
        )
        and (item.get("display_ready") or "display_ready" not in item)
    ]
    selected = select_overview_items(eligible, minimum=minimum, maximum=maximum, window_start=window_start, window_end=window_end, kind="news")
    fallback = _news_fallback(profile, selected)
    if not selected or not llm.available:
        return fallback
    records = [_news_payload(item) for item in selected]
    valid_ids = {clean_space(item.get("news_id")) for item in records if clean_space(item.get("news_id"))}
    system = (prompts_dir / "news_overview.md").read_text(encoding="utf-8")
    prompt = json.dumps({
        "policy_version": OVERVIEW_POLICY_VERSION,
        "pathogen_zh": profile.get("display_name_zh"),
        "pathogen_en": profile.get("display_name_en"),
        "window_start": str(window_start or ""),
        "window_end": str(window_end or ""),
        "selection_rule": "recent-event and publication first, then official status, body completeness, risk significance and source diversity",
        "input_count": len(records),
        "records": records,
    }, ensure_ascii=False)
    try:
        result = llm.json_task(system=system, prompt=prompt, provider_order=getattr(llm, "provider_order", lambda purpose: None)("overview"), validator=_overview_validator(valid_ids, "news"), temperature=0.03, max_models_per_provider=2)
        data = sanitize_editorial_block(dict(result.data))
        if len(data.get("key_findings_zh") or []) < 3:
            return fallback
        data.update({"status": f"{result.provider}:{result.model}", "input_count": len(records), "policy_version": OVERVIEW_POLICY_VERSION})
        # Provenance must cover the full selected qualified-news set, regardless
        # of which items produced Chinese findings.
        data["source_ids"] = unique_strings(item.get("news_id") for item in records)
        data["qualified_news_count"] = len(records)
        data["zh"] = _compose_zh(data)
        data["en"] = clean_space(data.get("brief_en"))
        return data
    except LLMError:
        return fallback


def build_overviews(
    profile: dict[str, Any], papers: list[dict[str, Any]], news: list[dict[str, Any]], llm: LLMRouter, prompts_dir: Any,
    *, minimum: int = 15, maximum: int = 25, window_start: date | str | None = None, window_end: date | str | None = None,
) -> dict[str, Any]:
    literature = build_literature_overview(profile, papers, llm, prompts_dir, minimum=minimum, maximum=maximum, window_start=window_start, window_end=window_end)
    news_brief = build_news_overview(profile, news, llm, prompts_dir, minimum=minimum, maximum=maximum, window_start=window_start, window_end=window_end)
    return {
        "literature": literature,
        "news": news_brief,
        "zh": clean_space(f"{literature.get('headline_zh')}；{news_brief.get('headline_zh')}"),
        "en": clean_space(f"{literature.get('headline_en')}; {news_brief.get('headline_en')}"),
        "policy_version": OVERVIEW_POLICY_VERSION,
    }
