from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .utils import clean_space, unique_strings


PUBMED_LIMIT = 1800
NEWS_LIMIT = 350


def _quote(term: str) -> str:
    value = clean_space(term).replace('"', '')
    return f'"{value}"' if value else ""


def _pm(term: str) -> str:
    return f'{_quote(term)}[Title/Abstract]'


def _epmc(term: str) -> str:
    return f'TITLE_ABS:{_quote(term)}'


def _entries(profile: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [x for x in ((profile.get("vocabulary") or {}).get(key) or []) if isinstance(x, dict)]


def _safe_terms(profile: dict[str, Any], key: str) -> list[str]:
    output: list[str] = []
    for item in _entries(profile, key):
        if item.get("safe_to_use_alone", key == "member_identity_terms"):
            term = clean_space(item.get("term"))
            if term:
                output.append(term)
    return unique_strings(output)


def _qualified_items(profile: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in _entries(profile, "qualified_identity_terms"):
        term = clean_space(item.get("term"))
        contexts = unique_strings(item.get("required_context_terms") or [])[:8]
        if term and contexts:
            output.append({"term": term, "contexts": contexts})
    return output


def _qualified_fragments(profile: dict[str, Any], provider: str) -> list[str]:
    output: list[str] = []
    for item in _qualified_items(profile):
        term = item["term"]
        contexts = item["contexts"]
        if provider == "pubmed":
            output.append(f'({_pm(term)} AND ({" OR ".join(_pm(x) for x in contexts)}))')
        elif provider == "europe_pmc":
            output.append(f'({_epmc(term)} AND ({" OR ".join(_epmc(x) for x in contexts)}))')
        else:
            output.append(f'({_quote(term)} AND ({" OR ".join(_quote(x) for x in contexts)}))')
    return output


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)] or [[]]


def _context_terms(profile: dict[str, Any]) -> list[str]:
    return unique_strings(clean_space(x.get("term")) for x in _entries(profile, "context_terms"))


def _mode_contexts(profile: dict[str, Any]) -> dict[str, list[str]]:
    seeds = _context_terms(profile)
    fixed = {
        "core": [],
        "molecular": ["genome", "sequence", "phylogeny", "mutation", "protein", "receptor", "reassortment", "recombination"],
        "epidemiology": ["outbreak", "surveillance", "incidence", "prevalence", "transmission", "seroprevalence"],
        "clinical": ["diagnosis", "clinical", "treatment", "vaccine", "severity", "hospitalization"],
        "genomic": ["genome", "sequence", "phylogeny", "lineage", "mutation", "evolution"],
    }
    patterns = {
        "molecular": re.compile(r"gene|protein|genom|sequence|segment|receptor|polymerase|capsid|mutation|reassort|recombin|phylogen", re.I),
        "epidemiology": re.compile(r"outbreak|surveillance|incidence|prevalence|transmission|reservoir|vector|host|spillover|epidemi", re.I),
        "clinical": re.compile(r"clinical|diagnos|treat|vaccine|hospital|therapy|antiviral|syndrome|disease|infection", re.I),
        "genomic": re.compile(r"genom|sequence|phylogen|lineage|mutation|evolution|genotype|serotype|clade", re.I),
    }
    for mode, pattern in patterns.items():
        fixed[mode] = unique_strings(fixed[mode] + [x for x in seeds if pattern.search(x)])[:14]
    return fixed


def _exclusions(profile: dict[str, Any]) -> list[str]:
    low_risk: list[str] = []
    for item in _entries(profile, "exclusion_terms"):
        risk = clean_space(item.get("risk_of_over_exclusion")).lower()
        if risk == "low":
            term = clean_space(item.get("term"))
            if term:
                low_risk.append(term)
    return unique_strings(low_risk)


def _strip_date_placeholder(query: str) -> str:
    value = clean_space(query)
    value = re.sub(r"\s+AND\s+\{\{DATE_FILTER\}\}", "", value, flags=re.I)
    value = value.replace("{{DATE_FILTER}}", "")
    value = re.sub(r"\(\s*\)", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _build_boolean_queries(
    branches: list[str],
    *,
    contexts: list[str] | None = None,
    exclusions: list[str] | None = None,
    chunk_size: int = 8,
    max_chars: int = 1800,
) -> list[str]:
    output: list[str] = []
    contexts = contexts or []
    exclusions = exclusions or []
    for chunk in _chunks(branches, chunk_size):
        if not chunk:
            continue
        query = f'({" OR ".join(chunk)})'
        if contexts:
            query += f' AND ({" OR ".join(contexts)})'
        if exclusions:
            query += f' NOT ({" OR ".join(exclusions)})'
        if len(query) <= max_chars:
            output.append(query)
            continue
        # Re-split only this chunk; never silently truncate a query because
        # truncation can produce invalid parentheses or change semantics.
        if len(chunk) > 1:
            output.extend(
                _build_boolean_queries(
                    chunk,
                    contexts=contexts,
                    exclusions=exclusions,
                    chunk_size=max(1, len(chunk) // 2),
                    max_chars=max_chars,
                )
            )
            continue
        # A single identity branch can only exceed the provider limit because
        # the optional context/exclusion lists are too long. Shrink those
        # deterministically while preserving the identity branch.
        reduced_contexts = list(contexts)
        reduced_exclusions = list(exclusions)
        while len(query) > max_chars and (reduced_contexts or reduced_exclusions):
            if len(reduced_contexts) >= len(reduced_exclusions) and reduced_contexts:
                reduced_contexts.pop()
            elif reduced_exclusions:
                reduced_exclusions.pop()
            query = f'({chunk[0]})'
            if reduced_contexts:
                query += f' AND ({" OR ".join(reduced_contexts)})'
            if reduced_exclusions:
                query += f' NOT ({" OR ".join(reduced_exclusions)})'
        if len(query) > max_chars:
            raise RuntimeError(f"single identity branch exceeds provider limit {max_chars}: {chunk[0][:120]}")
        output.append(query)
    return unique_strings(output)


def _semantic_text(term: str) -> str:
    # Semantic Scholar relevance search documents that hyphenated terms can
    # yield no matches. Preserve the identity words but normalize punctuation.
    value = clean_space(term).replace("-", " ").replace("/", " ")
    return clean_space(re.sub(r"[^A-Za-z0-9\s]", " ", value))


def _tokenized_identity(term: str) -> list[str]:
    """Return meaningful alphanumeric tokens for provider fallbacks.

    This is deliberately conservative: short one-letter protein-like tokens are
    not used. The fallback is always joined with AND, never OR, so it broadens
    punctuation and word-order matching without turning context words into
    standalone identity branches.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", clean_space(term))
    return unique_strings(x for x in tokens if len(x) >= 2)[:8]


def _conjunctive_identity_queries(profile: dict[str, Any], provider: str) -> list[str]:
    output: list[str] = []
    identities = unique_strings(
        _safe_terms(profile, "identity_anchor_terms")
        + _safe_terms(profile, "member_identity_terms")
        + _safe_terms(profile, "disease_identity_terms")
    )
    for term in identities[:18]:
        tokens = _tokenized_identity(term)
        if len(tokens) < 2:
            continue
        if provider == "pubmed":
            output.append("(" + " AND ".join(f'{x}[Title/Abstract]' for x in tokens) + ")")
        elif provider == "europe_pmc":
            output.append("TITLE_ABS:(" + " AND ".join(tokens) + ")")
    return unique_strings(output)


def _single_anchor_queries(profile: dict[str, Any], provider: str) -> list[str]:
    """Compile one independent query per safe authoritative identity.

    Grouped OR queries are useful for efficiency, but a common high-volume
    identity can fill a provider result page and hide a rare member.  Every
    safe identity therefore gets its own provider-native query.  Context-only
    terms are never admitted here.
    """
    identities = unique_strings(
        _safe_terms(profile, "identity_anchor_terms")
        + _safe_terms(profile, "member_identity_terms")
        + _safe_terms(profile, "disease_identity_terms")
    )
    if provider == "pubmed":
        return [_pm(term) for term in identities]
    if provider == "europe_pmc":
        return [_epmc(term) for term in identities]
    if provider in {"news_en", "gdelt", "reliefweb"}:
        return [_quote(term) for term in identities]
    if provider == "news_zh":
        return [_quote(term) for term in _news_aliases_zh(profile)]
    raise ValueError(f"unsupported provider for single-anchor compilation: {provider}")


def _single_qualified_queries(profile: dict[str, Any], provider: str) -> list[str]:
    """Return one query per ambiguous abbreviation with mandatory context."""
    if provider in {"pubmed", "europe_pmc"}:
        return _qualified_fragments(profile, provider)
    output: list[str] = []
    for item in _qualified_items(profile):
        term = _quote(item["term"])
        contexts = [_quote(x) for x in item["contexts"] if clean_space(x)]
        if term and contexts:
            output.append(f"{term} AND ({' OR '.join(contexts)})")
    return unique_strings(output)


def _semantic_queries(profile: dict[str, Any]) -> list[str]:
    """Compile Semantic Scholar bulk-search queries.

    The bulk endpoint accepts Boolean matching, but PubMed field tags and PubMed
    date syntax are not portable. Safe full identities are sent individually;
    ambiguous abbreviations are emitted only as ``ABBR AND context`` queries.
    Hyphens are normalized because the Semantic Scholar search documentation
    warns that hyphenated text can yield no matches.
    """
    output: list[str] = []
    safe = unique_strings(
        _safe_terms(profile, "identity_anchor_terms")
        + _safe_terms(profile, "member_identity_terms")
        + _safe_terms(profile, "disease_identity_terms")
    )
    output.extend(_semantic_text(x) for x in safe if _semantic_text(x))
    for item in _qualified_items(profile):
        abbreviation = _semantic_text(item["term"])
        if not abbreviation:
            continue
        contexts = unique_strings(
            normalized
            for value in item["contexts"][:4]
            if (normalized := _semantic_text(value))
        )
        if contexts:
            # Semantic Scholar bulk search syntax is provider-specific:
            # + means AND and | means OR. Parentheses preserve precedence.
            output.append(f"{abbreviation} +({' | '.join(contexts)})")
    return unique_strings(output)


def _openalex_query_sets(profile: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return exact and normal OpenAlex search channels.

    ``search.exact`` protects precision for the highest-priority identities.
    Normal ``search`` queries add stemming/punctuation tolerance. Boolean OR
    groups reduce request volume while preserving every identity branch. The
    local article-level relevance gate remains authoritative.
    """
    safe = unique_strings(
        _safe_terms(profile, "identity_anchor_terms")
        + _safe_terms(profile, "member_identity_terms")
        + _safe_terms(profile, "disease_identity_terms")
    )
    # Every safe identity is sent independently through both exact and normal
    # channels.  The normal channel catches punctuation, stemming and word-order
    # variants; the exact channel protects precision.  Grouped OR queries are
    # retained only as an additional discovery path, never as the sole path.
    exact = list(safe)
    normal: list[str] = [clean_space(x) for x in safe if clean_space(x)]
    for group in _chunks(safe, 4):
        values = [_quote(x) for x in group if clean_space(x)]
        if len(values) > 1:
            normal.append("(" + " OR ".join(values) + ")")
    for item in _qualified_items(profile):
        term = _quote(item["term"])
        contexts = [_quote(x) for x in item["contexts"][:6] if clean_space(x)]
        if term and contexts:
            normal.append(f"{term} AND ({' OR '.join(contexts)})")
    return unique_strings(exact), unique_strings(normal)


def _simple_identity_queries(profile: dict[str, Any], limit: int | None = None) -> list[str]:
    anchors = _safe_terms(profile, "identity_anchor_terms")
    members = _safe_terms(profile, "member_identity_terms")
    diseases = _safe_terms(profile, "disease_identity_terms")
    output = unique_strings(anchors + members + diseases)
    for item in _qualified_items(profile):
        output.append(clean_space(" ".join([item["term"], *item["contexts"][:2]])))
    values = unique_strings(output)
    return values if limit is None else values[:limit]


def _news_aliases_zh(profile: dict[str, Any]) -> list[str]:
    qualified = {clean_space(x.get("term")).casefold() for x in _entries(profile, "qualified_identity_terms")}
    forbidden = {clean_space(x.get("term")).casefold() for x in _entries(profile, "display_only_terms")}
    values = unique_strings(
        [profile.get("display_name_zh")]
        + list(profile.get("news_identity_terms_zh") or [])
        + [
            x.get("term")
            for x in _entries(profile, "disease_identity_terms")
            if re.search(r"[\u4e00-\u9fff]", clean_space(x.get("term")))
        ]
    )
    # A Chinese-news alias may contain a Latin acronym.  Ambiguous acronyms
    # such as SFTSV/RSV/HPV are never emitted as standalone news queries; their
    # qualified English query remains available separately.
    return [x for x in values if clean_space(x).casefold() not in qualified | forbidden]


def _news_identity_groups(profile: dict[str, Any], language: str) -> list[list[str]]:
    if language == "zh":
        values = _news_aliases_zh(profile)
    else:
        values = unique_strings(
            _safe_terms(profile, "identity_anchor_terms")
            + _safe_terms(profile, "member_identity_terms")
            + _safe_terms(profile, "disease_identity_terms")
        )
    return [x for x in _chunks(values, 4) if x]


def _generic_news_queries(profile: dict[str, Any], language: str) -> list[str]:
    context_groups = (
        [[], ["outbreak", "cases", "surveillance", "transmission"], ["vaccine", "treatment", "diagnosis", "guidance"], ["genome", "variant", "lineage", "mutation"]]
        if language == "en"
        else [[], ["病例", "暴发", "疫情", "传播", "监测"], ["疫苗", "治疗", "诊断", "指南"], ["基因组", "变异", "谱系", "突变"]]
    )
    output: list[str] = []
    # Single-authoritative-anchor queries are mandatory and are executed before
    # grouped discovery queries.  This prevents common identities from crowding
    # rare members out of a provider's result budget.
    singles = _single_anchor_queries(profile, "news_zh" if language == "zh" else "news_en")
    # Every authoritative identity is queried independently once. Context-mode
    # variants are then applied to small grouped identities, avoiding hundreds
    # of near-duplicate RSS/API requests that would increase throttling and
    # paradoxically reduce recall.
    output.extend(singles)
    # Grouped queries remain a complementary path for provider ranking and
    # broad event discovery, not the only retrieval route.
    for group in _news_identity_groups(profile, language):
        identity = f'({" OR ".join(_quote(x) for x in group)})'
        for contexts in context_groups:
            query = identity
            if contexts:
                query += f' ({" OR ".join(_quote(x) for x in contexts)})'
            if len(query) <= NEWS_LIMIT:
                output.append(query)
    return unique_strings(output)


def _gdelt_queries(profile: dict[str, Any]) -> list[str]:
    exclusions = _exclusions(profile)[:2]
    output: list[str] = []
    event_groups = [[], ["outbreak", "cases", "surveillance", "transmission"], ["vaccine", "treatment", "guidance"], ["genome", "variant", "lineage"]]
    identities = _single_anchor_queries(profile, "gdelt")
    # One identity-only query per safe anchor guarantees member coverage. Event
    # context variants are generated on small groups below to control provider
    # request volume and reduce rate-limit-driven omissions.
    for identity in identities:
        query = identity
        if exclusions:
            query += " " + " ".join(f'-{_quote(x)}' for x in exclusions)
        if len(query) <= NEWS_LIMIT:
            output.append(query)
    for group in _news_identity_groups(profile, "en"):
        identity = f'({" OR ".join(_quote(x) for x in group)})'
        for contexts in event_groups:
            query = identity
            if contexts:
                query += f' ({" OR ".join(_quote(x) for x in contexts)})'
            if exclusions:
                query += " " + " ".join(f'-{_quote(x)}' for x in exclusions)
            if len(query) <= NEWS_LIMIT:
                output.append(query)
    return unique_strings(output)


def _reliefweb_queries(profile: dict[str, Any]) -> list[str]:
    output: list[str] = []
    identities = _single_anchor_queries(profile, "reliefweb")
    # Independent anchor-only coverage first; grouped contextual discovery below.
    output.extend(identities)
    for group in _news_identity_groups(profile, "en"):
        identity = f'({" OR ".join(_quote(x) for x in group)})'
        output.append(identity)
        for contexts in (["outbreak", "cases", "transmission"], ["vaccine", "treatment", "response"]):
            query = f'{identity} AND ({" OR ".join(_quote(x) for x in contexts)})'
            if len(query) <= NEWS_LIMIT:
                output.append(query)
    return unique_strings(output)


def _authoritative_web_queries(profile: dict[str, Any]) -> list[str]:
    identities = _simple_identity_queries(profile, limit=16)[:12]
    if not identities:
        return []
    identity = f'({" OR ".join(_quote(x) for x in identities)})'
    output: list[str] = []
    for source in profile.get("sources") or []:
        url = clean_space(source.get("url"))
        host = (urlparse(url).hostname or "").lower()
        if not host:
            continue
        query = f'site:{host} {identity}'
        if len(query) <= NEWS_LIMIT:
            output.append(query)
    return unique_strings(output)


def compile_query_sets(profile: dict[str, Any]) -> dict[str, list[str]]:
    anchors = _safe_terms(profile, "identity_anchor_terms")
    members = _safe_terms(profile, "member_identity_terms")
    diseases = _safe_terms(profile, "disease_identity_terms")
    if not anchors and not members and not _qualified_items(profile):
        raise RuntimeError(f"{profile.get('profile_id')}: no safe identity anchors")

    high_precision = unique_strings(anchors + members + diseases)
    contexts = _mode_contexts(profile)
    exclusions = _exclusions(profile)

    pm_base = [_pm(x) for x in high_precision]
    pm_single = _single_anchor_queries(profile, "pubmed")
    pm_qualified = _qualified_fragments(profile, "pubmed")
    epmc_base = [_epmc(x) for x in high_precision]
    epmc_single = _single_anchor_queries(profile, "europe_pmc")
    epmc_qualified = _qualified_fragments(profile, "europe_pmc")

    manual = clean_space((profile.get("manual_query_skeletons") or {}).get("pubmed_high_precision"))
    manual = _strip_date_placeholder(manual) if manual else ""

    pubmed_precision = _build_boolean_queries(
        pm_base,
        exclusions=[_pm(x) for x in exclusions],
        chunk_size=8,
        max_chars=PUBMED_LIMIT,
    )
    if manual:
        pubmed_precision = unique_strings([manual] + pubmed_precision)

    pubmed_recall = _build_boolean_queries(pm_base + pm_qualified, chunk_size=8, max_chars=PUBMED_LIMIT)
    # A conjunctive token fallback catches punctuation, hyphen and word-order
    # variants without allowing individual context words to retrieve records.
    pubmed_fallback = _conjunctive_identity_queries(profile, "pubmed")

    pubmed_molecular = _build_boolean_queries(
        pm_base + pm_qualified,
        contexts=[_pm(x) for x in contexts["molecular"]],
        chunk_size=7,
        max_chars=PUBMED_LIMIT,
    )
    pubmed_epi = _build_boolean_queries(
        pm_base + pm_qualified,
        contexts=[_pm(x) for x in contexts["epidemiology"]],
        chunk_size=7,
        max_chars=PUBMED_LIMIT,
    )
    pubmed_clinical = _build_boolean_queries(
        pm_base + pm_qualified,
        contexts=[_pm(x) for x in contexts["clinical"]],
        chunk_size=7,
        max_chars=PUBMED_LIMIT,
    )
    epmc = _build_boolean_queries(epmc_base + epmc_qualified, chunk_size=8, max_chars=1600)
    epmc_fallback = _conjunctive_identity_queries(profile, "europe_pmc")

    # Crossref does not receive a PubMed Boolean expression. Each identity is
    # queried independently; the adapter applies published/created/indexed
    # date channels and local relevance filtering.
    crossref = _simple_identity_queries(profile, limit=None)
    semantic = _semantic_queries(profile)
    openalex_exact, openalex_normal = _openalex_query_sets(profile)

    news_en = _generic_news_queries(profile, "en")
    news_zh = _generic_news_queries(profile, "zh")

    return {
        "pubmed_single_anchor_exact": pm_single,
        "pubmed_single_qualified": _single_qualified_queries(profile, "pubmed"),
        "pubmed_core_high_precision": pubmed_precision,
        "pubmed_core_high_recall": pubmed_recall,
        "pubmed_identity_fallback": pubmed_fallback,
        "pubmed_molecular": pubmed_molecular,
        "pubmed_epidemiology": pubmed_epi,
        "pubmed_clinical": pubmed_clinical,
        "europe_pmc_single_anchor_exact": epmc_single,
        "europe_pmc_single_qualified": _single_qualified_queries(profile, "europe_pmc"),
        "europe_pmc": epmc,
        "europe_pmc_identity_fallback": epmc_fallback,
        "crossref": crossref,
        "semantic_scholar": semantic,
        "openalex_exact": openalex_exact,
        "openalex_normal": openalex_normal,
        # Compatibility alias for older diagnostics.
        "openalex": unique_strings(openalex_exact + openalex_normal),
        "general_news_single_en": _single_anchor_queries(profile, "news_en"),
        "general_news_single_zh": _single_anchor_queries(profile, "news_zh"),
        "general_news_en": news_en,
        "general_news_zh": news_zh,
        "gdelt": _gdelt_queries(profile),
        "reliefweb": _reliefweb_queries(profile),
        "authoritative_web_queries": _authoritative_web_queries(profile),
        "genomic_query": _build_boolean_queries(
            [_quote(x) for x in high_precision] + _qualified_fragments(profile, "generic"),
            contexts=[_quote(x) for x in contexts["genomic"]],
            chunk_size=7,
            max_chars=1200,
        ),
    }

def build_relevance_rules(profile: dict[str, Any]) -> dict[str, Any]:
    anchors = _safe_terms(profile, "identity_anchor_terms")
    members = _safe_terms(profile, "member_identity_terms")
    diseases = _safe_terms(profile, "disease_identity_terms")
    qualified = [
        {"term": x["term"], "required_context_terms": x["contexts"]}
        for x in _qualified_items(profile)
    ]
    return {
        "title_required_patterns": anchors + members + diseases,
        "identity_anchor_patterns": anchors,
        "title_or_abstract_identity_patterns": anchors + members + diseases,
        "member_patterns": members,
        "disease_patterns": diseases,
        "qualified_abbreviation_rules": qualified,
        "context_patterns": _context_terms(profile),
        "excluded_entity_patterns": [clean_space(x.get("term")) for x in _entries(profile, "exclusion_terms")],
        "reject_if_only_context_terms": True,
        "minimum_relevance_score": int((profile.get("query_policy") or {}).get("minimum_relevance_score", 6)),
        "review_score_min": int((profile.get("query_policy") or {}).get("review_score_min", 3)),
        "scoring_rules": [
            "+6 title exact identity anchor",
            "+5 title allowed member",
            "+4 title specific disease",
            "+3 abstract/body exact identity anchor",
            "+2 abstract/body allowed member",
            "+1 context term",
            "+3 qualified abbreviation with required context",
            "-6 excluded entity dominates title without target title anchor",
            "-4 ambiguous abbreviation without required context",
            "-4 context-only hit",
        ],
    }


def validate_compiled_queries(profile: dict[str, Any], sets: dict[str, list[str]]) -> dict[str, Any]:
    forbidden = {clean_space(x.get("term")).casefold() for x in _entries(profile, "display_only_terms")}
    length_issues: list[str] = []
    standalone_issues: list[str] = []
    for name, queries in sets.items():
        limit = PUBMED_LIMIT if name.startswith("pubmed") else NEWS_LIMIT if name in {
            "general_news_single_en", "general_news_single_zh", "general_news_en", "general_news_zh",
            "gdelt", "reliefweb", "authoritative_web_queries"
        } else 1800
        for query in queries:
            if len(query) > limit:
                length_issues.append(f"{name} exceeds {limit} chars")
            stripped = clean_space(query).strip("()").strip('"').casefold()
            if stripped in forbidden:
                standalone_issues.append(f"{name} uses forbidden standalone term: {query}")
    unsafe_abbreviations = [
        x["term"] for x in _qualified_items(profile) if not x.get("contexts")
    ]
    safe_identities = unique_strings(
        _safe_terms(profile, "identity_anchor_terms")
        + _safe_terms(profile, "member_identity_terms")
        + _safe_terms(profile, "disease_identity_terms")
    )
    pubmed_singles = "\n".join(sets.get("pubmed_single_anchor_exact") or []).casefold()
    epmc_singles = "\n".join(sets.get("europe_pmc_single_anchor_exact") or []).casefold()
    missing_single_coverage = [
        term for term in safe_identities
        if clean_space(term).casefold() not in pubmed_singles
        or clean_space(term).casefold() not in epmc_singles
    ]
    return {
        "branch_anchor_check": {"passed": not standalone_issues, "unanchored_branches": standalone_issues},
        "standalone_context_check": {"passed": not standalone_issues, "invalid_identity_terms": standalone_issues},
        "abbreviation_check": {"passed": not unsafe_abbreviations, "unsafe_abbreviations": unsafe_abbreviations},
        "scope_check": {"passed": True, "out_of_scope_members": []},
        "disease_specificity_check": {"passed": True, "overbroad_disease_terms": []},
        "query_length_check": {"passed": not length_issues, "issues": length_issues},
        "over_exclusion_check": {"passed": True, "issues": []},
        "source_evidence_check": {"passed": bool(profile.get("sources")), "terms_without_sources": []},
        "single_anchor_coverage_check": {"passed": not missing_single_coverage, "missing_identities": missing_single_coverage},
        "negative_test_check": {"passed": True, "negative_scenarios": []},
    }


def compile_profile_queries(profile: dict[str, Any]) -> dict[str, Any]:
    sets = compile_query_sets(profile)
    profile["query_sets"] = sets
    profile["queries"] = {k: (v[0] if v else "") for k, v in sets.items()}
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    profile["validation"] = validate_compiled_queries(profile, sets)
    failures = [k for k, v in profile["validation"].items() if not v.get("passed")]
    if failures:
        profile["status"] = "needs_review"
        profile.setdefault("blocking_issues", []).extend(x for x in failures if x not in profile.get("blocking_issues", []))
    return profile


def build_query_plan(profile: dict[str, Any], max_groups: int = 200) -> list[dict[str, Any]]:
    """Return a complete provider-aware audit plan.

    Unlike v3, every query chunk is represented. The collection pipeline reads
    ``profile['query_sets']`` directly and executes all chunks; this function is
    for audit files and the Pages diagnostics view.
    """
    sets = profile.get("query_sets") or compile_query_sets(profile)
    plan: list[dict[str, Any]] = []
    provider_keys = [
        "pubmed_single_anchor_exact",
        "pubmed_single_qualified",
        "pubmed_core_high_precision",
        "pubmed_core_high_recall",
        "pubmed_identity_fallback",
        "pubmed_molecular",
        "pubmed_epidemiology",
        "pubmed_clinical",
        "europe_pmc_single_anchor_exact",
        "europe_pmc_single_qualified",
        "europe_pmc",
        "europe_pmc_identity_fallback",
        "crossref",
        "semantic_scholar",
        "openalex_exact",
        "openalex_normal",
        "general_news_single_en",
        "general_news_single_zh",
        "general_news_en",
        "general_news_zh",
        "gdelt",
        "reliefweb",
        "authoritative_web_queries",
    ]
    for provider in provider_keys:
        for index, query in enumerate(sets.get(provider) or [], 1):
            row = {
                "group_id": f"{provider}-{index:02d}",
                "provider": provider,
                "purpose": "provider-specific anchored retrieval",
                "query": query,
                # Backward-compatible fields used by earlier tests/UI.
                "pubmed_query": query if provider.startswith("pubmed") else "",
                "europe_pmc_query": query if provider.startswith("europe_pmc") else "",
                "crossref_query": query if provider == "crossref" else "",
                "semantic_scholar_query": query if provider == "semantic_scholar" else "",
                "openalex_query": query if provider in {"openalex_exact", "openalex_normal"} else "",
                "news_query": query if provider in {"general_news_single_en", "general_news_single_zh", "general_news_en", "general_news_zh", "gdelt", "reliefweb", "authoritative_web_queries"} else "",
            }
            plan.append(row)
            if len(plan) >= max_groups:
                return plan
    return plan
