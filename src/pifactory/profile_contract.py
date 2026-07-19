from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .utils import clean_space, unique_strings, utc_now_iso

SCHEMA_VERSION = "3.2"

BROAD_DISEASE_WORDS = {
    "fever", "pneumonia", "encephalitis", "hepatitis", "gastroenteritis",
    "rash", "thrombocytopenia", "infection", "disease", "bronchiolitis",
}


def _term_object(term: str, *, term_type: str, source_urls: list[str], safe: bool = True) -> dict[str, Any]:
    return {
        "term": clean_space(term),
        "normalized_term": clean_space(term).casefold(),
        "type": term_type,
        "language": "zh" if re.search(r"[\u4e00-\u9fff]", term) else "en",
        "safe_to_use_alone": bool(safe),
        "qualification_required": [],
        "source_urls": list(source_urls),
        "confidence": "high",
    }


def deterministic_profile(seed: dict[str, Any], documents: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = seed.get("candidate_vocabulary") or {}
    source_urls = [x.get("url") for x in documents if x.get("usable") and x.get("url")]
    if not source_urls:
        source_urls = [x.get("url") for x in seed.get("authoritative_sources") or [] if isinstance(x, dict) and x.get("url")]

    forbidden_standalone = {
        clean_space(x).casefold()
        for x in unique_strings(candidates.get("display_only_terms") or [])
    }
    identity = [
        _term_object(
            x,
            term_type="virus_name",
            source_urls=source_urls,
            safe=clean_space(x).casefold() not in forbidden_standalone,
        )
        for x in unique_strings(candidates.get("identity_anchor_terms") or [])
    ]
    members = [
        {
            **_term_object(
                x,
                term_type="member_name",
                source_urls=source_urls,
                safe=clean_space(x).casefold() not in forbidden_standalone,
            ),
            "member_level": "member_or_subtype",
            "high_precision": clean_space(x).casefold() not in forbidden_standalone,
        }
        for x in unique_strings(candidates.get("member_identity_terms") or [])
    ]
    diseases = []
    for term in unique_strings(candidates.get("disease_identity_terms") or []):
        low = term.casefold()
        safe = not (low in BROAD_DISEASE_WORDS or len(low) < 5)
        diseases.append({
            **_term_object(term, term_type="disease_name", source_urls=source_urls, safe=safe),
            "specificity": "high" if safe else "context_only",
        })

    qualified = []
    for item in candidates.get("qualified_identity_terms") or []:
        if not isinstance(item, dict):
            continue
        term = clean_space(item.get("term"))
        contexts = unique_strings(item.get("required_context_terms") or [])
        if not term or not contexts:
            continue
        qualified.append({
            "term": term,
            "required_context_terms": contexts,
            "forbidden_without_context": True,
            "query_fragment": clean_space(item.get("query_fragment_seed")),
            "ambiguity_reason": "seed-defined ambiguous or abbreviated identity term",
            "source_urls": source_urls,
            "confidence": "high",
        })

    contexts = [
        {
            "term": x,
            "category": "topic_context",
            "may_use_only_after_identity": True,
            "source_urls": source_urls,
            "confidence": "high",
        }
        for x in unique_strings(candidates.get("context_terms") or [])
    ]
    display_only = [
        {
            "term": x,
            "reason": "seed explicitly forbids standalone retrieval use",
            "allowed_uses": ["display", "classification", "relevance_scoring"],
        }
        for x in unique_strings(candidates.get("display_only_terms") or [])
    ]
    exclusions = [
        {
            "term": x,
            "reason": "seed-defined recurrent non-target entity or scope",
            "applies_to_modes": ["strict_core"],
            "risk_of_over_exclusion": "medium",
            "source_or_test_evidence": "manual topic-boundary table",
        }
        for x in unique_strings(candidates.get("exclusion_terms") or [])
    ]

    search_strategy = deepcopy(seed.get("search_strategy") or {})
    search_strategy["max_concepts"] = 5
    search_strategy["frozen"] = True
    search_strategy["allow_weekly_mutation"] = False
    search_strategy.setdefault("core_terms_version", "2.0")
    search_strategy.setdefault("generated_from", "authoritative_sources_and_manual_seed")

    profile = {
        "schema_version": SCHEMA_VERSION,
        "profile_id": seed["profile_id"],
        "status": "ready",
        "display_name_en": seed.get("display_name_en") or seed["profile_id"],
        "display_name_zh": seed.get("display_name_zh") or seed.get("display_name_en") or seed["profile_id"],
        "target_scope": deepcopy(seed.get("target_scope") or {}),
        "sources": [
            {k: x.get(k) for k in ("url", "organization", "name", "role", "retrieved_at", "sha256", "usable", "failure_reason", "cache_status")}
            for x in documents
        ],
        "vocabulary": {
            "identity_anchor_terms": identity,
            "qualified_identity_terms": qualified,
            "member_identity_terms": members,
            "disease_identity_terms": diseases,
            "context_terms": contexts,
            "display_only_terms": display_only,
            "exclusion_terms": exclusions,
            "paper_priority_terms": deepcopy(candidates.get("paper_priority_terms") or []),
            "document_type_terms": deepcopy(candidates.get("document_type_terms") or {}),
        },
        "search_strategy": search_strategy,
        "news_identity_terms_zh": list(seed.get("news_identity_terms_zh") or []),
        "query_policy": deepcopy(seed.get("query_policy") or {}),
        "retrieval_policy": deepcopy(seed.get("retrieval_policy") or {}),
        "source_policy": deepcopy(seed.get("source_policy") or {}),
        "translation_glossary": [],
        "blocking_issues": [],
        "manual_review_required": False,
        "generated_by": "deterministic_seed_contract",
        "generated_at": utc_now_iso(),
    }
    return profile


def _entries(data: Any) -> list[dict[str, Any]]:
    return [x for x in (data or []) if isinstance(x, dict)]


def validate_profile(profile: dict[str, Any], seed: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if profile.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version must be 3.2")
    if profile.get("profile_id") != seed.get("profile_id"):
        issues.append("profile_id mismatch")
    vocabulary = profile.get("vocabulary") or {}
    anchors = _entries(vocabulary.get("identity_anchor_terms"))
    members = _entries(vocabulary.get("member_identity_terms"))
    qualified = _entries(vocabulary.get("qualified_identity_terms"))
    if not any(x.get("safe_to_use_alone") for x in anchors + members):
        issues.append("no safe identity anchor")
    for item in qualified:
        if not item.get("term") or not item.get("required_context_terms"):
            issues.append(f"qualified term missing context: {item.get('term')}")
    forbidden = {clean_space(x.get("term")).casefold() for x in _entries(vocabulary.get("display_only_terms"))}
    unsafe = [
        clean_space(x.get("term"))
        for x in anchors + members
        if x.get("safe_to_use_alone") and clean_space(x.get("term")).casefold() in forbidden
    ]
    if unsafe:
        issues.append(f"display-only terms entered identity group: {unsafe}")
    allowed = {clean_space(x).casefold() for x in (seed.get("target_scope") or {}).get("allowed_members") or []}
    for item in members:
        term = clean_space(item.get("term")).casefold()
        if allowed and term not in allowed:
            issues.append(f"out-of-scope member: {item.get('term')}")
    strategy = profile.get("search_strategy") or {}
    concepts = [x for x in strategy.get("concepts") or [] if isinstance(x, dict)]
    scholarly = [clean_space(x.get("scholarly")) for x in concepts if clean_space(x.get("scholarly"))]
    normalized = {re.sub(r"[^a-z0-9]+", " ", x.casefold()).strip() for x in scholarly}
    if len(concepts) != 5:
        issues.append(f"search_strategy must contain exactly 5 concepts, got {len(concepts)}")
    if strategy.get("frozen") is not True:
        issues.append("search_strategy.frozen must be true")
    if strategy.get("allow_weekly_mutation") is not False:
        issues.append("search_strategy.allow_weekly_mutation must be false")
    if not clean_space(strategy.get("core_terms_version")):
        issues.append("search_strategy.core_terms_version is required")
    if len(scholarly) != len(normalized):
        issues.append("search_strategy contains semantic duplicate scholarly terms")
    try:
        from .literature.profile import validate_frozen_core_terms
        contract = validate_frozen_core_terms(profile, strict=False)
        issues.extend(x for x in contract.get("issues", []) if x not in issues)
    except Exception as exc:
        issues.append(f"core-term contract validation failed: {clean_space(exc)}")
    exact = (profile.get("source_policy") or {}).get("exact_urls_only")
    discovery = (profile.get("source_policy") or {}).get("allow_search_discovery")
    if exact is not True or discovery is not False:
        issues.append("source policy must be exact-only and search discovery disabled")
    if not profile.get("sources"):
        issues.append("missing authoritative source records")
    return not issues, issues


def merge_llm_refinement(base: dict[str, Any], proposal: dict[str, Any], seed: dict[str, Any]) -> dict[str, Any]:
    """Accept a conservative LLM refinement without allowing scope expansion.

    Allowed members and exclusions remain governed by seed.yaml. The model may
    add source-supported aliases, structured context terms and translations,
    but cannot replace the target boundary or source policy.
    """
    result = deepcopy(base)
    if not isinstance(proposal, dict):
        return result
    result["translation_glossary"] = proposal.get("translation_glossary") or result.get("translation_glossary") or []
    proposed_vocab = proposal.get("vocabulary") or {}
    base_vocab = result["vocabulary"]
    source_urls = {x.get("url") for x in result.get("sources") or [] if x.get("url")}

    for key in ("identity_anchor_terms", "qualified_identity_terms", "disease_identity_terms", "context_terms", "display_only_terms", "paper_priority_terms"):
        accepted = []
        for item in _entries(proposed_vocab.get(key)):
            urls = set(item.get("source_urls") or [])
            if urls and not urls.issubset(source_urls):
                continue
            term = clean_space(item.get("term"))
            if not term:
                continue
            accepted.append(item)
        if accepted:
            # Deterministic seed entries stay first and cannot be removed.
            existing = base_vocab.get(key) or []
            seen = {clean_space(x.get("term")).casefold() for x in _entries(existing)}
            for item in accepted:
                if clean_space(item.get("term")).casefold() not in seen:
                    existing.append(item)
            base_vocab[key] = existing

    proposed_document_types = proposed_vocab.get("document_type_terms")
    if isinstance(proposed_document_types, dict) and proposed_document_types:
        result["vocabulary"]["document_type_terms"] = deepcopy(proposed_document_types)

    # On explicit profile refresh only, the LLM may select a new five-term set
    # from source-supported names. Weekly production runs consume the frozen
    # profile and never call this merge path.
    proposed_strategy = proposal.get("search_strategy") or {}
    proposed_concepts = [x for x in proposed_strategy.get("concepts") or [] if isinstance(x, dict)]
    if len(proposed_concepts) == 5:
        allowed_terms = {
            clean_space(x.get("term")).casefold()
            for key in ("identity_anchor_terms", "member_identity_terms", "disease_identity_terms")
            for x in _entries(result["vocabulary"].get(key))
            if clean_space(x.get("term"))
        }
        accepted_concepts = []
        seen = set()
        for index, item in enumerate(proposed_concepts, 1):
            term = clean_space(item.get("scholarly"))
            norm = re.sub(r"[^a-z0-9]+", " ", term.casefold()).strip()
            if not term or norm in seen or term.casefold() not in allowed_terms:
                accepted_concepts = []
                break
            if re.search(r"\b(?:AND|OR|NOT)\b|[()\[\]{}]", term, flags=re.I):
                accepted_concepts = []
                break
            seen.add(norm)
            accepted_concepts.append({
                "id": clean_space(item.get("id")) or f"core_{index}",
                "scholarly": term,
                "news_en": term,
                "news_zh": clean_space(item.get("news_zh")),
                "role": clean_space(item.get("role")) or "identity",
                "priority": index,
            })
        if len(accepted_concepts) == 5:
            result["search_strategy"]["concepts"] = accepted_concepts
            result["search_strategy"]["core_terms_version"] = clean_space(proposed_strategy.get("core_terms_version")) or "2.0"
    result["search_strategy"]["max_concepts"] = 5
    result["search_strategy"]["frozen"] = True
    result["search_strategy"]["allow_weekly_mutation"] = False

    # Member and exclusion lists are intentionally not expanded by the LLM.
    result["generated_by"] = proposal.get("generated_by") or "llm_refined_with_seed_boundary"
    result["manual_review_required"] = bool(proposal.get("manual_review_required", False))
    return result
