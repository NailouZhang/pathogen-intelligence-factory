from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .utils import clean_space

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])\s*|(?<=[.!?])\s+(?=[A-Z0-9])")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_quality_contract() -> dict[str, Any]:
    root = _project_root() / "config" / "news_extraction"
    terms = json.loads((root / "boilerplate_terms.json").read_text(encoding="utf-8"))
    rules = json.loads((root / "site_rules.json").read_text(encoding="utf-8"))
    return {"terms": terms, "rules": rules}


def clean_article_dom(soup: BeautifulSoup, host: str = "") -> BeautifulSoup:
    clone = BeautifulSoup(str(soup), "lxml")
    contract = load_quality_contract()
    selectors = list((contract["rules"] or {}).get("global_remove_selectors") or [])
    domain_rules = ((contract["rules"] or {}).get("domains") or {}).get(host, {})
    selectors.extend(domain_rules.get("remove_selectors") or [])
    for selector in selectors:
        try:
            for node in clone.select(selector):
                node.decompose()
        except Exception:
            continue
    return clone


def _lines(text: str) -> list[str]:
    """Return true layout lines without inventing duplicate sentence rows.

    Most extractors flatten a legitimate article into one long string. Treating
    every sentence as a separate layout line made repeated epidemiological
    wording look like a duplicated navigation menu. Navigation/short-line
    diagnostics therefore operate only on actual line breaks. Sentence quality
    is measured separately below.
    """
    raw = [clean_space(line) for line in re.split(r"[\r\n]+", text) if clean_space(line)]
    return raw or ([clean_space(text)] if clean_space(text) else [])

def diagnose_news_text(text: str, *, title: str = "", official: bool = False, content_kind: str = "article") -> dict[str, Any]:
    raw_value = str(text or "")
    value = clean_space(raw_value)
    lower = value.casefold()
    contract = load_quality_contract()["terms"]
    terms = [clean_space(x).casefold() for x in contract.get("terms") or [] if clean_space(x)]
    hard = [clean_space(x).casefold() for x in contract.get("hard_reject_phrases") or [] if clean_space(x)]
    hits = {term: lower.count(term) for term in terms if term and term in lower}
    hit_total = sum(hits.values())
    hard_hits = [term for term in hard if term in lower]
    lines = _lines(raw_value)
    paragraphs = [x for x in lines if len(x) >= 60]
    short_lines = [x for x in lines if len(x) < 45]
    # Duplicate navigation diagnostics are meaningful only when the extractor
    # preserved a menu-like set of many relatively short blocks. Long article
    # paragraphs and flattened prose are intentionally excluded.
    duplicate_candidates = [
        re.sub(r"\W+", "", x.casefold())
        for x in lines
        if 2 <= len(x) <= 180
    ]
    duplicate_count = len(duplicate_candidates) - len(set(duplicate_candidates))
    duplicate_ratio = (
        duplicate_count / max(1, len(duplicate_candidates))
        if len(duplicate_candidates) >= 8
        else 0.0
    )
    short_line_ratio = len(short_lines) / max(1, len(lines)) if len(lines) >= 8 else 0.0
    sentences = [x for x in _SENTENCE_SPLIT.split(value) if len(clean_space(x)) >= 20]
    sentence_chars = sum(len(x) for x in sentences)
    sentence_ratio = sentence_chars / max(1, len(value))
    navigation_chars = sum(len(term) * count for term, count in hits.items())
    navigation_ratio = min(1.0, navigation_chars / max(1, len(value)))
    token_count = len(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value))

    min_chars = int(os.getenv("PIF_NEWS_MIN_ARTICLE_CHARS", "300"))
    min_paragraphs = int(os.getenv("PIF_NEWS_MIN_PARAGRAPHS", "3"))
    max_nav = float(os.getenv("PIF_NEWS_MAX_NAVIGATION_RATIO", "0.25"))
    max_dup = float(os.getenv("PIF_NEWS_MAX_DUPLICATE_LINE_RATIO", "0.30"))
    min_sentence = float(os.getenv("PIF_NEWS_MIN_SENTENCE_RATIO", "0.35"))
    max_short = float(os.getenv("PIF_NEWS_MAX_SHORT_LINE_RATIO", "0.45"))
    enabled = os.getenv("PIF_NEWS_BOILERPLATE_FILTER", "true").strip().lower() in {"1", "true", "yes", "on"}

    reasons: list[str] = []
    if content_kind == "article" and len(value) < min_chars and not official:
        reasons.append("article_too_short")
    if content_kind == "article" and len(paragraphs) < min_paragraphs and len(value) < 1000 and not official:
        reasons.append("too_few_article_paragraphs")
    if navigation_ratio > max_nav or hit_total >= max(12, len(value) // 180):
        reasons.append("navigation_ratio_exceeded")
    if duplicate_ratio > max_dup:
        reasons.append("duplicate_line_ratio_exceeded")
    if sentence_ratio < min_sentence and len(value) >= 500:
        reasons.append("sentence_ratio_too_low")
    if short_line_ratio > max_short and len(lines) >= 8:
        reasons.append("short_line_ratio_exceeded")
    if hard_hits and len(value) >= 500:
        reasons.append("hard_boilerplate_phrase")
    # Navigation category matrices have many tokens but few grammatical
    # sentences. This catches the long Chinese examples without keyword-only
    # overfitting.
    if token_count >= 80 and sentence_ratio < 0.25 and short_line_ratio > 0.35:
        reasons.append("category_matrix_detected")

    contaminated = bool(enabled and reasons)
    return {
        "content_kind": content_kind,
        "content_quality_status": "boilerplate_contaminated" if contaminated else "passed",
        "article_char_count": len(value),
        "paragraph_count": len(paragraphs),
        "line_count": len(lines),
        "sentence_count": len(sentences),
        "sentence_ratio": round(sentence_ratio, 4),
        "navigation_ratio": round(navigation_ratio, 4),
        "duplicate_line_ratio": round(duplicate_ratio, 4),
        "short_line_ratio": round(short_line_ratio, 4),
        "boilerplate_hits": hits,
        "hard_boilerplate_hits": hard_hits,
        "rejection_reasons": sorted(set(reasons)),
        "boilerplate_contaminated": contaminated,
    }
