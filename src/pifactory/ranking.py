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


def rank_papers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        score, reasons = paper_quality(record)
        record["quality_score"] = score
        record["quality_reasons"] = reasons
    return sorted(
        records,
        key=lambda x: (x.get("quality_score") or 0, x.get("availability_date") or ""),
        reverse=True,
    )


def rank_news(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for record in records:
        score, reasons = news_quality(record)
        record["quality_score"] = score
        record["quality_reasons"] = reasons
    return sorted(
        records,
        key=lambda x: (x.get("quality_score") or 0, x.get("published_date") or ""),
        reverse=True,
    )
