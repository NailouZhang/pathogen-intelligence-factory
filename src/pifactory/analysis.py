from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LLMError, LLMRouter
from .utils import clean_space, split_sentences, truncate


ANALYSIS_POLICY_VERSION = "v8-typed-evidence-analysis-1"

REVIEW_HINTS = re.compile(
    r"\b(review|systematic review|meta-analysis|narrative review|scoping review|umbrella review|viewpoint|perspective|commentary|consensus statement)\b",
    flags=re.I,
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


def classify_paper(work: dict[str, Any]) -> str:
    types = " ".join(str(x) for x in work.get("publication_types") or [])
    title = clean_space(work.get("title"))
    abstract = clean_space(work.get("abstract"))[:900]
    if REVIEW_HINTS.search(" ".join([types, title, abstract])):
        return "review"
    return "research"


def _evidence_payload(text: str, prefix: str, max_sentences: int) -> list[dict[str, str]]:
    return [
        {"id": f"{prefix}{idx}", "text": sentence}
        for idx, sentence in enumerate(split_sentences(text, max_sentences), 1)
    ]


def _section_evidence(work: dict[str, Any]) -> list[dict[str, str]]:
    sections = work.get("full_text_sections") or {}
    if not isinstance(sections, dict):
        return []
    output: list[dict[str, str]] = []
    prefixes = {
        "methods": "M",
        "results": "R",
        "discussion": "D",
        "conclusion": "C",
        "abstract": "B",
        "full_text": "F",
        "other": "O",
    }
    budgets = {
        "methods": 10,
        "results": 14,
        "discussion": 8,
        "conclusion": 6,
        "abstract": 18,
        "full_text": 18,
        "other": 5,
    }
    for name in ("methods", "results", "discussion", "conclusion", "abstract", "full_text", "other"):
        text = clean_space(sections.get(name))
        if not text:
            continue
        output.extend(_evidence_payload(text, prefixes[name], budgets[name]))
    return output


def build_paper_evidence(work: dict[str, Any]) -> dict[str, Any]:
    abstract = clean_space(work.get("abstract"))
    evidence = _evidence_payload(abstract, "A", 28)
    existing = {row["text"] for row in evidence}
    for row in _section_evidence(work):
        if row["text"] not in existing:
            evidence.append(row)
            existing.add(row["text"])
    if not evidence:
        full = clean_space(work.get("full_text"))
        evidence = _evidence_payload(full, "F", 36)
    return {
        "policy_version": ANALYSIS_POLICY_VERSION,
        "paper_id": work.get("paper_id"),
        "title": work.get("title"),
        "bibliography": {
            "authors": (work.get("authors") or [])[:20],
            "journal": work.get("journal"),
            "published_date": work.get("online_date") or work.get("first_publication_date") or work.get("availability_date"),
            "year": work.get("year"),
            "doi": work.get("doi"),
            "publication_types": work.get("publication_types") or [],
            "evidence_level": work.get("evidence_level"),
        },
        "evidence": evidence,
        "evidence_scope": (
            "abstract_and_open_fulltext_sections"
            if work.get("full_text") or work.get("full_text_sections")
            else ("abstract_only" if abstract else "metadata_only")
        ),
    }


def build_news_evidence(article: dict[str, Any]) -> dict[str, Any]:
    content = clean_space(article.get("content"))
    return {
        "policy_version": ANALYSIS_POLICY_VERSION,
        "news_id": article.get("news_id"),
        "title": article.get("title"),
        "publisher": article.get("publisher") or article.get("source"),
        "published_date": article.get("published_date"),
        "url": article.get("resolved_url") or article.get("url"),
        "content_status": article.get("content_status"),
        "content_method": article.get("content_method"),
        "evidence": _evidence_payload(content, "N", 55),
    }


def _valid_evidence_ids(payload: dict[str, Any]) -> set[str]:
    return {clean_space(row.get("id")) for row in payload.get("evidence") or [] if clean_space(row.get("id"))}


def _contains_bad_placeholder(value: Any) -> bool:
    text = clean_space(value).lower()
    return any(
        marker in text
        for marker in (
            "translation unavailable",
            "翻译暂不可用",
            "internal error",
            "as an ai",
            "i cannot",
        )
    )


def _paper_validator(kind: str, valid_ids: set[str]):
    required = RESEARCH_FIELDS if kind == "research" else REVIEW_FIELDS

    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        analysis = data.get("analysis")
        evidence_ids = data.get("evidence_ids")
        if not isinstance(analysis, dict):
            return False, "analysis missing"
        if not isinstance(evidence_ids, dict):
            return False, "evidence_ids missing"
        for key in required:
            value = clean_space(analysis.get(key))
            if len(value) < 12:
                return False, f"{key} too short"
            if _contains_bad_placeholder(value):
                return False, f"{key} contains invalid placeholder"
            refs = evidence_ids.get(key)
            if not isinstance(refs, list) or not refs:
                return False, f"{key} lacks evidence ids"
            unknown = [ref for ref in refs if clean_space(ref) not in valid_ids]
            if unknown:
                return False, f"{key} contains unknown evidence ids"
        summary = clean_space(data.get("summary_en"))
        if len(summary) < 80 or len(summary.split()) > 260:
            return False, "summary_en length invalid"
        if clean_space(data.get("confidence")) not in {"high", "moderate", "low"}:
            return False, "confidence invalid"
        return True, "ok"

    return validator


def _news_validator(valid_ids: set[str]):
    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        analysis = data.get("analysis")
        evidence_ids = data.get("evidence_ids")
        if not isinstance(analysis, dict) or not isinstance(evidence_ids, dict):
            return False, "analysis or evidence_ids missing"
        for key in NEWS_FIELDS:
            value = clean_space(analysis.get(key))
            if len(value) < 8:
                return False, f"{key} too short"
            refs = evidence_ids.get(key)
            if not isinstance(refs, list) or not refs:
                return False, f"{key} lacks evidence ids"
            if any(clean_space(ref) not in valid_ids for ref in refs):
                return False, f"{key} contains unknown evidence ids"
        brief = clean_space(data.get("brief_en"))
        words = len(brief.split())
        if words < 55 or words > 170:
            return False, "brief_en must contain 55-170 words"
        if clean_space(data.get("source_assessment")) not in {
            "official",
            "reputable_media",
            "secondary_media",
            "aggregator",
            "unclear",
        }:
            return False, "source_assessment invalid"
        if clean_space(data.get("confidence")) not in {"high", "moderate", "low"}:
            return False, "confidence invalid"
        return True, "ok"

    return validator


def _fallback_research(payload: dict[str, Any], error: str) -> dict[str, Any]:
    rows = payload.get("evidence") or []
    texts = [clean_space(row.get("text")) for row in rows if clean_space(row.get("text"))]
    ids = [row.get("id") for row in rows if row.get("id")]

    def value(index: int, default: str) -> str:
        return truncate(texts[index] if index < len(texts) else default, 360)

    analysis = {
        "research_question_and_background": value(0, "The supplied evidence does not clearly report the research question."),
        "study_design_and_population": value(1, "Study design, setting, or population was not clearly reported in the supplied evidence."),
        "methods": value(2, "Core methods were not clearly reported in the supplied evidence."),
        "main_results": truncate(" ".join(texts[3:6]) or "Main results were not clearly reported in the supplied evidence.", 520),
        "interpretation_and_novelty": value(6, "The authors' interpretation or novelty could not be reliably determined from the supplied evidence."),
        "scientific_and_public_health_significance": value(7, "Scientific or public-health significance requires cautious interpretation from the available evidence."),
        "limitations_and_evidence_strength": "This deterministic fallback is based only on the supplied abstract or open-text fragments; unreported details were not inferred.",
    }
    refs = {key: [ids[min(index, len(ids) - 1)]] if ids else [] for index, key in enumerate(RESEARCH_FIELDS)}
    return {
        "status": "fallback_source_extract",
        "kind": "research",
        "analysis": analysis,
        "summary_en": " ".join(analysis.values()),
        "evidence_ids": refs,
        "confidence": "low",
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }


def _fallback_review(payload: dict[str, Any], error: str) -> dict[str, Any]:
    rows = payload.get("evidence") or []
    texts = [clean_space(row.get("text")) for row in rows if clean_space(row.get("text"))]
    ids = [row.get("id") for row in rows if row.get("id")]

    def value(index: int, default: str) -> str:
        return truncate(texts[index] if index < len(texts) else default, 420)

    analysis = {
        "scope_and_question": value(0, "The review scope and central question were not clearly reported in the supplied evidence."),
        "evidence_base_and_review_method": value(1, "The review method, databases, or evidence base were not clearly reported."),
        "consensus_and_key_conclusions": truncate(" ".join(texts[2:5]) or "The main consensus could not be reliably determined from the supplied evidence.", 560),
        "controversies_and_evidence_gaps": value(5, "Evidence gaps and controversies require assessment from the full review."),
        "research_and_practice_implications": value(6, "Research and practice implications require cautious interpretation from the available evidence."),
    }
    refs = {key: [ids[min(index, len(ids) - 1)]] if ids else [] for index, key in enumerate(REVIEW_FIELDS)}
    return {
        "status": "fallback_source_extract",
        "kind": "review",
        "analysis": analysis,
        "summary_en": " ".join(analysis.values()),
        "evidence_ids": refs,
        "confidence": "low",
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }


def analyze_paper(work: dict[str, Any], llm: LLMRouter, prompts_dir: Path) -> dict[str, Any]:
    kind = classify_paper(work)
    work["paper_type"] = kind
    payload = build_paper_evidence(work)
    if not payload["evidence"]:
        work["analysis"] = {
            "status": "not_run_no_abstract_or_fulltext",
            "kind": kind,
            "analysis": {},
            "summary_en": "",
            "policy_version": ANALYSIS_POLICY_VERSION,
        }
        work["analysis_ready"] = False
        return work
    prompt_file = "research_analysis.md" if kind == "research" else "review_analysis.md"
    system = (prompts_dir / prompt_file).read_text(encoding="utf-8")
    valid_ids = _valid_evidence_ids(payload)
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(payload, ensure_ascii=False),
            validator=_paper_validator(kind, valid_ids),
            max_models_per_provider=2,
            temperature=0.05,
        )
        data = result.data if isinstance(result.data, dict) else {}
        data.update(
            {
                "status": "passed",
                "kind": kind,
                "provider": result.provider,
                "model": result.model,
                "attempts": result.attempts,
                "policy_version": ANALYSIS_POLICY_VERSION,
            }
        )
        work["analysis"] = data
        work["analysis_ready"] = True
    except LLMError as exc:
        work["analysis"] = (
            _fallback_research(payload, clean_space(exc)[:600])
            if kind == "research"
            else _fallback_review(payload, clean_space(exc)[:600])
        )
        work["analysis_ready"] = True
    return work


def _fallback_news(payload: dict[str, Any], error: str) -> dict[str, Any]:
    rows = payload.get("evidence") or []
    texts = [clean_space(row.get("text")) for row in rows if clean_space(row.get("text"))]
    ids = [row.get("id") for row in rows if row.get("id")]
    title = clean_space(payload.get("title"))
    published = clean_space(payload.get("published_date")) or "not reported"
    event_text = truncate(" ".join(texts[:4]) or title, 650)
    analysis = {
        "time": published,
        "location_and_population": "The location or affected population was not reliably extracted by the deterministic fallback.",
        "event": event_text or "The event could not be reliably described from the supplied body text.",
        "scale_impact_and_risk": "Case counts, impacts, and risk were not inferred beyond the supplied body text.",
        "response_status_and_uncertainty": "Official response and unresolved information require confirmation from the original source.",
    }
    refs = {key: [ids[min(index, len(ids) - 1)]] if ids else [] for index, key in enumerate(NEWS_FIELDS)}
    brief = truncate(" ".join(texts[:7]) or title, 1200)
    return {
        "status": "fallback_source_extract",
        "analysis": analysis,
        "brief_en": brief,
        "summary_en": brief,
        "evidence_ids": refs,
        "source_assessment": "unclear",
        "confidence": "low",
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }


def analyze_news(article: dict[str, Any], llm: LLMRouter, prompts_dir: Path) -> dict[str, Any]:
    payload = build_news_evidence(article)
    if not payload["evidence"]:
        article["analysis"] = {
            "status": "not_run_no_content",
            "analysis": {},
            "brief_en": "",
            "policy_version": ANALYSIS_POLICY_VERSION,
        }
        article["analysis_ready"] = False
        return article
    system = (prompts_dir / "news_analysis.md").read_text(encoding="utf-8")
    valid_ids = _valid_evidence_ids(payload)
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(payload, ensure_ascii=False),
            validator=_news_validator(valid_ids),
            max_models_per_provider=2,
            temperature=0.05,
        )
        data = result.data if isinstance(result.data, dict) else {}
        data["summary_en"] = clean_space(data.get("brief_en"))
        data.update(
            {
                "status": "passed",
                "provider": result.provider,
                "model": result.model,
                "attempts": result.attempts,
                "policy_version": ANALYSIS_POLICY_VERSION,
            }
        )
        article["analysis"] = data
        article["analysis_ready"] = True
    except LLMError as exc:
        article["analysis"] = _fallback_news(payload, clean_space(exc)[:600])
        article["analysis_ready"] = True
    return article
