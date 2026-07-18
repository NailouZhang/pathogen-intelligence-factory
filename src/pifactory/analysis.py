from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LLMError, LLMRouter
from .utils import clean_space, split_sentences, truncate
from .postprocess import contains_cross_field_overlap, deduplicate_structured_analysis, complete_text


ANALYSIS_POLICY_VERSION = "v10-exclusive-role-close-reading-1"

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


ROLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("background", re.compile(r"^(background|introduction|importance|rationale)\b|\b(knowledge gap|remains unclear|is unknown|has emerged)\b", re.I)),
    ("objective", re.compile(r"^(objective|objectives|aim|aims|purpose)\b|\b(we aimed|this study aimed|we sought|we investigated)\b", re.I)),
    ("design_population", re.compile(r"^(design|setting|participants|patients|population|samples|materials)\b|\b(cross-sectional|cohort|case-control|randomized|non-randomized|multicentre|multicenter|participants were|patients were|samples were)\b", re.I)),
    ("methods", re.compile(r"^(methods|methodology|procedures|statistical analysis)\b|\b(we used|we performed|was measured|were tested|sequencing|assay|regression|model was)\b", re.I)),
    ("results", re.compile(r"^(results|findings)\b|\b(we found|showed|demonstrated|was associated|were detected|increased|decreased|odds ratio|hazard ratio|confidence interval|p[ =<])\b", re.I)),
    ("interpretation", re.compile(r"^(interpretation|discussion)\b|\b(we interpret|these findings suggest|authors suggest|may indicate)\b", re.I)),
    ("conclusion", re.compile(r"^(conclusion|conclusions)\b|\b(in conclusion|we conclude|supports the)\b", re.I)),
    ("limitations", re.compile(r"^(limitations|limitation)\b|\b(limited by|small sample|selection bias|confounding|cannot establish|generalizability)\b", re.I)),
    ("implications", re.compile(r"^(implications|significance|recommendations)\b|\b(public health|surveillance|clinical practice|future research|prevention)\b", re.I)),
]


def _strip_heading(sentence: str) -> tuple[str, str | None]:
    value = clean_space(sentence)
    match = re.match(r"^(BACKGROUND|INTRODUCTION|OBJECTIVE|OBJECTIVES|AIM|AIMS|PURPOSE|DESIGN|SETTING|PARTICIPANTS|PATIENTS|METHODS|METHODOLOGY|RESULTS|FINDINGS|INTERPRETATION|DISCUSSION|CONCLUSION|CONCLUSIONS|LIMITATIONS|IMPLICATIONS)\s*[:.-]\s*", value, flags=re.I)
    if not match:
        return value, None
    heading = match.group(1).lower()
    role_map = {
        "background": "background", "introduction": "background",
        "objective": "objective", "objectives": "objective", "aim": "objective", "aims": "objective", "purpose": "objective",
        "design": "design_population", "setting": "design_population", "participants": "design_population", "patients": "design_population",
        "methods": "methods", "methodology": "methods",
        "results": "results", "findings": "results",
        "interpretation": "interpretation", "discussion": "interpretation",
        "conclusion": "conclusion", "conclusions": "conclusion",
        "limitations": "limitations", "implications": "implications",
    }
    return clean_space(value[match.end():]), role_map.get(heading)


def _sentence_role(sentence: str, default_role: str = "general") -> str:
    cleaned, heading_role = _strip_heading(sentence)
    if heading_role:
        return heading_role
    for role, pattern in ROLE_PATTERNS:
        if pattern.search(cleaned):
            return role
    return default_role


def _evidence_payload(text: str, prefix: str, max_sentences: int, default_role: str = "general") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_role = default_role
    for idx, sentence in enumerate(split_sentences(text, max_sentences), 1):
        cleaned, heading_role = _strip_heading(sentence)
        if heading_role:
            current_role = heading_role
        role = _sentence_role(sentence, current_role)
        rows.append({"id": f"{prefix}{idx}", "role": role, "text": cleaned or clean_space(sentence)})
    return rows


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
        output.extend(_evidence_payload(text, prefixes[name], budgets[name], default_role={"methods":"methods","results":"results","discussion":"interpretation","conclusion":"conclusion","abstract":"general","full_text":"general","other":"general"}.get(name,"general")))
    return output


def build_paper_evidence(work: dict[str, Any]) -> dict[str, Any]:
    abstract = clean_space(work.get("abstract"))
    evidence = _evidence_payload(abstract, "A", 36, default_role="general")
    existing = {row["text"] for row in evidence}
    for row in _section_evidence(work):
        if row["text"] not in existing:
            evidence.append(row)
            existing.add(row["text"])
    if not evidence:
        full = clean_space(work.get("full_text"))
        evidence = _evidence_payload(full, "F", 48, default_role="general")
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
        "field_evidence_hints": {
            "research_question_and_background": ["background", "objective", "general"],
            "study_design_and_population": ["design_population", "methods", "general"],
            "methods": ["methods", "design_population"],
            "main_results": ["results"],
            "interpretation_and_novelty": ["interpretation", "conclusion", "results"],
            "scientific_and_public_health_significance": ["implications", "conclusion", "interpretation"],
            "limitations_and_evidence_strength": ["limitations", "methods", "conclusion", "general"],
            "scope_and_question": ["background", "objective", "general"],
            "evidence_base_and_review_method": ["methods", "design_population", "general"],
            "consensus_and_key_conclusions": ["results", "conclusion", "interpretation"],
            "controversies_and_evidence_gaps": ["limitations", "interpretation", "general"],
            "research_and_practice_implications": ["implications", "conclusion", "interpretation"],
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
        "evidence": _evidence_payload(content, "N", 70, default_role="general"),
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


def _evidence_role_map(payload: dict[str, Any]) -> dict[str, str]:
    return {clean_space(row.get("id")): clean_space(row.get("role")) or "general" for row in payload.get("evidence") or []}


FIELD_ALLOWED_ROLES = {
    "research_question_and_background": {"background", "objective", "general"},
    "study_design_and_population": {"design_population", "methods"},
    "methods": {"methods", "design_population"},
    "main_results": {"results"},
    "interpretation_and_novelty": {"interpretation", "conclusion", "results", "general"},
    "scientific_and_public_health_significance": {"implications", "conclusion", "interpretation", "general"},
    "limitations_and_evidence_strength": {"limitations", "conclusion", "general"},
    "scope_and_question": {"background", "objective", "general"},
    "evidence_base_and_review_method": {"methods", "design_population"},
    "consensus_and_key_conclusions": {"results", "conclusion", "interpretation", "general"},
    "controversies_and_evidence_gaps": {"limitations", "interpretation", "general"},
    "research_and_practice_implications": {"implications", "conclusion", "interpretation", "general"},
}


def _paper_validator(kind: str, valid_ids: set[str], role_map: dict[str, str] | None = None):
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
            if role_map:
                allowed = FIELD_ALLOWED_ROLES.get(key, {"general"})
                cited_roles = {role_map.get(clean_space(ref), "general") for ref in refs}
                if not cited_roles.intersection(allowed):
                    return False, f"{key} cites evidence assigned to the wrong rhetorical role: {sorted(cited_roles)}"
        overlap, reason = contains_cross_field_overlap(analysis, required)
        if overlap:
            return False, reason
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
        overlap, reason = contains_cross_field_overlap(analysis, NEWS_FIELDS, threshold=0.92)
        if overlap:
            return False, reason
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


def _role_sentences(payload: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for row in payload.get("evidence") or []:
        role = clean_space(row.get("role")) or "general"
        text = clean_space(row.get("text"))
        evidence_id = clean_space(row.get("id"))
        if text and evidence_id:
            groups.setdefault(role, []).append((evidence_id, text))
    return groups


def _pick_role_text(groups: dict[str, list[tuple[str, str]]], roles: list[str], default: str, limit: int = 520) -> tuple[str, list[str]]:
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for role in roles:
        for evidence_id, text in groups.get(role, []):
            if text in seen:
                continue
            seen.add(text)
            selected.append((evidence_id, text))
            if len(selected) >= 3:
                break
        if selected:
            break
    if not selected:
        return default, []
    value, _ = complete_text(" ".join(text for _, text in selected), max_chars=limit)
    return value, [evidence_id for evidence_id, _ in selected]


def _fallback_research(payload: dict[str, Any], error: str) -> dict[str, Any]:
    groups = _role_sentences(payload)
    field_roles = {
        "research_question_and_background": ["background", "objective", "general"],
        "study_design_and_population": ["design_population", "methods", "general"],
        "methods": ["methods", "design_population"],
        "main_results": ["results"],
        "interpretation_and_novelty": ["interpretation", "conclusion", "results"],
        "scientific_and_public_health_significance": ["implications", "conclusion", "interpretation"],
        "limitations_and_evidence_strength": ["limitations", "methods", "general"],
    }
    defaults = {
        "research_question_and_background": "The supplied evidence does not clearly report the research question or background.",
        "study_design_and_population": "Study design, setting, and population were not clearly reported in the supplied evidence.",
        "methods": "Core methods were not clearly reported in the supplied evidence.",
        "main_results": "Main results were not clearly reported in the supplied evidence.",
        "interpretation_and_novelty": "The authors' interpretation and novelty were not clearly reported in the supplied evidence.",
        "scientific_and_public_health_significance": "Scientific and public-health significance could not be determined beyond the supplied evidence.",
        "limitations_and_evidence_strength": "The fallback uses only supplied abstract or open-text evidence; unreported limitations were not inferred.",
    }
    analysis: dict[str, str] = {}
    refs: dict[str, list[str]] = {}
    for field in RESEARCH_FIELDS:
        analysis[field], refs[field] = _pick_role_text(groups, field_roles[field], defaults[field])
        if not refs[field]:
            all_rows = [pair for rows in groups.values() for pair in rows]
            if all_rows:
                refs[field] = [all_rows[0][0]]
    return {
        "status": "fallback_source_extract",
        "fallback_policy": "role_aligned",
        "kind": "research",
        "analysis": analysis,
        "summary_en": " ".join(analysis.values()),
        "evidence_ids": refs,
        "confidence": "low",
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }


def _fallback_review(payload: dict[str, Any], error: str) -> dict[str, Any]:
    groups = _role_sentences(payload)
    field_roles = {
        "scope_and_question": ["background", "objective", "general"],
        "evidence_base_and_review_method": ["methods", "design_population", "general"],
        "consensus_and_key_conclusions": ["results", "conclusion", "interpretation"],
        "controversies_and_evidence_gaps": ["limitations", "interpretation", "general"],
        "research_and_practice_implications": ["implications", "conclusion", "interpretation"],
    }
    defaults = {
        "scope_and_question": "The review scope and central question were not clearly reported in the supplied evidence.",
        "evidence_base_and_review_method": "The review method, databases, and evidence base were not clearly reported.",
        "consensus_and_key_conclusions": "The main consensus could not be reliably determined from the supplied evidence.",
        "controversies_and_evidence_gaps": "Controversies and evidence gaps were not clearly reported in the supplied evidence.",
        "research_and_practice_implications": "Research and practice implications require cautious interpretation from the available evidence.",
    }
    analysis: dict[str, str] = {}
    refs: dict[str, list[str]] = {}
    for field in REVIEW_FIELDS:
        analysis[field], refs[field] = _pick_role_text(groups, field_roles[field], defaults[field], limit=560)
        if not refs[field]:
            all_rows = [pair for rows in groups.values() for pair in rows]
            if all_rows:
                refs[field] = [all_rows[0][0]]
    return {
        "status": "fallback_source_extract",
        "fallback_policy": "role_aligned",
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
            validator=_paper_validator(kind, valid_ids, _evidence_role_map(payload)),
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
        data = deduplicate_structured_analysis(data, payload, kind)
        work["analysis"] = data
        work["analysis_ready"] = True
    except LLMError as exc:
        fallback = (
            _fallback_research(payload, clean_space(exc)[:600])
            if kind == "research"
            else _fallback_review(payload, clean_space(exc)[:600])
        )
        work["analysis"] = deduplicate_structured_analysis(fallback, payload, kind)
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
        data = deduplicate_structured_analysis(data, payload, "news")
        article["analysis"] = data
        article["analysis_ready"] = True
    except LLMError as exc:
        article["analysis"] = deduplicate_structured_analysis(
            _fallback_news(payload, clean_space(exc)[:600]), payload, "news"
        )
        article["analysis_ready"] = True
    return article
