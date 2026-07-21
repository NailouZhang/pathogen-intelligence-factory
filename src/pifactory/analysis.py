from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .llm import LLMError, LLMRouter, classify_llm_failure, summarize_attempt_categories
from .evidence_selector import select_evidence_rows
from .utils import clean_space, split_sentences, truncate
from .postprocess import contains_cross_field_overlap, deduplicate_structured_analysis, complete_text
from .language_contract import annotate_source_language, sanitize_english_analysis


ANALYSIS_POLICY_VERSION = "v17.1-source-language-safe-analysis-1"

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
    abstract = clean_space(work.get("abstract"))[:1200]
    haystack = clean_space(" ".join([types, title, abstract])).casefold()
    configured = work.get("document_type_terms") or {}
    scores: dict[str, int] = {}
    if isinstance(configured, dict):
        for category, terms in configured.items():
            score = sum(1 for term in (terms or []) if clean_space(term) and clean_space(term).casefold() in haystack)
            if score:
                scores[str(category)] = score
    if scores:
        category = sorted(scores, key=lambda key: (scores[key], key in {"systematic_review", "narrative_review"}), reverse=True)[0]
        work["document_type_category"] = category
        work["document_type_term_scores"] = scores
        if category in {"systematic_review", "narrative_review"}:
            return "review"
        return "research"
    work["document_type_category"] = "review" if REVIEW_HINTS.search(haystack) else "research"
    work["document_type_term_scores"] = {}
    return "review" if REVIEW_HINTS.search(haystack) else "research"


ROLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("background", re.compile(
        r"^(background|introduction|importance|rationale)\b|"
        r"\b(knowledge gap|remains unclear|is unknown|has emerged|is poorly understood|little is known|lack of)\b",
        re.I,
    )),
    ("objective", re.compile(
        r"^(objective|objectives|aim|aims|purpose)\b|"
        r"\b(we aimed|this study aimed|the study aimed|we sought|we investigated|we evaluated|we assessed|our objective)\b",
        re.I,
    )),
    # Methods and results are evaluated before generic population cues so a
    # sentence such as "12 participants were seropositive" is not mislabeled
    # merely because it contains the word participants.
    ("methods", re.compile(
        r"^(methods|methodology|procedures|statistical analysis)\b|"
        r"\b(we used|we performed|we conducted|we analy[sz]ed|we measured|we tested|we sequenced|we modeled|"
        r"was measured|were measured|were tested|was assessed|were assessed|data were collected|samples were collected|"
        r"sequencing|assay|elisa|pcr|rt-pcr|serolog|immunoassay|regression|phylogen|variant calling|machine learning|"
        r"force[- ]of[- ]infection|meta-analysis|systematic search|databases were searched|prisma|model was|using a|using an)\b",
        re.I,
    )),
    ("results", re.compile(
        r"^(results|findings)\b|"
        r"\b(we found|we observed|we identified|showed|demonstrated|revealed|reported|was associated|were associated|"
        r"were detected|was detected|were seropositive|was seropositive|increased|decreased|survived|died|accounted for|"
        r"compared with|compared to|odds ratio|hazard ratio|risk ratio|confidence interval|p\s*[=<]|"
        r"\d+(?:\.\d+)?%|\d+\s+of\s+\d+)\b",
        re.I,
    )),
    ("design_population", re.compile(
        r"^(design|setting|participants|patients|population|samples|materials)\b|"
        r"\b(cross-sectional|cohort|case-control|randomi[sz]ed|non-randomi[sz]ed|retrospective|prospective|"
        r"multicentre|multicenter|case series|case report|animal model|participants were|patients were|samples were|"
        r"individuals were|subjects were|enrolled|included|recruited|hospitali[sz]ed|specimens from|samples from|"
        r"n\s*[=:]\s*\d+|\d+\s+(patients|participants|subjects|samples|animals|cases|records|studies))\b",
        re.I,
    )),
    ("interpretation", re.compile(
        r"^(interpretation|discussion)\b|"
        r"\b(we interpret|these findings suggest|these results suggest|authors suggest|authors stated|may indicate|"
        r"indicates that|supports the hypothesis|to our knowledge|first report|first nationwide|novel)\b",
        re.I,
    )),
    ("conclusion", re.compile(
        r"^(conclusion|conclusions)\b|"
        r"\b(in conclusion|we conclude|we suggest|the study concludes|supports the|highlight(?:s|ed) the need)\b",
        re.I,
    )),
    ("limitations", re.compile(
        r"^(limitations|limitation)\b|"
        r"\b(limited by|small sample|selection bias|recall bias|confounding|cannot establish|could not establish|"
        r"generalizability|generalisability|single[- ]cent(?:er|re)|abstract only|further studies are needed|"
        r"evidence remains limited|heterogeneity|data were unavailable)\b",
        re.I,
    )),
    ("implications", re.compile(
        r"^(implications|significance|recommendations)\b|"
        r"\b(public health|surveillance|clinical practice|future research|further study|prevention|preparedness|risk assessment|"
        r"diagnosis|vaccination|treatment|monitoring|control measures|policy)\b",
        re.I,
    )),
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
    """Build a bounded evidence pack; full text is locally selected, never sent wholesale."""

    abstract = clean_space(work.get("abstract"))
    analysis_level = clean_space(work.get("analysis_level")) or (
        "L2_retrieved_fulltext_evidence" if work.get("full_text") or work.get("full_text_sections") else "L1_abstract_only"
    )
    evidence = _evidence_payload(abstract, "A", 36, default_role="general")
    selector_audit: dict[str, Any] = {
        "selector": "abstract_only",
        "original_rows": len(evidence),
        "selected_rows": len(evidence),
        "original_chars": sum(len(row["text"]) for row in evidence),
        "selected_chars": sum(len(row["text"]) for row in evidence),
    }

    if analysis_level.startswith(("L2", "L3")):
        existing = {row["text"] for row in evidence}
        for row in _section_evidence(work):
            if row["text"] not in existing:
                evidence.append(row)
                existing.add(row["text"])
        if not evidence:
            # Last-resort local parsing is still bounded before any remote call.
            full = clean_space(work.get("full_text"))
            evidence = _evidence_payload(full, "F", 160, default_role="general")
        evidence, selector_audit = select_evidence_rows(
            evidence,
            max_chars=max(2500, int(os.getenv("PIF_ANALYSIS_EVIDENCE_MAX_CHARS", "9000"))),
        )
    elif not evidence:
        # A paper without an abstract is allowed to use a tiny locally selected
        # full-text pack, but never the whole document.
        full_rows = _evidence_payload(clean_space(work.get("full_text")), "F", 120, default_role="general")
        evidence, selector_audit = select_evidence_rows(
            full_rows,
            max_chars=max(2500, int(os.getenv("PIF_ANALYSIS_EVIDENCE_MAX_CHARS", "9000"))),
        )
        analysis_level = "L2_retrieved_fulltext_evidence" if evidence else "L1_abstract_only"

    evidence_scope = (
        "retrieved_fulltext_evidence"
        if analysis_level.startswith(("L2", "L3")) and evidence
        else ("abstract_only" if abstract else "metadata_only")
    )
    return {
        "policy_version": ANALYSIS_POLICY_VERSION,
        "analysis_level": analysis_level,
        "paper_id": work.get("paper_id"),
        "title": work.get("title"),
        "source_language": work.get("source_language") or "und",
        "bibliography": {
            "authors": (work.get("authors") or [])[:20],
            "journal": work.get("journal"),
            "published_date": work.get("availability_date") or work.get("online_date") or work.get("first_publication_date"),
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
        "evidence_scope": evidence_scope,
        "evidence_selector": selector_audit,
    }


def build_news_evidence(article: dict[str, Any]) -> dict[str, Any]:
    content = clean_space(article.get("content") or article.get("excerpt"))
    minimum = max(1, int(os.getenv("PIF_NEWS_BRIEF_MIN_SOURCE_CHARS", "500")))
    return {
        "policy_version": ANALYSIS_POLICY_VERSION,
        "news_id": article.get("news_id"),
        "title": article.get("title"),
        "source_language": article.get("source_language") or "und",
        "publisher": article.get("publisher") or article.get("source"),
        "published_date": article.get("published_date"),
        "url": article.get("resolved_url") or article.get("url"),
        "content_status": article.get("content_status"),
        "content_method": article.get("content_method"),
        "source_char_count": len(content),
        "generate_brief": len(content) >= minimum,
        "evidence": _evidence_payload(content, "N", 70, default_role="general"),
    }



def compact_analysis_payload(payload: dict[str, Any], max_chars: int | None = None) -> dict[str, Any]:
    """Bound LLM input while retaining role-diverse evidence and stable IDs."""

    limit = max(4000, int(max_chars or os.getenv("PIF_ANALYSIS_MAX_PROMPT_CHARS", "14000")))
    raw_text = json.dumps(payload, ensure_ascii=False)
    if len(raw_text) <= limit:
        output = dict(payload)
        output["prompt_compaction"] = {
            "applied": False,
            "original_chars": len(raw_text),
            "final_chars": len(raw_text),
            "original_evidence": len(payload.get("evidence") or []),
            "retained_evidence": len(payload.get("evidence") or []),
        }
        return output

    rows = [row for row in payload.get("evidence") or [] if isinstance(row, dict)]
    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    # First reserve two sentences per rhetorical role so methods/results are not
    # lost when long open-text sections dominate the payload.
    role_counts: dict[str, int] = {}
    for row in rows:
        role = clean_space(row.get("role")) or "general"
        if role_counts.get(role, 0) >= 2:
            continue
        evidence_id = clean_space(row.get("id"))
        if evidence_id:
            selected.append(row)
            selected_ids.add(evidence_id)
            role_counts[role] = role_counts.get(role, 0) + 1

    base = {key: value for key, value in payload.items() if key != "evidence"}
    # Fill the remaining budget in source order.
    for row in rows:
        evidence_id = clean_space(row.get("id"))
        if not evidence_id or evidence_id in selected_ids:
            continue
        trial = dict(base)
        trial["evidence"] = selected + [row]
        if len(json.dumps(trial, ensure_ascii=False)) > limit:
            continue
        selected.append(row)
        selected_ids.add(evidence_id)

    positions = {clean_space(row.get("id")): index for index, row in enumerate(rows)}
    selected.sort(key=lambda row: positions.get(clean_space(row.get("id")), 10**9))
    output = dict(base)
    output["evidence"] = selected
    final_chars = len(json.dumps(output, ensure_ascii=False))
    output["prompt_compaction"] = {
        "applied": True,
        "original_chars": len(raw_text),
        "final_chars": final_chars,
        "original_evidence": len(rows),
        "retained_evidence": len(selected),
        "limit_chars": limit,
    }
    return output

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
            raw_value = analysis.get(key)
            if not isinstance(raw_value, str):
                return False, f"{key} must be a string, got {type(raw_value).__name__}"
            value = clean_space(raw_value)
            if len(value) < 12:
                return False, f"{key} too short"
            if len(re.findall(r"[\u4e00-\u9fff]", value)) > max(2, int(len(value) * 0.05)):
                return False, f"{key} must be English for the source-language entity"
            if _contains_bad_placeholder(value):
                return False, f"{key} contains invalid placeholder"
            refs = evidence_ids.get(key)
            if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
                return False, f"{key} lacks string evidence ids"
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
        if not isinstance(data.get("summary_en"), str):
            return False, "summary_en must be a string"
        summary = clean_space(data.get("summary_en"))
        if len(summary) < 80 or len(summary.split()) > 260:
            return False, "summary_en length invalid"
        if clean_space(data.get("confidence")) not in {"high", "moderate", "low"}:
            return False, "confidence invalid"
        return True, "ok"

    return validator


def _news_validator(valid_ids: set[str], *, require_brief: bool = True):
    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        analysis = data.get("analysis")
        evidence_ids = data.get("evidence_ids")
        if not isinstance(analysis, dict) or not isinstance(evidence_ids, dict):
            return False, "analysis or evidence_ids missing"
        for key in NEWS_FIELDS:
            raw_value = analysis.get(key)
            if not isinstance(raw_value, str):
                return False, f"{key} must be a string, got {type(raw_value).__name__}"
            value = clean_space(raw_value)
            if len(value) < 8:
                return False, f"{key} too short"
            if len(re.findall(r"[\u4e00-\u9fff]", value)) > max(2, int(len(value) * 0.05)):
                return False, f"{key} must be English for the source-language entity"
            refs = evidence_ids.get(key)
            if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
                return False, f"{key} lacks string evidence ids"
            if any(clean_space(ref) not in valid_ids for ref in refs):
                return False, f"{key} contains unknown evidence ids"
        overlap, reason = contains_cross_field_overlap(analysis, NEWS_FIELDS, threshold=0.92)
        if overlap:
            return False, reason
        if not isinstance(data.get("brief_en"), str):
            return False, "brief_en must be a string"
        brief = clean_space(data.get("brief_en"))
        words = len(brief.split())
        if require_brief and (words < 100 or words > 220):
            return False, "brief_en must contain 100-220 words when a brief is requested"
        if not require_brief and brief:
            return False, "brief_en must be empty for short-source records"
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


def _ordered_evidence(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "id": clean_space(row.get("id")),
            "role": clean_space(row.get("role")) or "general",
            "text": clean_space(row.get("text")),
        }
        for row in payload.get("evidence") or []
        if clean_space(row.get("id")) and clean_space(row.get("text"))
    ]


FALLBACK_FIELD_CUES: dict[str, re.Pattern[str]] = {
    "research_question_and_background": re.compile(
        r"\b(aim|objective|purpose|investigat|evaluat|assess|knowledge gap|unknown|unclear|risk|burden|causes?)\b",
        re.I,
    ),
    "study_design_and_population": re.compile(
        r"\b(retrospective|prospective|cross-sectional|cohort|case-control|randomi[sz]ed|study|survey|patients?|"
        r"participants?|subjects?|workers?|samples?|specimens?|animals?|records?|cases?|between\s+\w+\s+and|"
        r"\bn\s*[=:]\s*\d+|\d+\s+(patients?|participants?|samples?|animals?|cases?|records?))\b",
        re.I,
    ),
    "methods": re.compile(
        r"\b(using|used|performed|conducted|analy[sz]ed|measured|tested|sequenced|assay|elisa|pcr|rt-pcr|"
        r"regression|model|phylogen|variant calling|sampling|collected|questionnaire|database|machine learning|"
        r"meta-analysis|systematic search|force[- ]of[- ]infection)\b",
        re.I,
    ),
    "main_results": re.compile(
        r"\b(found|observed|identified|detected|showed|demonstrated|revealed|associated|increased|decreased|"
        r"survived|died|significant|confidence interval|odds ratio|hazard ratio|risk ratio|\d+(?:\.\d+)?%|"
        r"\d+\s+of\s+\d+|p\s*[=<])\b",
        re.I,
    ),
    "interpretation_and_novelty": re.compile(
        r"\b(suggest|indicat|interpret|conclude|supports?|to our knowledge|first|novel|driven by|unlikely)\b",
        re.I,
    ),
    "scientific_and_public_health_significance": re.compile(
        r"\b(surveillance|public health|prevention|preparedness|diagnos|treat|vaccin|monitor|risk assessment|"
        r"clinical practice|future research|control|policy|need for)\b",
        re.I,
    ),
    "limitations_and_evidence_strength": re.compile(
        r"\b(limit|bias|confound|small sample|single[- ]cent|generaliz|heterogeneity|cannot establish|"
        r"further studies|abstract only|uncertain|evidence remains)\b",
        re.I,
    ),
    "scope_and_question": re.compile(
        r"\b(review|overview|summari[sz]|scope|aim|focus|examines?|addresses?|virology|clinical|epidemiology)\b",
        re.I,
    ),
    "evidence_base_and_review_method": re.compile(
        r"\b(systematic|meta-analysis|database|search|prisma|eligibility|included|studies|reports|screened|"
        r"literature review|narrative review|scoping review)\b",
        re.I,
    ),
    "consensus_and_key_conclusions": re.compile(
        r"\b(conclude|consensus|evidence shows|studies show|associated|consistent|most common|pooled|"
        r"increased|decreased|characterized|indicates?)\b",
        re.I,
    ),
    "controversies_and_evidence_gaps": re.compile(
        r"\b(gap|unclear|unknown|conflict|heterogeneity|limited|lack|insufficient|controvers|few studies|"
        r"future studies|remains to be)\b",
        re.I,
    ),
    "research_and_practice_implications": re.compile(
        r"\b(should|need|recommend|surveillance|practice|prevention|diagnosis|treatment|vaccination|future research|"
        r"preparedness|policy|monitoring|priority)\b",
        re.I,
    ),
}


FALLBACK_ALLOWED_ROLES: dict[str, set[str]] = {
    key: set(value) for key, value in FIELD_ALLOWED_ROLES.items()
}


FALLBACK_POSITION_TARGETS: dict[str, float] = {
    "research_question_and_background": 0.08,
    "study_design_and_population": 0.25,
    "methods": 0.38,
    "main_results": 0.62,
    "interpretation_and_novelty": 0.82,
    "scientific_and_public_health_significance": 0.92,
    "limitations_and_evidence_strength": 0.95,
    "scope_and_question": 0.12,
    "evidence_base_and_review_method": 0.32,
    "consensus_and_key_conclusions": 0.62,
    "controversies_and_evidence_gaps": 0.82,
    "research_and_practice_implications": 0.93,
}


COMPUTATIONAL_FALLBACK_RE = re.compile(
    r"\b(mathematical|compartmental|fractional[- ]order|caputo|simulation|algorithm|machine learning|"
    r"neural network|pinn|bayesian|regression|model(?:ing|ling)?|optimization|sensitivity analysis|"
    r"cost[- ]effectiveness|phylogenetic|genomic|bioinformatic)\b",
    re.I,
)


def _fallback_track(payload: dict[str, Any]) -> str:
    text = " ".join(row.get("text", "") for row in _ordered_evidence(payload))
    return "computational_or_modeling" if COMPUTATIONAL_FALLBACK_RE.search(text) else "empirical_or_clinical"


def _fallback_candidate_score(field: str, row: dict[str, str], index: int, total: int, *, track: str = "empirical_or_clinical") -> float:
    role = row["role"]
    text = row["text"]
    allowed = FALLBACK_ALLOWED_ROLES.get(field, {"general"})
    score = 0.0
    if role in allowed:
        score += 80.0 if role != "general" else 28.0
    cue = FALLBACK_FIELD_CUES.get(field)
    if cue and cue.search(text):
        score += 58.0
    position = index / max(1, total - 1) if total > 1 else 0.5
    target = FALLBACK_POSITION_TARGETS.get(field, 0.5)
    score += max(0.0, 24.0 - abs(position - target) * 38.0)
    if field == "main_results" and re.search(r"\d", text):
        score += 18.0
    if track == "computational_or_modeling":
        if field == "methods" and re.search(r"\b(model|simulation|algorithm|network|regression|optimization|sensitivity)\b", text, re.I):
            score += 26.0
        if field == "main_results" and re.search(r"\b(predicted|estimated|simulation|scenario|accuracy|performance|reproduction number|r0|cost[- ]effective)\b", text, re.I):
            score += 22.0
        if field == "study_design_and_population" and re.search(r"\b(dataset|records?|surveillance data|parameters?|scenarios?|model)\b", text, re.I):
            score += 12.0
    if field in {"study_design_and_population", "evidence_base_and_review_method"} and re.search(r"\d", text):
        score += 8.0
    if field in {"interpretation_and_novelty", "scientific_and_public_health_significance", "research_and_practice_implications"} and position > 0.65:
        score += 8.0
    return score


def _fallback_default(field: str, payload: dict[str, Any]) -> str:
    scope = clean_space(payload.get("evidence_scope"))
    defaults = {
        "research_question_and_background": "The supplied evidence does not clearly state the research question or its rationale.",
        "study_design_and_population": "The supplied evidence does not clearly state the study design, setting, or study population.",
        "methods": "The supplied evidence does not clearly state the core experimental, analytical, or statistical methods.",
        "main_results": "The supplied evidence does not clearly state a directly observed main result.",
        "interpretation_and_novelty": "The supplied evidence does not clearly distinguish the authors' interpretation or the study's novelty.",
        "scientific_and_public_health_significance": "The supplied evidence does not clearly state a specific scientific or public-health implication.",
        "limitations_and_evidence_strength": (
            f"This deterministic extraction is based on {scope.replace('_', ' ') or 'the supplied evidence'}. "
            "The source does not explicitly report enough limitations for an independent appraisal, so confidence is low."
        ),
        "scope_and_question": "The supplied evidence does not clearly state the review scope or central question.",
        "evidence_base_and_review_method": "The supplied evidence does not clearly state the review method, databases, eligibility process, or evidence base.",
        "consensus_and_key_conclusions": "The supplied evidence does not clearly state a stable consensus or central conclusion.",
        "controversies_and_evidence_gaps": "The supplied evidence does not clearly state controversies or evidence gaps.",
        "research_and_practice_implications": "The supplied evidence does not clearly state a specific research or practice implication.",
    }
    return defaults[field]


def _fallback_extract_fields(payload: dict[str, Any], fields: list[str], *, limit: int) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str], str]:
    rows = _ordered_evidence(payload)
    track = _fallback_track(payload)
    used: set[str] = set()
    extracted: dict[str, str] = {}
    extracted_refs: dict[str, list[str]] = {}
    extracted_sources: dict[str, str] = {}
    # Reserve the most diagnostic method/result sentences before broader fields
    # such as design/background can consume them.
    if fields == RESEARCH_FIELDS:
        selection_order = [
            "methods", "main_results", "study_design_and_population",
            "research_question_and_background", "interpretation_and_novelty",
            "scientific_and_public_health_significance", "limitations_and_evidence_strength",
        ]
    else:
        selection_order = [
            "evidence_base_and_review_method", "consensus_and_key_conclusions",
            "scope_and_question", "controversies_and_evidence_gaps",
            "research_and_practice_implications",
        ]

    for field in selection_order:
        ranked: list[tuple[float, int, dict[str, str]]] = []
        for index, row in enumerate(rows):
            if row["id"] in used:
                continue
            ranked.append((_fallback_candidate_score(field, row, index, len(rows), track=track), index, row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected: list[dict[str, str]] = []
        for score, _, row in ranked:
            if score < 36.0:
                continue
            selected.append(row)
            # One strong sentence is safer than consuming evidence that belongs
            # to another element; structured headings already preserve complete
            # method/result sentences before this rescue path is used.
            break
        if selected:
            # Preserve source order after semantic scoring selected the candidate set.
            positions = {row["id"]: index for index, row in enumerate(rows)}
            selected.sort(key=lambda row: positions[row["id"]])
            value, _ = complete_text(" ".join(row["text"] for row in selected), max_chars=limit)
            extracted[field] = value
            extracted_refs[field] = [row["id"] for row in selected]
            used.update(extracted_refs[field])
            extracted_sources[field] = "role_cue_position"
        else:
            extracted[field] = _fallback_default(field, payload)
            extracted_refs[field] = []
            extracted_sources[field] = "explicit_absence_statement"
    analysis = {field: extracted[field] for field in fields}
    refs = {field: extracted_refs[field] for field in fields}
    sources = {field: extracted_sources[field] for field in fields}
    return analysis, refs, sources, track


def _llm_failure_details(exc: LLMError) -> tuple[list[dict[str, Any]], str, str]:
    attempts = list(getattr(exc, "attempts", []) or [])
    if not attempts:
        raw = clean_space(exc)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                attempts = [row for row in parsed if isinstance(row, dict)]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    category = clean_space(getattr(exc, "category", "")) or summarize_attempt_categories(attempts)
    if category in {"", "unknown"}:
        category = classify_llm_failure(exc)
    summary = clean_space(exc)[:1000]
    return attempts, category, summary


def _fallback_research(payload: dict[str, Any], error: str, *, attempts: list[dict[str, Any]] | None = None, failure_category: str = "unknown") -> dict[str, Any]:
    analysis, refs, field_sources, fallback_track = _fallback_extract_fields(payload, RESEARCH_FIELDS, limit=560)
    return {
        "status": "fallback_source_extract",
        "fallback_policy": "role_cue_position_rescue",
        "fallback_field_sources": field_sources,
        "fallback_track": fallback_track,
        "kind": "research",
        "analysis": analysis,
        "summary_en": " ".join(analysis.values()),
        "evidence_ids": refs,
        "confidence": "low",
        "failure_category": failure_category,
        "attempts": list(attempts or []),
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }


def _fallback_review(payload: dict[str, Any], error: str, *, attempts: list[dict[str, Any]] | None = None, failure_category: str = "unknown") -> dict[str, Any]:
    analysis, refs, field_sources, fallback_track = _fallback_extract_fields(payload, REVIEW_FIELDS, limit=590)
    return {
        "status": "fallback_source_extract",
        "fallback_policy": "role_cue_position_rescue",
        "fallback_field_sources": field_sources,
        "fallback_track": fallback_track,
        "kind": "review",
        "analysis": analysis,
        "summary_en": " ".join(analysis.values()),
        "evidence_ids": refs,
        "confidence": "low",
        "failure_category": failure_category,
        "attempts": list(attempts or []),
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }



def _crosscheck_agreement(primary: dict[str, Any], secondary: dict[str, Any], kind: str) -> float:
    fields = RESEARCH_FIELDS if kind == "research" else REVIEW_FIELDS
    first = primary.get("analysis") or {}
    second = secondary.get("analysis") or {}
    scores: list[float] = []
    for field in fields:
        left = clean_space(first.get(field)).lower()
        right = clean_space(second.get(field)).lower()
        if left and right:
            scores.append(SequenceMatcher(None, left, right).ratio())
    return round(sum(scores) / len(scores), 3) if scores else 0.0

def analyze_paper(work: dict[str, Any], llm: LLMRouter, prompts_dir: Path) -> dict[str, Any]:
    source_language = annotate_source_language(work, kind="paper")
    kind = classify_paper(work)
    work["paper_type"] = kind
    payload = build_paper_evidence(work)
    if not payload["evidence"]:
        work["analysis"] = {
            "status": "not_run_no_abstract_or_fulltext",
            "kind": kind,
            "analysis": {},
            "summary_en": "",
            "attempts": [],
            "failure_category": "no_evidence",
            "policy_version": ANALYSIS_POLICY_VERSION,
        }
        work["analysis_ready"] = False
        return work
    prompt_file = "research_analysis.md" if kind == "research" else "review_analysis.md"
    system = (prompts_dir / prompt_file).read_text(encoding="utf-8")
    prompt_payload = compact_analysis_payload(payload)
    valid_ids = _valid_evidence_ids(prompt_payload)
    require_brief = bool(payload.get("generate_brief"))
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(prompt_payload, ensure_ascii=False),
            provider_order=getattr(llm, "provider_order", lambda purpose: None)("extract"),
            validator=_paper_validator(kind, valid_ids, _evidence_role_map(prompt_payload)),
            max_models_per_provider=2,
            temperature=0.05,
            task_name=f"paper_{kind}_analysis",
        )
        data = result.data if isinstance(result.data, dict) else {}
        data.update(
            {
                "status": "passed",
                "kind": kind,
                "provider": result.provider,
                "model": result.model,
                "attempts": result.attempts,
                "failure_category": "",
                "prompt_compaction": prompt_payload.get("prompt_compaction") or {},
                "policy_version": ANALYSIS_POLICY_VERSION,
            }
        )
        data = deduplicate_structured_analysis(data, payload, kind)
        data = sanitize_english_analysis(data, kind="paper", source_language=source_language)
        data["analysis_level"] = payload.get("analysis_level")
        data["evidence_scope"] = payload.get("evidence_scope")
        data["evidence_selector"] = payload.get("evidence_selector") or {}
        if clean_space(payload.get("analysis_level")).startswith("L3"):
            rescue_order = tuple(name for name in getattr(llm, "provider_order", lambda purpose: ())("rescue") if name != result.provider)
            try:
                cross = llm.json_task(
                    system=system,
                    prompt=json.dumps(prompt_payload, ensure_ascii=False),
                    provider_order=rescue_order,
                    validator=_paper_validator(kind, valid_ids, _evidence_role_map(prompt_payload)),
                    max_models_per_provider=1,
                    temperature=0.0,
                    task_name=f"paper_{kind}_crosscheck",
                )
                cross_data = cross.data if isinstance(cross.data, dict) else {}
                agreement = _crosscheck_agreement(data, cross_data, kind)
                data["crosscheck"] = {
                    "status": "passed" if agreement >= 0.35 else "disagreement",
                    "provider": cross.provider,
                    "model": cross.model,
                    "agreement": agreement,
                    "attempts": cross.attempts,
                }
                if agreement < 0.35:
                    data["confidence"] = "low"
            except LLMError as cross_exc:
                data["crosscheck"] = {
                    "status": "failed",
                    "failure_category": getattr(cross_exc, "category", "unknown"),
                    "attempts": getattr(cross_exc, "attempts", []) or [],
                    "error": clean_space(cross_exc)[:700],
                }
        work["analysis"] = data
        work["analysis_ready"] = True
    except LLMError as exc:
        attempts, category, error = _llm_failure_details(exc)
        fallback = (
            _fallback_research(payload, error, attempts=attempts, failure_category=category)
            if kind == "research"
            else _fallback_review(payload, error, attempts=attempts, failure_category=category)
        )
        fallback["prompt_compaction"] = prompt_payload.get("prompt_compaction") or {}
        work["analysis"] = sanitize_english_analysis(
            deduplicate_structured_analysis(fallback, payload, kind),
            kind="paper", source_language=source_language,
        )
        work["analysis_ready"] = True
    return work


NEWS_FALLBACK_CUES: dict[str, re.Pattern[str]] = {
    "time": re.compile(r"\b(20\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|yesterday|this week|on \w+ \d{1,2})\b", re.I),
    "location_and_population": re.compile(r"\b(in|at|from|across|residents?|patients?|passengers?|workers?|children|adults?|contacts?|community|county|province|state|city|country|hospital|ship)\b", re.I),
    "event": re.compile(r"\b(reported|confirmed|announced|detected|identified|investigating|outbreak|case|cases|infection|exposure|death|hospitali[sz]ed|quarantine)\b", re.I),
    "scale_impact_and_risk": re.compile(r"\b(\d+|risk|spread|transmission|severe|fatal|deaths?|cases?|affected|stable|no additional|increase|decrease)\b", re.I),
    "response_status_and_uncertainty": re.compile(r"\b(officials?|authority|authorities|testing|investigation|advised|recommended|monitoring|tracing|response|pending|unclear|unknown|confirmatory|prevention)\b", re.I),
}


def _fallback_news(payload: dict[str, Any], error: str, *, attempts: list[dict[str, Any]] | None = None, failure_category: str = "unknown") -> dict[str, Any]:
    rows = _ordered_evidence(payload)
    used: set[str] = set()
    analysis: dict[str, str] = {}
    refs: dict[str, list[str]] = {}
    published = clean_space(payload.get("published_date"))
    title = clean_space(payload.get("title"))
    defaults = {
        "time": published or "The supplied source does not clearly report when the event occurred.",
        "location_and_population": "The supplied source does not clearly identify the location or affected population.",
        "event": title or "The supplied source does not clearly describe the event.",
        "scale_impact_and_risk": "The supplied source does not clearly report the scale, impact, or risk.",
        "response_status_and_uncertainty": "The supplied source does not clearly report the response status or unresolved information.",
    }
    position_targets = {
        "time": 0.10,
        "location_and_population": 0.20,
        "event": 0.28,
        "scale_impact_and_risk": 0.58,
        "response_status_and_uncertainty": 0.85,
    }
    for field in NEWS_FIELDS:
        ranked: list[tuple[float, int, dict[str, str]]] = []
        for index, row in enumerate(rows):
            if row["id"] in used:
                continue
            position = index / max(1, len(rows) - 1) if len(rows) > 1 else 0.5
            score = max(0.0, 24.0 - abs(position - position_targets[field]) * 38.0)
            if NEWS_FALLBACK_CUES[field].search(row["text"]):
                score += 60.0
            if field in {"event", "scale_impact_and_risk"} and re.search(r"\d", row["text"]):
                score += 10.0
            ranked.append((score, index, row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected = next((row for score, _, row in ranked if score >= 38.0), None)
        if selected:
            analysis[field], _ = complete_text(selected["text"], max_chars=520)
            refs[field] = [selected["id"]]
            used.add(selected["id"])
        else:
            analysis[field] = defaults[field]
            refs[field] = []
    brief = truncate(" ".join(row["text"] for row in rows[:7]) or title, 1200)
    return {
        "status": "fallback_source_extract",
        "fallback_policy": "cue_and_position_rescue",
        "analysis": analysis,
        "brief_en": brief,
        "summary_en": brief,
        "evidence_ids": refs,
        "source_assessment": "unclear",
        "confidence": "low",
        "failure_category": failure_category,
        "attempts": list(attempts or []),
        "error": error,
        "policy_version": ANALYSIS_POLICY_VERSION,
    }


def analyze_news(article: dict[str, Any], llm: LLMRouter, prompts_dir: Path) -> dict[str, Any]:
    source_language = annotate_source_language(article, kind="news")
    payload = build_news_evidence(article)
    if not payload["evidence"]:
        article["analysis"] = {
            "status": "not_run_no_content",
            "analysis": {},
            "brief_en": "",
            "attempts": [],
            "failure_category": "no_evidence",
            "policy_version": ANALYSIS_POLICY_VERSION,
        }
        article["analysis_ready"] = False
        return article
    system = (prompts_dir / "news_analysis.md").read_text(encoding="utf-8")
    prompt_payload = compact_analysis_payload(payload)
    valid_ids = _valid_evidence_ids(prompt_payload)
    require_brief = bool(payload.get("generate_brief"))
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(prompt_payload, ensure_ascii=False),
            provider_order=getattr(llm, "provider_order", lambda purpose: None)("extract"),
            validator=_news_validator(valid_ids, require_brief=require_brief),
            max_models_per_provider=2,
            temperature=0.05,
            task_name="news_analysis",
        )
        data = result.data if isinstance(result.data, dict) else {}
        if require_brief:
            data["brief_generation"] = "llm_from_verified_body"
        else:
            source_text = clean_space(article.get("content") or article.get("excerpt"))
            data["brief_en"] = source_text
            data["brief_generation"] = "source_short_evidence_no_llm_expansion"
        data["summary_en"] = clean_space(data.get("brief_en"))
        data.update(
            {
                "status": "passed",
                "provider": result.provider,
                "model": result.model,
                "attempts": result.attempts,
                "failure_category": "",
                "prompt_compaction": prompt_payload.get("prompt_compaction") or {},
                "policy_version": ANALYSIS_POLICY_VERSION,
            }
        )
        data = deduplicate_structured_analysis(data, payload, "news")
        data = sanitize_english_analysis(data, kind="news", source_language=source_language)
        article["analysis"] = data
        article["analysis_ready"] = True
    except LLMError as exc:
        attempts, category, error = _llm_failure_details(exc)
        fallback = _fallback_news(payload, error, attempts=attempts, failure_category=category)
        if not require_brief:
            fallback["brief_en"] = clean_space(article.get("content") or article.get("excerpt"))
            fallback["summary_en"] = fallback["brief_en"]
            fallback["brief_generation"] = "source_short_evidence_no_llm_expansion"
        fallback["prompt_compaction"] = prompt_payload.get("prompt_compaction") or {}
        article["analysis"] = sanitize_english_analysis(
            deduplicate_structured_analysis(fallback, payload, "news"),
            kind="news", source_language=source_language,
        )
        article["analysis_ready"] = True
    return article
