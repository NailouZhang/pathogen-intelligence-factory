from __future__ import annotations

from typing import Any

from .utils import clean_space


NEWS_STATE_POLICY_VERSION = "v15.2-independent-news-readiness-1"
NEWS_CONTENT_STATUSES = {"full", "partial", "syndicated_summary"}
NEWS_ANALYSIS_FIELDS = (
    "time",
    "location_and_population",
    "event",
    "scale_impact_and_risk",
    "response_status_and_uncertainty",
)


def _nonempty_mapping(mapping: Any, fields: tuple[str, ...]) -> bool:
    if not isinstance(mapping, dict):
        return False
    return all(bool(clean_space(mapping.get(field))) for field in fields)


def mark_source_qualified(article: dict[str, Any], qualified: bool = True, *, reason: str = "") -> dict[str, Any]:
    """Record source/relevance qualification independently of later analysis/translation.

    This function must be called only after the title/source/date/body identity,
    duplicate, error-page and final relevance gates have completed.
    """
    article["source_qualified"] = bool(qualified)
    article["source_qualification"] = {
        "qualified": bool(qualified),
        "reason": clean_space(reason) or ("all_source_and_relevance_gates_passed" if qualified else "source_gate_failed"),
        "content_status": article.get("content_status"),
        "policy_version": NEWS_STATE_POLICY_VERSION,
    }
    return article


def derive_analysis_ready(article: dict[str, Any]) -> bool:
    analysis_block = article.get("analysis") or {}
    elements_en = article.get("elements_en") or article.get("analysis_en") or analysis_block.get("analysis") or {}
    brief_en = clean_space(analysis_block.get("brief_en") or article.get("summary_en"))
    ready = bool(brief_en and _nonempty_mapping(elements_en, NEWS_ANALYSIS_FIELDS))
    article["analysis_ready"] = ready
    return ready


def derive_translation_complete(article: dict[str, Any]) -> bool:
    title_zh = clean_space(article.get("title_zh"))
    brief_zh = clean_space(article.get("content_zh") or article.get("summary_zh"))
    elements_zh = article.get("elements_zh") or article.get("analysis_zh") or {}
    audit = article.get("translation_audit") or {}
    fallback = clean_space(article.get("translation_status")) == "english_fallback" or bool(audit.get("english_fallback"))
    complete = bool(title_zh and brief_zh and _nonempty_mapping(elements_zh, NEWS_ANALYSIS_FIELDS) and not fallback)
    article["translation_complete"] = complete
    return complete


def apply_english_display_fallback(article: dict[str, Any]) -> dict[str, Any]:
    """Fill missing Chinese display slots with verified English content.

    The fallback deliberately does not claim successful Chinese translation.
    """
    analysis_block = article.get("analysis") or {}
    elements_en = article.get("elements_en") or article.get("analysis_en") or analysis_block.get("analysis") or {}
    brief_en = clean_space(analysis_block.get("brief_en") or article.get("summary_en") or article.get("content") or article.get("title"))
    changed_fields: list[str] = []

    if not clean_space(article.get("title_zh")):
        article["title_zh"] = clean_space(article.get("title"))
        changed_fields.append("title")
    if not clean_space(article.get("content_zh") or article.get("summary_zh")):
        article["content_zh"] = brief_en
        article["summary_zh"] = brief_en
        changed_fields.append("brief")

    elements_zh = dict(article.get("elements_zh") or article.get("analysis_zh") or {})
    for field in NEWS_ANALYSIS_FIELDS:
        if not clean_space(elements_zh.get(field)):
            elements_zh[field] = clean_space(elements_en.get(field))
            changed_fields.append(field)
    article["elements_zh"] = elements_zh
    article["analysis_zh"] = dict(elements_zh)

    if changed_fields:
        article["translation_status"] = "english_fallback"
        article["translation_complete"] = False
        audit = dict(article.get("translation_audit") or {})
        audit["english_fallback"] = True
        audit["english_fallback_fields"] = changed_fields
        audit["ready"] = False
        audit["policy_version"] = NEWS_STATE_POLICY_VERSION
        article["translation_audit"] = audit
    return article


def finalize_news_state(article: dict[str, Any]) -> dict[str, Any]:
    source_qualified = bool(article.get("source_qualified"))
    analysis_ready = derive_analysis_ready(article)
    translation_complete = derive_translation_complete(article)
    if source_qualified and analysis_ready and not translation_complete:
        apply_english_display_fallback(article)
        translation_complete = False

    display_ready = bool(source_qualified and analysis_ready)
    # WeChat can use a compact Chinese translation or the verified English fallback.
    wechat_summary = clean_space(article.get("wechat_summary_zh") or article.get("content_zh") or article.get("summary_zh"))
    wechat_ready = bool(display_ready and wechat_summary)

    article["display_ready"] = display_ready
    article["wechat_ready"] = wechat_ready
    article["wechat_summary_ready"] = wechat_ready
    # Backwards-compatible name: in v15.2 this means channel-display ready,
    # not that every Chinese field was translated successfully.
    article["translation_ready"] = display_ready
    if translation_complete:
        article["translation_status"] = "complete"
    elif display_ready:
        article.setdefault("translation_status", "english_fallback")
    else:
        article.setdefault("translation_status", "unavailable")
    article["news_state"] = {
        "source_qualified": source_qualified,
        "relevance_ready": bool(article.get("relevance_ready", source_qualified)),
        "analysis_ready": analysis_ready,
        "translation_complete": translation_complete,
        "translation_status": article.get("translation_status"),
        "display_ready": display_ready,
        "wechat_ready": wechat_ready,
        "policy_version": NEWS_STATE_POLICY_VERSION,
    }
    return article
