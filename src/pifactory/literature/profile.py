from __future__ import annotations

import re
from typing import Any

from ..utils import clean_space, unique_strings

CORE_SEARCH_TERM_COUNT = 5
PROFILE_POLICY_VERSION = "v15.1-frozen-five-core-terms-2"

_GENERIC_RESEARCH_TERMS = {
    "outbreak", "surveillance", "vaccine", "vaccines", "diagnosis", "diagnostic",
    "treatment", "therapy", "genome", "genomic", "epidemiology", "infection",
    "virus", "disease", "fever", "syndrome", "case", "cases", "host", "protein",
}
_BOOLEAN_RE = re.compile(r"\b(?:AND|OR|NOT)\b")

# Core concepts are identity concepts, not research questions.  These tokens are
# permitted in the post-retrieval priority vocabulary, but a multi-word core
# term combining them with a virus/disease identity is prohibited.
_DIRECTION_TOKENS = {
    "outbreak", "epidemic", "surveillance", "monitoring", "vaccine", "vaccination",
    "diagnosis", "diagnostic", "screening", "treatment", "therapy", "therapeutic",
    "antiviral", "prevention", "prophylaxis", "transmission", "spillover",
    "genomic", "genome", "sequencing", "mutation", "variant", "effectiveness",
    "wastewater", "seroprevalence", "incidence", "prevalence", "model",
    "assay", "nirsevimab", "functional", "cure",
}

def prohibited_direction_tokens(term: str) -> list[str]:
    words = {x.casefold() for x in re.findall(r"[A-Za-z]+", clean_space(term))}
    # Single-token abbreviations and names such as RSV, H5N1 and COVID-19 are
    # identity concepts.  The prohibition concerns identity + research direction.
    if len(words) <= 1:
        return []
    return sorted(words & _DIRECTION_TOKENS)


_DEFAULT_PRIORITY_TERMS = [
    {"term": "outbreak", "category": "emerging_event", "weight": 5},
    {"term": "spillover", "category": "cross_species", "weight": 5},
    {"term": "cross-species", "category": "cross_species", "weight": 5},
    {"term": "first report", "category": "novelty", "weight": 5},
    {"term": "novel host", "category": "novelty", "weight": 5},
    {"term": "new region", "category": "novelty", "weight": 4},
    {"term": "genomic surveillance", "category": "genomics", "weight": 4},
    {"term": "recombination", "category": "evolution", "weight": 4},
    {"term": "evolution", "category": "evolution", "weight": 3},
    {"term": "vaccine", "category": "countermeasure", "weight": 3},
    {"term": "therapeutic", "category": "countermeasure", "weight": 3},
    {"term": "diagnostic", "category": "diagnostics", "weight": 3},
    {"term": "clinical outcome", "category": "clinical", "weight": 3},
    {"term": "cohort", "category": "study_scale", "weight": 2},
    {"term": "systematic review", "category": "evidence_synthesis", "weight": 3},
    {"term": "public health", "category": "public_health", "weight": 2},
]

_DEFAULT_DOCUMENT_TYPE_TERMS = {
    "research": ["trial", "cohort", "case-control", "cross-sectional", "experimental study"],
    "systematic_review": ["systematic review", "meta-analysis", "scoping review"],
    "narrative_review": ["review", "narrative review", "perspective"],
    "case_report": ["case report", "case series"],
    "surveillance_report": ["surveillance report", "outbreak report", "epidemiological update"],
    "methods": ["method", "assay", "protocol", "algorithm", "model"],
    "commentary": ["editorial", "commentary", "letter", "opinion"],
}


def _concepts(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in ((profile.get("search_strategy") or {}).get("concepts") or []) if isinstance(x, dict)]


def validate_frozen_core_terms(profile: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Validate the immutable five-term retrieval contract.

    Every term must be a short standalone virus/disease identity concept.  Boolean
    expressions and generic research-direction terms are rejected.  Weekly runs
    require ``frozen=true``; only explicit profile refresh may create a new version.
    """

    strategy = profile.get("search_strategy") or {}
    concepts = _concepts(profile)
    terms = [clean_space(x.get("scholarly")) for x in concepts]
    normalized = [re.sub(r"[^a-z0-9]+", " ", x.casefold()).strip() for x in terms]
    issues: list[str] = []
    if len(concepts) != CORE_SEARCH_TERM_COUNT:
        issues.append(f"exactly {CORE_SEARCH_TERM_COUNT} core search terms are required; got {len(concepts)}")
    if len(set(normalized)) != len(normalized):
        issues.append("core search terms contain semantic duplicates")
    for index, term in enumerate(terms, 1):
        if not term:
            issues.append(f"core term {index} is empty")
            continue
        if len(term) > 120:
            issues.append(f"core term {index} is too long")
        if _BOOLEAN_RE.search(term):
            issues.append(f"core term {index} contains Boolean/query syntax: {term}")
        words = {x.casefold() for x in re.findall(r"[A-Za-z]+", term)}
        if words and words.issubset(_GENERIC_RESEARCH_TERMS):
            issues.append(f"core term {index} is generic rather than a virus identity: {term}")
        directions = prohibited_direction_tokens(term)
        if directions:
            issues.append(
                f"core term {index} combines identity with research-direction tokens {directions}: {term}"
            )
    if strategy.get("frozen") is not True:
        issues.append("search_strategy.frozen must be true during production runs")
    if strategy.get("allow_weekly_mutation") is not False:
        issues.append("search_strategy.allow_weekly_mutation must be false")
    version = clean_space(strategy.get("core_terms_version"))
    if not version:
        issues.append("search_strategy.core_terms_version is required")
    result = {
        "policy_version": PROFILE_POLICY_VERSION,
        "passed": not issues,
        "strict": strict,
        "core_term_count": len(concepts),
        "terms": terms,
        "frozen": strategy.get("frozen") is True,
        "core_terms_version": version,
        "issues": issues,
    }
    if strict and issues:
        raise RuntimeError(f"{profile.get('profile_id')}: invalid frozen five-term contract: {issues}")
    return result


def _vocabulary_source(profile: dict[str, Any]) -> dict[str, Any]:
    value = profile.get("vocabulary") or profile.get("candidate_vocabulary") or {}
    return value if isinstance(value, dict) else {}


def _vocabulary_terms(profile: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in (_vocabulary_source(profile).get(key) or []):
        if isinstance(value, dict):
            rows.append(dict(value))
        elif clean_space(value):
            rows.append({"term": clean_space(value)})
    return rows


def build_post_retrieval_vocabulary(profile: dict[str, Any]) -> dict[str, Any]:
    """Return purpose-specific vocabularies used only after provider retrieval."""

    identity = unique_strings(
        clean_space(x.get("term"))
        for key in ("identity_anchor_terms", "member_identity_terms", "disease_identity_terms")
        for x in _vocabulary_terms(profile, key)
        if clean_space(x.get("term"))
    )
    qualified = []
    for item in _vocabulary_terms(profile, "qualified_identity_terms"):
        term = clean_space(item.get("term"))
        if not term:
            continue
        qualified.append({
            "term": term,
            "required_context_terms": unique_strings(item.get("required_context_terms") or []),
            "wrong_meanings": unique_strings(item.get("wrong_meanings") or item.get("excluded_meanings") or []),
            "forbidden_without_context": True,
        })
    exclusions = unique_strings(
        clean_space(x.get("term")) for x in _vocabulary_terms(profile, "exclusion_terms") if clean_space(x.get("term"))
    )
    priority = [x for x in (_vocabulary_source(profile).get("paper_priority_terms") or []) if isinstance(x, dict)]
    if not priority:
        priority = [dict(x) for x in _DEFAULT_PRIORITY_TERMS]
    document_types = _vocabulary_source(profile).get("document_type_terms")
    if not isinstance(document_types, dict) or not document_types:
        document_types = {key: list(values) for key, values in _DEFAULT_DOCUMENT_TYPE_TERMS.items()}
    return {
        "policy_version": PROFILE_POLICY_VERSION,
        "identity_terms": identity,
        "qualified_abbreviations": qualified,
        "exclusion_terms": exclusions,
        "paper_priority_terms": priority,
        "document_type_terms": document_types,
        "query_conversion_allowed": False,
        "controlled_supplemental_query_terms": unique_strings(
            clean_space(x) for x in ((profile.get("search_strategy") or {}).get("controlled_supplemental_terms") or []) if clean_space(x)
        ),
    }
