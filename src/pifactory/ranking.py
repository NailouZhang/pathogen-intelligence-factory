from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urlparse

from .utils import clean_space

PAPER_SOURCE_WEIGHT = {
    "PubMed": 18,
    "Europe PMC": 17,
    "Crossref": 10,
    "Semantic Scholar": 9,
    "OpenAlex": 9,
    "bioRxiv": 4,
    "medRxiv": 4,
}

OFFICIAL_NEWS_TOKENS = (
    "who.int",
    "cdc.gov",
    "ecdc.europa.eu",
    "gov.",
    ".gov",
    "health.gov",
    "nhs.uk",
    "reliefweb.int",
    "afro.who.int",
    "paho.org",
)

HIGH_LEVEL_TYPES = {
    "systematic review": 22,
    "meta-analysis": 24,
    "randomized controlled trial": 22,
    "clinical trial": 18,
    "multicenter study": 16,
    "cohort study": 14,
    "case-control study": 12,
    "review": 10,
    "guideline": 18,
    "practice guideline": 20,
}

TIER_ORDER = {"A": 3, "B": 2, "C": 1}
TRUSTED_PAPER_SOURCES = {"PubMed", "Europe PMC", "OpenAlex", "Crossref", "Semantic Scholar"}



def _days_old(value: str | None) -> int:
    try:
        return max(0, (date.today() - date.fromisoformat(str(value)[:10])).days)
    except (TypeError, ValueError):
        return 30


def paper_quality(record: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = float(record.get("relevance_score") or 0) * 36
    sources = record.get("sources") or [record.get("source")]
    source_points = max((PAPER_SOURCE_WEIGHT.get(str(x), 5) for x in sources if x), default=0)
    score += source_points
    reasons.append(f"source={source_points}")

    types = " ".join(str(x).lower() for x in record.get("publication_types") or [])
    design_points = max((points for token, points in HIGH_LEVEL_TYPES.items() if token in types), default=0)
    score += design_points
    if design_points:
        reasons.append(f"design={design_points}")

    if clean_space(record.get("abstract")):
        score += 10
        reasons.append("abstract=10")
    if record.get("open_access") or record.get("full_text_urls") or record.get("full_text_links"):
        score += 5
    if record.get("doi"):
        score += 3
    citations = min(int(record.get("citation_count") or 0), 100)
    score += min(citations / 10, 8)
    score += max(0, 8 - _days_old(record.get("availability_date")) * 0.5)

    source_text = " ".join(str(x).lower() for x in sources if x)
    if "biorxiv" in source_text or "medrxiv" in source_text:
        score -= 5
        reasons.append("preprint=-5")
    return round(score, 3), reasons


def news_quality(record: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = float(record.get("relevance_score") or 0) * 35
    url = clean_space(record.get("resolved_url") or record.get("url"))
    host = (urlparse(url).hostname or "").lower()
    publisher = clean_space(record.get("publisher")).lower()
    source = clean_space(record.get("source")).lower()
    haystack = " ".join((host, publisher, source))

    official = bool(record.get("official")) or any(token in haystack for token in OFFICIAL_NEWS_TOKENS)
    if official:
        score += 45
        reasons.append("official=45")
    elif any(token in haystack for token in ("university", "hospital", "institute", "laboratory", "public health")):
        score += 25
        reasons.append("institution=25")
    elif "reliefweb" in haystack:
        score += 35
    else:
        score += 8

    status = clean_space(record.get("content_status") or record.get("body_status"))
    if status in {"full", "captured", "partial"} and clean_space(record.get("content")):
        score += 12
        reasons.append("body=12")
    elif clean_space(record.get("excerpt")):
        score += 4
    score += max(0, 8 - _days_old(record.get("published_date")) * 0.5)
    return round(score, 3), reasons


def paper_priority_tier(record: dict[str, Any]) -> tuple[str, str]:
    relevance = float(record.get("relevance_score") or 0)
    sources = set(record.get("sources") or [record.get("source")])
    types = " ".join(str(x).lower() for x in record.get("publication_types") or [])
    design_points = max((points for token, points in HIGH_LEVEL_TYPES.items() if token in types), default=0)
    has_evidence = bool(clean_space(record.get("abstract") or record.get("full_text")))
    evidence_level = clean_space(record.get("evidence_level"))
    trusted = bool(sources & TRUSTED_PAPER_SOURCES)
    citations = int(record.get("citation_count") or 0)

    if relevance >= 0.6 and has_evidence and (
        design_points >= 16
        or evidence_level == "E2"
        or citations >= 20
        or (trusted and float(record.get("quality_score") or 0) >= 62)
    ):
        return "A", "高相关性，且具备高等级研究设计、全文证据或较强数据库/引用支持"
    if relevance >= 0.6 and (has_evidence or trusted):
        return "B", "主题明确且摘要或可信数据库元数据较完整"
    return "C", "补充性记录、预印本或证据完整度有限"


def news_priority_tier(record: dict[str, Any]) -> tuple[str, str]:
    url = clean_space(record.get("resolved_url") or record.get("url"))
    host = (urlparse(url).hostname or "").lower()
    publisher = clean_space(record.get("publisher")).lower()
    source = clean_space(record.get("source")).lower()
    haystack = " ".join((host, publisher, source))
    official = bool(record.get("official")) or any(token in haystack for token in OFFICIAL_NEWS_TOKENS)
    has_body = bool(clean_space(record.get("content")))
    institution = any(token in haystack for token in ("university", "hospital", "institute", "laboratory", "public health", "reliefweb"))
    if official:
        return "A", "政府、WHO/CDC/ECDC或其他官方公共卫生来源"
    if institution or has_body:
        return "B", "可信机构来源或已成功抓获正文"
    return "C", "新闻聚合或正文证据有限，作为补充信息"


def rank_papers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        score, reasons = paper_quality(record)
        record["quality_score"] = score
        record["quality_reasons"] = reasons
        tier, reason = paper_priority_tier(record)
        record["priority_tier"] = tier
        record["priority_tier_reason"] = reason
    return sorted(
        records,
        key=lambda x: (
            TIER_ORDER.get(str(x.get("priority_tier")), 0),
            x.get("quality_score") or 0,
            x.get("availability_date") or "",
            clean_space(x.get("title")),
        ),
        reverse=True,
    )


def rank_news(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        score, reasons = news_quality(record)
        record["quality_score"] = score
        record["quality_reasons"] = reasons
        tier, reason = news_priority_tier(record)
        record["priority_tier"] = tier
        record["priority_tier_reason"] = reason
    return sorted(
        records,
        key=lambda x: (
            TIER_ORDER.get(str(x.get("priority_tier")), 0),
            x.get("quality_score") or 0,
            x.get("published_date") or "",
            clean_space(x.get("title")),
        ),
        reverse=True,
    )
