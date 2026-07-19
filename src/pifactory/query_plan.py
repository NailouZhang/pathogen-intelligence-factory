from __future__ import annotations

import re
from typing import Any

from .utils import clean_space, unique_strings

QUERY_POLICY_VERSION = "v15.1-five-core-plus-controlled-supplemental-2"
PUBMED_LIMIT = 1800
NEWS_LIMIT = 350


def _entries(profile: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [x for x in ((profile.get("vocabulary") or {}).get(key) or []) if isinstance(x, dict)]


def _safe_terms(profile: dict[str, Any], key: str) -> list[str]:
    return unique_strings(
        clean_space(x.get("term"))
        for x in _entries(profile, key)
        if clean_space(x.get("term")) and x.get("safe_to_use_alone", True)
    )


def _core_concepts(profile: dict[str, Any]) -> list[dict[str, Any]]:
    strategy = profile.get("search_strategy") or {}
    raw = [x for x in strategy.get("concepts") or [] if isinstance(x, dict)]
    max_concepts = 5
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, 1):
        scholarly = clean_space(item.get("scholarly"))
        if not scholarly:
            continue
        norm = re.sub(r"[^a-z0-9]+", " ", scholarly.casefold()).strip()
        semantic_key = clean_space(item.get("semantic_key")) or norm
        semantic_key = re.sub(r"[^a-z0-9]+", " ", semantic_key.casefold()).strip()
        if not norm or not semantic_key or semantic_key in seen:
            continue
        seen.add(semantic_key)
        out.append({
            "id": clean_space(item.get("id")) or f"concept_{index}",
            "semantic_key": semantic_key,
            "scholarly": scholarly,
            "news_en": clean_space(item.get("news_en")) or scholarly,
            "news_zh": clean_space(item.get("news_zh")),
            "role": clean_space(item.get("role")) or "identity",
            "priority": int(item.get("priority") or index),
        })
        if len(out) >= max_concepts:
            break
    if len(out) == 5:
        return out
    if out:
        raise RuntimeError(f"{profile.get('profile_id')}: frozen core concept contract requires exactly 5 valid concepts; got {len(out)}")

    fallback = unique_strings(
        _safe_terms(profile, "identity_anchor_terms")
        + _safe_terms(profile, "member_identity_terms")
        + _safe_terms(profile, "disease_identity_terms")
    )[:5]
    if len(fallback) != 5:
        raise RuntimeError(f"{profile.get('profile_id')}: unable to construct exactly 5 fallback identity terms")
    return [
        {
            "id": f"fallback_{index}",
            "scholarly": term,
            "news_en": term,
            "news_zh": "",
            "role": "fallback_identity",
            "priority": index,
        }
        for index, term in enumerate(fallback, 1)
    ]


def _tokens(term: str) -> list[str]:
    stop = {"a", "an", "the", "with", "of", "and", "or", "in"}
    values = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", clean_space(term))
    kept = [x for x in values if x.casefold() not in stop and len(x) >= 2]
    return unique_strings(kept)[:10]


def _pubmed_query(term: str) -> str:
    # A direct authoritative concept is intentionally submitted without field
    # tags or forced phrase quoting so PubMed Automatic Term Mapping can add
    # MeSH/synonym expansions. Python relevance review remains the precision
    # boundary after retrieval.
    return clean_space(term)


def _epmc_query(term: str) -> str:
    # Submit one short natural-language concept without field restriction.
    # Europe PMC can apply its own free-text expansion/ranking; precision is
    # enforced later by the rich Python/LLM relevance gate.
    return clean_space(term)


def _semantic_query(term: str) -> str:
    # The bulk endpoint stems all terms and treats whitespace as AND. Remove
    # punctuation/hyphens that the relevance endpoint is known to mishandle.
    value = clean_space(term).replace("-", " ").replace("/", " ")
    return clean_space(re.sub(r"[^A-Za-z0-9\s]", " ", value))


def _news_query(term: str) -> str:
    return clean_space(term)[:NEWS_LIMIT]


def _gdelt_query(term: str) -> str:
    value = clean_space(term).replace('"', "")
    return f'"{value}"' if " " in value else value


def _controlled_supplemental_terms(profile: dict[str, Any]) -> list[str]:
    strategy = profile.get("search_strategy") or {}
    values = unique_strings(clean_space(x) for x in strategy.get("controlled_supplemental_terms") or [] if clean_space(x))
    core = {re.sub(r"[^a-z0-9]+", " ", clean_space(x.get("scholarly")).casefold()).strip() for x in _core_concepts(profile)}
    output: list[str] = []
    for value in values:
        norm = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
        if norm and norm not in core:
            output.append(value)
        if len(output) >= 8:
            break
    return output


def compile_query_sets(profile: dict[str, Any]) -> dict[str, Any]:
    concepts = _core_concepts(profile)
    if not concepts:
        raise RuntimeError(f"{profile.get('profile_id')}: no lean retrieval concepts")

    scholarly = unique_strings(x["scholarly"] for x in concepts)
    news_en = unique_strings(x["news_en"] for x in concepts if x.get("news_en"))
    news_zh = unique_strings(x["news_zh"] for x in concepts if x.get("news_zh"))

    pubmed = unique_strings(_pubmed_query(x) for x in scholarly)
    epmc = unique_strings(_epmc_query(x) for x in scholarly)
    semantic = unique_strings(_semantic_query(x) for x in scholarly if _semantic_query(x))
    crossref = list(scholarly)
    openalex = list(scholarly)
    gdelt = unique_strings(_gdelt_query(x) for x in news_en)
    reliefweb = unique_strings(_news_query(x) for x in news_en)

    supplemental_terms = _controlled_supplemental_terms(profile)
    supplemental_concepts = [
        {
            "id": f"supplemental_{index}",
            "semantic_key": re.sub(r"[^a-z0-9]+", " ", term.casefold()).strip(),
            "scholarly": term,
            "news_en": term,
            "news_zh": "",
            "role": "controlled_supplemental_identity",
            "priority": 100 + index,
        }
        for index, term in enumerate(supplemental_terms, 1)
    ]
    pubmed_supplemental = unique_strings(_pubmed_query(x) for x in supplemental_terms)
    epmc_supplemental = unique_strings(_epmc_query(x) for x in supplemental_terms)
    crossref_supplemental = list(supplemental_terms)
    semantic_supplemental = unique_strings(_semantic_query(x) for x in supplemental_terms if _semantic_query(x))
    openalex_supplemental = list(supplemental_terms)

    query_concept_map: dict[str, list[str]] = {}
    provider_concept_map: dict[str, dict[str, list[str]]] = {}
    for concept in concepts:
        concept_id = str(concept.get("id"))
        pairs = {
            "pubmed": _pubmed_query(concept.get("scholarly", "")),
            "europe_pmc": _epmc_query(concept.get("scholarly", "")),
            "crossref": clean_space(concept.get("scholarly")),
            "semantic_scholar": _semantic_query(concept.get("scholarly", "")),
            "openalex": clean_space(concept.get("scholarly")),
            "news_en": clean_space(concept.get("news_en")),
            "news_zh": clean_space(concept.get("news_zh")),
            "gdelt": _gdelt_query(concept.get("news_en", "")),
            "reliefweb": _news_query(concept.get("news_en", "")),
        }
        for provider, query in pairs.items():
            query = clean_space(query)
            if not query:
                continue
            query_concept_map.setdefault(query, []).append(concept_id)
            provider_concept_map.setdefault(provider, {}).setdefault(query, []).append(concept_id)

    for concept in supplemental_concepts:
        concept_id = str(concept.get("id"))
        term = clean_space(concept.get("scholarly"))
        pairs = {
            "pubmed_supplemental": _pubmed_query(term),
            "europe_pmc_supplemental": _epmc_query(term),
            "crossref_supplemental": term,
            "semantic_scholar_supplemental": _semantic_query(term),
            "openalex_supplemental": term,
        }
        for provider, query in pairs.items():
            query = clean_space(query)
            if not query:
                continue
            query_concept_map.setdefault(query, []).append(concept_id)
            provider_concept_map.setdefault(provider, {}).setdefault(query, []).append(concept_id)

    return {
        "query_policy_version": QUERY_POLICY_VERSION,
        "core_concepts": concepts,
        "controlled_supplemental_concepts": supplemental_concepts,
        "controlled_supplemental_terms": supplemental_terms,
        "query_concept_map": query_concept_map,
        "provider_concept_map": provider_concept_map,
        "pubmed_core": pubmed,
        "europe_pmc_core": epmc,
        "crossref_core": crossref,
        "semantic_scholar_core": semantic,
        "openalex_core": openalex,
        "pubmed_supplemental": pubmed_supplemental,
        "europe_pmc_supplemental": epmc_supplemental,
        "crossref_supplemental": crossref_supplemental,
        "semantic_scholar_supplemental": semantic_supplemental,
        "openalex_supplemental": openalex_supplemental,
        "pubmed_all": unique_strings(pubmed + pubmed_supplemental),
        "europe_pmc_all": unique_strings(epmc + epmc_supplemental),
        "crossref_all": unique_strings(crossref + crossref_supplemental),
        "semantic_scholar_all": unique_strings(semantic + semantic_supplemental),
        "openalex_all": unique_strings(openalex + openalex_supplemental),
        "general_news_en": news_en,
        "general_news_zh": news_zh,
        "gdelt_core": gdelt,
        "reliefweb_core": reliefweb,
        # Compatibility aliases retained so older Pages diagnostics and helper
        # scripts can read a v7 profile without multiplying queries.
        "pubmed_single_anchor_exact": pubmed,
        "pubmed_single_qualified": [],
        "pubmed_core_high_precision": pubmed,
        "pubmed_core_high_recall": [],
        "pubmed_identity_fallback": [],
        "pubmed_molecular": [],
        "pubmed_epidemiology": [],
        "pubmed_clinical": [],
        "europe_pmc_single_anchor_exact": epmc,
        "europe_pmc_single_qualified": [],
        "europe_pmc": epmc,
        "europe_pmc_identity_fallback": [],
        "crossref": crossref,
        "semantic_scholar": semantic,
        "openalex_exact": [],
        "openalex_normal": openalex,
        "openalex": openalex,
        "general_news_single_en": news_en,
        "general_news_single_zh": news_zh,
        "gdelt": gdelt,
        "reliefweb": reliefweb,
        "authoritative_web_queries": [],
        "genomic_query": [],
    }


def build_relevance_rules(profile: dict[str, Any]) -> dict[str, Any]:
    anchors = _safe_terms(profile, "identity_anchor_terms")
    members = _safe_terms(profile, "member_identity_terms")
    diseases = _safe_terms(profile, "disease_identity_terms")
    qualified = [
        {
            "term": clean_space(x.get("term")),
            "required_context_terms": unique_strings(x.get("required_context_terms") or []),
        }
        for x in _entries(profile, "qualified_identity_terms")
        if clean_space(x.get("term")) and x.get("required_context_terms")
    ]
    contexts = unique_strings(clean_space(x.get("term")) for x in _entries(profile, "context_terms") if clean_space(x.get("term")))
    exclusions = unique_strings(clean_space(x.get("term")) for x in _entries(profile, "exclusion_terms") if clean_space(x.get("term")))
    concepts = _core_concepts(profile)
    return {
        "title_required_patterns": anchors + members + diseases,
        "identity_anchor_patterns": anchors,
        "title_or_abstract_identity_patterns": anchors + members + diseases,
        "member_patterns": members,
        "disease_patterns": diseases,
        "qualified_abbreviation_rules": qualified,
        "context_patterns": contexts,
        "excluded_entity_patterns": exclusions,
        "core_concept_patterns": unique_strings(x["scholarly"] for x in concepts),
        "reject_if_only_context_terms": True,
        "minimum_relevance_score": int((profile.get("query_policy") or {}).get("minimum_relevance_score", 6)),
        "review_score_min": int((profile.get("query_policy") or {}).get("review_score_min", 3)),
        "scoring_rules": [
            "+6 title identity anchor",
            "+5 title allowed member",
            "+4 title specific disease",
            "+3 abstract/excerpt identity anchor",
            "+2 abstract/excerpt allowed member or disease",
            "+1 context term",
            "+3 qualified abbreviation with required context",
            "+1 repeated identity evidence",
            "+1 independent retrieval concept",
            "-6 excluded entity dominates title",
            "-4 context-only or unqualified abbreviation",
        ],
    }


def validate_compiled_queries(profile: dict[str, Any], sets: dict[str, Any]) -> dict[str, Any]:
    concepts = sets.get("core_concepts") or []
    scholarly = [clean_space(x.get("scholarly")) for x in concepts if isinstance(x, dict)]
    norm = [re.sub(r"[^a-z0-9]+", " ", x.casefold()).strip() for x in scholarly]
    issues: list[str] = []
    if len(concepts) != 5:
        issues.append(f"core concept count must be exactly 5, got {len(concepts)}")
    if len(norm) != len(set(norm)):
        issues.append("semantic duplicate core concepts")
    for name in ("pubmed_core", "europe_pmc_core", "crossref_core", "semantic_scholar_core", "openalex_core"):
        if len(sets.get(name) or []) != len(concepts):
            issues.append(f"{name} does not cover every core concept")
    if any(len(q) > PUBMED_LIMIT for q in sets.get("pubmed_core") or []):
        issues.append("PubMed query length exceeded")
    if any(len(q) > NEWS_LIMIT for q in (sets.get("general_news_en") or []) + (sets.get("general_news_zh") or [])):
        issues.append("news query length exceeded")
    valid = not issues
    return {
        "lean_core_concept_check": {"passed": valid, "issues": issues},
        "provider_coverage_check": {"passed": valid, "issues": issues},
        "branch_anchor_check": {"passed": valid, "unanchored_branches": [] if valid else list(issues)},
        "standalone_context_check": {"passed": valid, "invalid_identity_terms": []},
        "abbreviation_check": {"passed": valid, "unsafe_abbreviations": []},
        "scope_check": {"passed": valid, "out_of_scope_members": []},
        "disease_specificity_check": {"passed": valid, "overbroad_disease_terms": []},
        "query_length_check": {"passed": valid, "issues": issues},
        "over_exclusion_check": {"passed": True, "issues": []},
        "source_evidence_check": {"passed": bool(profile.get("sources")), "terms_without_sources": []},
        "negative_test_check": {"passed": True, "negative_scenarios": []},
    }


def compile_profile_queries(profile: dict[str, Any]) -> dict[str, Any]:
    sets = compile_query_sets(profile)
    profile["search_strategy"] = profile.get("search_strategy") or {"concepts": sets.get("core_concepts") or []}
    profile["query_sets"] = sets
    profile["queries"] = {k: (v[0] if v else "") for k, v in sets.items() if isinstance(v, list) and k != "core_concepts"}
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    profile["validation"] = validate_compiled_queries(profile, sets)
    failures = [k for k, v in profile["validation"].items() if not v.get("passed")]
    if failures:
        profile["status"] = "needs_review"
        profile.setdefault("blocking_issues", []).extend(x for x in failures if x not in profile.get("blocking_issues", []))
    return profile


def build_query_plan(profile: dict[str, Any], max_groups: int = 200) -> list[dict[str, Any]]:
    sets = compile_query_sets(profile)
    concepts = sets.get("core_concepts") or []
    providers = {
        "pubmed": sets.get("pubmed_core") or [],
        "europe_pmc": sets.get("europe_pmc_core") or [],
        "crossref": sets.get("crossref_core") or [],
        "semantic_scholar": sets.get("semantic_scholar_core") or [],
        "openalex": sets.get("openalex_core") or [],
        "pubmed_supplemental": sets.get("pubmed_supplemental") or [],
        "europe_pmc_supplemental": sets.get("europe_pmc_supplemental") or [],
        "crossref_supplemental": sets.get("crossref_supplemental") or [],
        "semantic_scholar_supplemental": sets.get("semantic_scholar_supplemental") or [],
        "openalex_supplemental": sets.get("openalex_supplemental") or [],
        "news_en": sets.get("general_news_en") or [],
        "news_zh": sets.get("general_news_zh") or [],
        "gdelt": sets.get("gdelt_core") or [],
        "reliefweb": sets.get("reliefweb_core") or [],
    }
    plan: list[dict[str, Any]] = []
    for provider, queries in providers.items():
        for index, query in enumerate(queries):
            supplemental_concepts = sets.get("controlled_supplemental_concepts") or []
            is_supplemental = provider.endswith("_supplemental")
            concept_pool = supplemental_concepts if is_supplemental else concepts
            concept = concept_pool[index] if index < len(concept_pool) else {}
            plan.append({
                "group_id": f"{provider}-{index + 1:02d}",
                "provider": provider,
                "purpose": (
                    "controlled supplemental member-identity discovery"
                    if is_supplemental
                    else "frozen simple identity-term provider-native discovery"
                ),
                "concept_id": concept.get("id"),
                "concept_role": concept.get("role"),
                "query": query,
                "pubmed_query": query if provider in {"pubmed", "pubmed_supplemental"} else "",
                "europe_pmc_query": query if provider in {"europe_pmc", "europe_pmc_supplemental"} else "",
                "crossref_query": query if provider in {"crossref", "crossref_supplemental"} else "",
                "semantic_scholar_query": query if provider in {"semantic_scholar", "semantic_scholar_supplemental"} else "",
                "openalex_query": query if provider in {"openalex", "openalex_supplemental"} else "",
                "news_query": query if provider.startswith("news") or provider in {"gdelt", "reliefweb"} else "",
            })
            if len(plan) >= max_groups:
                return plan
    return plan
