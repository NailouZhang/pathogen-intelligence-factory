from __future__ import annotations

import copy
import os
import re
from typing import Any

from .utils import clean_space, unique_strings

POLICY_VERSION = "v14-event-driven-news-query-1"
EVENT_RE = re.compile(
    r"\b(outbreak|epidemic|cluster|case(?:s)?|death(?:s)?|fatalit(?:y|ies)|emergence|"
    r"public health emergency|spillover|transmission)\b|疫情|暴发|爆发|病例|死亡|病死率|突发",
    flags=re.I,
)
COUNTRIES = {
    "ethiopia", "uganda", "rwanda", "tanzania", "kenya", "guinea", "ghana", "nigeria",
    "angola", "congo", "democratic republic of the congo", "sierra leone", "liberia",
    "germany", "chile", "argentina", "united states", "canada", "australia", "india",
    "china", "singapore", "ukraine", "brazil", "bolivia", "peru", "colombia", "mexico",
    "埃塞俄比亚", "乌干达", "卢旺达", "坦桑尼亚", "肯尼亚", "几内亚", "刚果", "中国",
    "美国", "澳大利亚", "印度", "新加坡", "智利", "阿根廷", "巴西", "秘鲁",
}
DEFAULT_SCARCE_PROFILES = {
    "marburg_virus", "nipah_virus", "ebola_viruses", "arenaviridae", "sftsv",
}


def _identity_terms(profile: dict[str, Any]) -> list[str]:
    rules = profile.get("post_retrieval_relevance_rules") or {}
    return unique_strings(
        [profile.get("display_name_en"), profile.get("display_name_zh")]
        + list(rules.get("identity_anchor_patterns") or [])
        + list(rules.get("member_patterns") or [])
        + list(rules.get("disease_patterns") or [])
    )


def _locations(text: str) -> list[str]:
    lower = text.casefold()
    found = [name for name in COUNTRIES if name.casefold() in lower]
    # Capture a conservative capitalized location immediately around event words.
    for match in re.finditer(r"\b(?:in|from|across|within)\s+([A-Z][A-Za-z-]+(?:\s+[A-Z][A-Za-z-]+){0,2})", text):
        candidate = clean_space(match.group(1))
        if candidate.casefold() not in {"The", "This", "Our"}:
            found.append(candidate)
    return unique_strings(found)[:4]


def derive_event_queries(
    papers: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    max_queries: int = 4,
) -> dict[str, Any]:
    identity = _identity_terms(profile)
    preferred_identity = clean_space(profile.get("display_name_en")) or (identity[0] if identity else profile.get("profile_id"))
    candidates: list[dict[str, Any]] = []
    for paper in papers:
        text = clean_space(f"{paper.get('title', '')} {paper.get('abstract', '')}")
        if not text or not EVENT_RE.search(text):
            continue
        if identity and not any(clean_space(term).casefold() in text.casefold() for term in identity if clean_space(term)):
            continue
        locations = _locations(text)
        year_match = re.search(r"\b(20\d{2})\b", text)
        event_word = clean_space(EVENT_RE.search(text).group(0)) if EVENT_RE.search(text) else "outbreak"
        if not locations:
            continue
        for location in locations:
            parts = [f'"{preferred_identity}"', location, event_word]
            if year_match:
                parts.append(year_match.group(1))
            candidates.append({
                "query": clean_space(" ".join(parts)),
                "paper_id": paper.get("paper_id"),
                "title": paper.get("title"),
                "location": location,
                "event_word": event_word,
            })
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidates:
        key = row["query"].casefold()
        if key not in seen:
            seen.add(key)
            unique.append(row)
        if len(unique) >= max_queries:
            break
    return {
        "policy_version": POLICY_VERSION,
        "profile_id": profile.get("profile_id"),
        "queries": [row["query"] for row in unique],
        "evidence": unique,
    }


def augment_news_query_sets(query_sets: dict[str, Any], event_plan: dict[str, Any]) -> dict[str, Any]:
    queries = unique_strings(event_plan.get("queries") or [])
    if not queries:
        return query_sets
    for key in ("general_news_en", "gdelt_core", "reliefweb_core"):
        query_sets[key] = unique_strings(list(query_sets.get(key) or []) + queries)
    query_sets["event_driven_news"] = queries
    return query_sets


def is_scarce_profile(profile_id: str) -> bool:
    configured = os.getenv("PIF_SCARCE_NEWS_PROFILES", "").strip()
    values = {clean_space(x) for x in configured.split(",") if clean_space(x)} if configured else DEFAULT_SCARCE_PROFILES
    return clean_space(profile_id) in values


def news_relevance_profile(profile: dict[str, Any], *, scarce: bool) -> dict[str, Any]:
    if not scarce:
        return profile
    adjusted = copy.deepcopy(profile)
    rules = adjusted.setdefault("post_retrieval_relevance_rules", {})
    rules["minimum_relevance_score"] = max(4, int(rules.get("minimum_relevance_score", 6)) - 1)
    rules["review_score_min"] = max(2, int(rules.get("review_score_min", 3)) - 1)
    adjusted["news_scarcity_policy"] = {
        "enabled": True,
        "policy_version": POLICY_VERSION,
        "note": "Threshold lowered by one point; identity and post-enrichment body gates remain mandatory.",
    }
    return adjusted
