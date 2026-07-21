from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import os
from typing import Any

from .analysis import ANALYSIS_POLICY_VERSION, analyze_news, analyze_paper, build_paper_evidence
from .language_contract import annotate_source_language, sanitize_english_analysis
from .analysis_quality import summarize_analysis_quality
from .bootstrap import _fallback_profile, build_profile
from .config import Settings, load_profile, load_seed
from .content import apply_news_content_circuit_breaker, complete_scholarly_work, resolve_and_extract_news
from .dates import assess_publication_date, date_window, publication_search_end
from .dedup import attach_news_to_papers, dedup_news, dedup_papers
from .http import HttpClient
from .llm import LLMRouter
from .news import filter_news_window, search_bing_news, search_gdelt, search_google_news, search_reliefweb, search_who
from .news_state import finalize_news_state, mark_source_qualified
from .query_plan import build_query_plan, build_relevance_rules, compile_query_sets
from .event_query import (
    append_event_queries_to_plan,
    augment_news_query_sets,
    derive_event_queries,
    is_scarce_profile,
    news_relevance_profile,
)
from .relevance import candidate_filter_news, candidate_filter_papers, filter_post_enrichment, final_filter
from .relevance_guard import apply_relevance_cliff_guard, baseline_value, update_baseline
from .source_status import SourceAudit
from .ranking import rank_news, rank_papers
from .render import render_site, render_wechat_package
from .scholarly import (
    filter_publication_window,
    search_crossref,
    search_europe_pmc,
    search_openalex,
    search_pubmed,
    search_semantic_scholar,
    search_biorxiv_medrxiv,
    probe_pubmed_anchor_counts,
    probe_europe_pmc_anchor_counts,
)
from .storage import load_state, save_state, write_issue
from .scholarly_gate import filter_scholarly_records
from .translation import TRANSLATION_CACHE_VERSION, translate_record, translate_title_only
from .overview import build_overviews
from .literature import (
    build_post_retrieval_vocabulary,
    complete_literature_catalog,
    metadata_verification,
    normalize_literature_record,
    select_primary_and_supplementary,
    validate_frozen_core_terms,
    verified_evidence_status,
)
from .progress import progress
from .cover import ensure_profile_cover
from .utils import append_jsonl, clean_space, dump_json, load_json, sha256_text, utc_now_iso, unique_strings
from .runtime_budget import RuntimeBudget
from .vocabulary_lifecycle import ensure_review_vocabulary


def _preprint_identity_terms(profile: dict[str, Any]) -> list[str]:
    vocabulary = profile.get("candidate_vocabulary") or {}
    scope = profile.get("target_scope") or {}
    terms: list[str] = []
    terms.extend(vocabulary.get("identity_anchor_terms") or [])
    terms.extend(scope.get("required_identity_concepts") or [])
    terms.extend(scope.get("allowed_members") or [])
    terms.extend([profile.get("display_name_en"), profile.get("profile_id")])
    # Qualified abbreviations are intentionally excluded unless their expanded
    # context appears elsewhere; short tokens such as SNV create false hits.
    return [term for term in unique_strings(terms) if len(clean_space(term)) >= 4]


def _demo_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    papers = [
        {
            "source": "Demo PubMed",
            "source_ids": {"pmid": "00000001"},
            "doi": "10.0000/demo.research",
            "title": "Serologic evidence of hantavirus exposure in forest workers",
            "abstract": "A cross-sectional study tested 371 forest workers for hantavirus antibodies. Hantavirus IgG was detected in 7.3% of participants. Exposure was associated with frequent rodent contact. The study was limited by its single-region design and lack of longitudinal follow-up.",
            "authors": ["Demo Author"],
            "journal": "Demo Virology",
            "year": 2026,
            "volume": "1",
            "issue": "1",
            "pages": "1-8",
            "online_date": date.today().isoformat(),
            "availability_date": date.today().isoformat(),
            "availability_date_basis": "online_date",
            "publication_types": ["Journal Article"],
            "url": "https://example.org/research",
        },
        {
            "source": "Demo Europe PMC",
            "source_ids": {"pmid": "00000002"},
            "doi": "10.0000/demo.review",
            "title": "Hantavirus infections in a changing world: a narrative review",
            "abstract": "This review discusses hantavirus epidemiology, reservoir ecology, pathogenesis, diagnosis, and prevention. Evidence indicates that environmental change and human contact with rodent reservoirs alter spillover risk. Gaps include limited prospective surveillance and few licensed countermeasures. Future work should integrate One Health surveillance and standardized clinical studies.",
            "authors": ["Review Author"],
            "journal": "Demo Reviews",
            "year": 2026,
            "online_date": date.today().isoformat(),
            "availability_date": date.today().isoformat(),
            "availability_date_basis": "online_date",
            "publication_types": ["Review"],
            "url": "https://example.org/review",
        },
    ]
    news = [
        {
            "source": "Demo News",
            "title": "Health authority reports a suspected hantavirus case",
            "url": "https://example.org/news",
            "published_date": date.today().isoformat(),
            "excerpt": "On Tuesday, the regional health authority reported one suspected hantavirus case in County A. The patient is stable and confirmatory testing is under way. Officials advised residents to avoid contact with rodent droppings. No additional cases have been reported.",
            "publisher": "Demo Public Health News",
            "language": "en",
        }
    ]
    return papers, news


def _parallel_map(items: list[dict[str, Any]], fn: Any, workers: int) -> list[dict[str, Any]]:
    if not items:
        return []
    output: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fn, item) for item in items]
        for future in as_completed(futures):
            try:
                output.append(future.result())
            except Exception:
                continue
    return output


def _review_summary(rows: list[dict[str, Any]], accepted_count: int) -> dict[str, Any]:
    methods = Counter(clean_space(x.get("relevance_review_method")) or "not_recorded" for x in rows)
    decisions = Counter(clean_space(x.get("relevance_decision")) or "not_recorded" for x in rows)
    cache = Counter(clean_space(x.get("relevance_review_cache")) or "not_used" for x in rows)
    return {
        "candidates_reviewed_by_python": len(rows),
        "accepted": accepted_count,
        "rejected": max(0, len(rows) - accepted_count),
        "review_methods": dict(sorted(methods.items())),
        "review_decisions": dict(sorted(decisions.items())),
        "cache": dict(sorted(cache.items())),
        "document_count_cutoff": None,
        "character_prefix_cutoff": None,
    }


def _anchor_coverage(profile: dict[str, Any], query_sets: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    concepts = [x for x in query_sets.get("core_concepts") or [] if isinstance(x, dict)]
    provider_queries = {
        "pubmed": query_sets.get("pubmed_all") or query_sets.get("pubmed_core") or [],
        "europe_pmc": query_sets.get("europe_pmc_all") or query_sets.get("europe_pmc_core") or [],
        "crossref": query_sets.get("crossref_all") or query_sets.get("crossref_core") or [],
        "semantic_scholar": query_sets.get("semantic_scholar_all") or query_sets.get("semantic_scholar_core") or [],
        "openalex": query_sets.get("openalex_all") or query_sets.get("openalex_core") or [],
        "news_en": query_sets.get("general_news_en") or [],
        "news_zh": query_sets.get("general_news_zh") or [],
    }
    rows: list[dict[str, Any]] = []
    for index, concept in enumerate(concepts):
        row: dict[str, Any] = {
            "concept_id": concept.get("id"),
            "scholarly": concept.get("scholarly"),
            "role": concept.get("role"),
            "providers": {},
        }
        for provider, queries in provider_queries.items():
            query = queries[index] if index < len(queries) else ""
            matching = [
                entry for entry in entries
                if query and clean_space(entry.get("query")) == clean_space(query)
            ]
            row["providers"][provider] = {
                "query": query,
                "executed": bool(matching),
                "records_reported": sum(int(x.get("records") or 0) for x in matching),
                "failed_queries": sum(x.get("status") == "failed" for x in matching),
            }
        rows.append(row)
    return {
        "profile_id": profile.get("profile_id"),
        "strategy": "lean_core_concepts_v7",
        "concept_count": len(rows),
        "concepts": rows,
    }


def _annotate_retrieval_concepts(records: list[dict[str, Any]], query_sets: dict[str, Any]) -> None:
    mapping = query_sets.get("query_concept_map") or {}
    for record in records:
        concepts: list[str] = []
        for query in record.get("retrieval_queries") or []:
            concepts.extend(mapping.get(clean_space(query)) or mapping.get(query) or [])
        record["retrieval_concepts"] = unique_strings(concepts)
        record["retrieval_concept_count"] = len(record["retrieval_concepts"])



def _load_or_build_runtime_profile(
    settings: Settings,
    http: HttpClient,
    llm: LLMRouter,
    *,
    demo: bool,
) -> dict[str, Any]:
    """Resolve a runtime profile without allowing weekly term drift.

    Only an explicit ``refresh_profile`` operator action may invoke the
    authoritative-source/LLM profile builder. Normal scheduled runs consume a
    matching persisted profile or deterministically rebuild the frozen
    ``seed.yaml`` contract when state is missing/stale.
    """
    profile = load_profile(settings)
    seed = load_seed(settings.project_root, settings.profile_id)
    seed_hash = sha256_text(json.dumps(seed, ensure_ascii=False, sort_keys=True))
    profile_stale = not profile or profile.get("seed_hash") != seed_hash
    progress(
        "profile",
        "decision",
        refresh=settings.refresh_profile,
        stale=profile_stale,
        cached=bool(profile),
    )
    if settings.refresh_profile:
        profile = build_profile(settings, http, llm)
    elif not profile or profile_stale or profile.get("generated_by") == "bundled_seed":
        profile = _fallback_profile(seed, [])
        profile["seed_hash"] = seed_hash
        profile["generated_by"] = (
            "deterministic_frozen_seed_demo" if demo else "deterministic_frozen_seed_refresh"
        )
        profile["profile_contract"] = "frozen-five-core-post-retrieval-vocabulary/v15.1"
        target = settings.state_dir.parent / "profiles" / settings.profile_id / "profile.json"
        dump_json(target, profile)
    return profile

def run_pipeline(settings: Settings, *, demo: bool = False) -> dict[str, Any]:
    progress("pipeline", "start", profile=settings.profile_id, demo=demo)
    runtime_budget = RuntimeBudget(
        profile_runtime_minutes=settings.profile_runtime_minutes,
        finalization_reserve_minutes=settings.finalization_reserve_minutes,
        stage_limits_minutes={
            "retrieval": settings.retrieval_max_minutes,
            "relevance": settings.relevance_max_minutes,
            "paper_processing": settings.paper_processing_max_minutes,
            "supplementary_review": settings.supplementary_review_max_minutes,
            "news_enrichment": settings.news_enrichment_max_minutes,
            "news_analysis": settings.news_analysis_max_minutes,
        },
    )
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    http = HttpClient(settings.user_agent)
    secrets = settings.secrets
    llm = LLMRouter(
        http,
        gemini_key=secrets.get("GEMINI_API_KEY", ""),
        groq_key=secrets.get("GROQ_API_KEY", ""),
        openrouter_key=secrets.get("OPENROUTER_API_KEY", ""),
        mistral_key=secrets.get("MISTRAL_API_KEY", ""),
        siliconflow_key=secrets.get("SILICONFLOW_API_KEY", ""),
        bigmodel_key=secrets.get("BIGMODEL_API_KEY", ""),
        deepseek_key=secrets.get("DEEPSEEK_API_KEY", ""),
    )
    for provider_name in llm.configured_providers():
        if provider_name in {"openrouter", "siliconflow", "deepseek"}:
            llm.provider_account_info(provider_name)
    llm_preflight = (
        load_json(settings.llm_preflight_file, {})
        if settings.llm_preflight_file and Path(settings.llm_preflight_file).exists()
        else {"status": "not_run", "reason": "PIF_LLM_PREFLIGHT_FILE not available"}
    )
    if settings.analysis_require_llm:
        preflight_status = clean_space((llm_preflight or {}).get("status"))
        if not llm.available or preflight_status in {"failed", "unavailable"}:
            raise RuntimeError(
                "PIF_ANALYSIS_REQUIRE_LLM=true but no configured LLM analysis provider passed preflight"
            )
    state = load_state(settings.state_dir)
    profile = _load_or_build_runtime_profile(settings, http, llm, demo=demo)
    progress("profile", "ready_check", status=(profile or {}).get("status"), generated_by=(profile or {}).get("generated_by"))
    if profile.get("status") != "ready":
        raise RuntimeError(
            f"profile {settings.profile_id} is not ready: "
            f"{profile.get('blocking_issues') or profile.get('status')}"
        )
    profile, review_vocabulary_audit = ensure_review_vocabulary(
        settings, profile, http, llm, demo=demo
    )
    if review_vocabulary_audit.get("cache_invalidation_required"):
        for cache_name in ("relevance_review_cache", "analysis_cache", "translation_cache"):
            state.pop(cache_name, None)
        state["profile_semantic_fingerprint"] = profile.get("profile_semantic_fingerprint")
    core_term_contract = validate_frozen_core_terms(profile, strict=True)
    post_retrieval_vocabulary = build_post_retrieval_vocabulary(profile)
    profile["core_term_contract"] = core_term_contract
    profile["post_retrieval_vocabulary"] = post_retrieval_vocabulary
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    query_sets = compile_query_sets(profile)
    profile["query_sets"] = query_sets
    plan = build_query_plan(profile, max_groups=120)
    controlled_supplemental_query_audit = {
        "policy_version": "v15.1-controlled-supplemental-query-audit-1",
        "terms": list(query_sets.get("controlled_supplemental_terms") or []),
        "provider_queries": {
            key: list(query_sets.get(key) or [])
            for key in (
                "pubmed_supplemental", "europe_pmc_supplemental", "crossref_supplemental",
                "semantic_scholar_supplemental", "openalex_supplemental",
            )
        },
        "plan_rows": [
            row for row in plan
            if clean_space(row.get("concept_role")) == "controlled_supplemental_identity"
        ],
    }
    event_query_plan = {"policy_version": "v14-event-driven-news-query-1", "queries": [], "evidence": []}
    scarce_news_mode = is_scarce_profile(settings.profile_id)
    news_profile = news_relevance_profile(profile, scarce=scarce_news_mode)
    progress(
        "query_plan",
        "compiled",
        query_counts={key: len(value) for key, value in query_sets.items() if isinstance(value, list)},
    )
    start, end = date_window(settings.window_days, timezone_name=settings.timezone)
    scholarly_search_end = publication_search_end(end, settings.publication_future_days)
    source_audit = SourceAudit()

    runtime_budget.start_stage("retrieval")
    if demo:
        raw_papers, raw_news = _demo_records()
        source_audit.add(source="Demo scholarly", status="success", records=len(raw_papers))
        source_audit.add(source="Demo news", status="success", records=len(raw_news))
    else:
        raw_papers: list[dict[str, Any]] = []
        scholarly_calls = [
            ("PubMed", lambda: search_pubmed(
                http, query_sets.get("pubmed_all") or query_sets.get("pubmed_core") or [], start, scholarly_search_end,
                secrets.get("NCBI_API_KEY", ""),
                per_query=settings.pubmed_per_query,
                max_total=settings.pubmed_total_limit,
                audit=source_audit,
            )),
            ("Europe PMC", lambda: search_europe_pmc(
                http, query_sets.get("europe_pmc_all") or query_sets.get("europe_pmc_core") or [], start, scholarly_search_end,
                per_query=settings.europe_pmc_per_query, audit=source_audit,
            )),
            ("Crossref", lambda: search_crossref(
                http, query_sets.get("crossref_all") or query_sets.get("crossref_core") or [], start, scholarly_search_end,
                secrets.get("CROSSREF_MAILTO", ""),
                per_query=settings.crossref_per_query,
                include_indexed=settings.crossref_include_indexed,
                audit=source_audit,
            )),
            ("Semantic Scholar", lambda: search_semantic_scholar(
                http, query_sets.get("semantic_scholar_all") or query_sets.get("semantic_scholar_core") or [], start, scholarly_search_end,
                secrets.get("SEMANTIC_SCHOLAR_API_KEY", ""),
                per_query=settings.semantic_per_query,
                anonymous_query_limit=settings.semantic_anonymous_query_limit,
                anonymous_delay_ms=settings.semantic_anonymous_delay_ms,
                audit=source_audit,
            )),
            ("OpenAlex", lambda: search_openalex(
                http, [], query_sets.get("openalex_all") or query_sets.get("openalex_core") or [], start, scholarly_search_end,
                secrets.get("OPENALEX_API_KEY", ""),
                per_query=settings.openalex_per_query, audit=source_audit,
            )),
            ("bioRxiv/medRxiv", lambda: search_biorxiv_medrxiv(
                http, start, end,
                max_records_per_server=settings.preprint_max_records_per_server,
                identity_terms=_preprint_identity_terms(profile),
                identity_filter=settings.preprint_identity_filter_enabled,
                audit=source_audit,
            )),
        ]
        progress("scholarly_retrieval", "start", providers=[name for name, _ in scholarly_calls], core_concepts=len(query_sets.get("core_concepts") or []))
        with ThreadPoolExecutor(max_workers=len(scholarly_calls)) as executor:
            future_names = {executor.submit(call): name for name, call in scholarly_calls}
            for future in as_completed(future_names):
                name = future_names[future]
                try:
                    rows = future.result()
                    raw_papers.extend(rows)
                    progress("scholarly_retrieval", "provider_complete", provider=name, records=len(rows), cumulative=len(raw_papers))
                except Exception as exc:
                    progress("scholarly_retrieval", "provider_failed", provider=name, error=str(exc)[:300])
                    source_audit.add(source="scholarly orchestration", status="failed", error=exc, details={"provider": name})
        progress("scholarly_retrieval", "complete", records=len(raw_papers))

        # Use high-confidence event/location clues found in current scholarly
        # records to expand news discovery before RSS/GDELT/WHO retrieval.
        event_source_papers, _event_date_rejections = filter_publication_window(
            [dict(item) for item in raw_papers], start, end, future_days=0
        )
        event_source_papers, _event_type_gate = filter_scholarly_records(event_source_papers)
        event_query_plan = derive_event_queries(
            event_source_papers, profile, max_queries=max(0, settings.news_event_query_limit)
        )
        augment_news_query_sets(query_sets, event_query_plan)
        plan = append_event_queries_to_plan(
            plan,
            event_query_plan,
            scarce_news_mode=scarce_news_mode,
            max_groups=120,
        )

        raw_news: list[dict[str, Any]] = []
        general_news_queries = unique_strings(
            (query_sets.get("general_news_en") or [])
            + (query_sets.get("general_news_zh") or [])
        )
        progress(
            "news_query_plan", "selected",
            core_concepts=len(query_sets.get("core_concepts") or []),
            rss_queries=len(general_news_queries),
            gdelt_queries=len(query_sets.get("gdelt_core") or []),
            reliefweb_queries=len(query_sets.get("reliefweb_core") or []),
        )
        who_terms = [x.get("news_en") or x.get("scholarly") for x in query_sets.get("core_concepts") or [] if isinstance(x, dict)]
        who_terms = unique_strings(who_terms + list(event_query_plan.get("queries") or []))
        news_calls = [
            ("Google News RSS", lambda: search_google_news(http, general_news_queries, start, end, audit=source_audit)),
            ("Bing News RSS", lambda: search_bing_news(http, general_news_queries, start, end, audit=source_audit)),
            ("GDELT", lambda: search_gdelt(http, query_sets.get("gdelt_core") or [], start, end, audit=source_audit)),
            ("ReliefWeb", lambda: search_reliefweb(
                http, query_sets.get("reliefweb_core") or [], start, end,
                appname=secrets.get("RELIEFWEB_APPNAME", ""), audit=source_audit,
            )),
            ("WHO", lambda: search_who(http, unique_strings(who_terms), start, end, audit=source_audit)),
        ]
        progress("news_retrieval", "start", providers=[name for name, _ in news_calls])
        with ThreadPoolExecutor(max_workers=len(news_calls)) as executor:
            future_names = {executor.submit(call): name for name, call in news_calls}
            for future in as_completed(future_names):
                name = future_names[future]
                try:
                    rows = future.result()
                    raw_news.extend(rows)
                    progress("news_retrieval", "provider_complete", provider=name, records=len(rows), cumulative=len(raw_news))
                except Exception as exc:
                    progress("news_retrieval", "provider_failed", provider=name, error=str(exc)[:300])
                    source_audit.add(source="news orchestration", status="failed", error=exc, details={"provider": name})
        progress("news_retrieval", "complete", records=len(raw_news))

        pubmed_7d_hits = sum(
            int(row.get("records") or 0) for row in source_audit.entries
            if row.get("source") == "PubMed" and row.get("status") == "success"
        )
        epmc_7d_hits = sum(
            int(row.get("records") or 0) for row in source_audit.entries
            if row.get("source") == "Europe PMC" and row.get("status") == "success"
        )
        if pubmed_7d_hits == 0 and epmc_7d_hits == 0:
            probe_start = end - timedelta(days=89)
            probe_pubmed_anchor_counts(
                http, query_sets.get("pubmed_all") or query_sets.get("pubmed_core") or [], probe_start, end,
                secrets.get("NCBI_API_KEY", ""), audit=source_audit,
            )
            probe_europe_pmc_anchor_counts(
                http, query_sets.get("europe_pmc_all") or query_sets.get("europe_pmc_core") or [], probe_start, end, audit=source_audit,
            )

        scholarly_names = {"PubMed", "Europe PMC", "Crossref", "Semantic Scholar", "OpenAlex", "bioRxiv", "medRxiv"}
        scholarly_success = any(
            row.get("source") in scholarly_names and row.get("status") == "success"
            for row in source_audit.entries
        )
        if not scholarly_success:
            raise RuntimeError("All scholarly source adapters failed or were skipped; inspect data/audit/source_status.json and GitHub logs")

    _annotate_retrieval_concepts(raw_papers, query_sets)
    _annotate_retrieval_concepts(raw_news, query_sets)

    runtime_budget.finish_stage("retrieval")
    raw_papers_before_window = len(raw_papers)
    raw_papers, paper_date_rejections = filter_publication_window(
        raw_papers,
        start,
        end,
        future_days=settings.publication_future_days,
    )
    papers_after_window = len(raw_papers)
    raw_papers, scholarly_record_type_gate_summary = filter_scholarly_records(raw_papers)
    papers_after_type_gate = len(raw_papers)
    paper_date_gate_summary = {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "future_grace_days": settings.publication_future_days,
        "search_end": scholarly_search_end.isoformat(),
        "raw": raw_papers_before_window,
        "accepted": papers_after_window,
        "accepted_by_status": dict(Counter(
            clean_space(item.get("publication_date_status")) or "unknown"
            for item in raw_papers
        )),
        "rejected": len(paper_date_rejections),
        "rejected_by_reason": dict(Counter(
            clean_space((item.get("publication_date_gate") or {}).get("reason")) or "unknown"
            for item in paper_date_rejections
        )),
        "rejected_records": [
            {
                "source": item.get("source"),
                "doi": item.get("doi"),
                "title": item.get("title"),
                "online_date": item.get("online_date"),
                "first_publication_date": item.get("first_publication_date"),
                "published_date": item.get("published_date"),
                "print_date": item.get("print_date"),
                "created_date": item.get("created_date"),
                "indexed_date": item.get("indexed_date"),
                "decision": item.get("publication_date_gate"),
            }
            for item in paper_date_rejections
        ],
    }
    paper_candidates = candidate_filter_papers(raw_papers, profile)
    papers_after_candidate_gate = len(paper_candidates)
    progress(
        "paper_publication_date_gate",
        "complete",
        raw=raw_papers_before_window,
        accepted=papers_after_window,
        rejected=len(paper_date_rejections),
        accepted_by_status=paper_date_gate_summary["accepted_by_status"],
        rejected_by_reason=paper_date_gate_summary["rejected_by_reason"],
        search_end=scholarly_search_end.isoformat(),
    )
    progress("paper_candidate_gate", "complete", raw=papers_after_window, after_window=papers_after_window, candidates=papers_after_candidate_gate)
    papers = dedup_papers(paper_candidates)
    papers_after_dedup = len(papers)
    papers = rank_papers(papers, profile)
    if settings.max_paper_candidates > 0:
        papers = papers[: settings.max_paper_candidates]
    progress("paper_dedup", "complete", candidates=papers_after_candidate_gate, unique=len(papers))

    raw_news_before_window = len(raw_news)
    raw_news = filter_news_window(raw_news, start, end)
    news_after_window = len(raw_news)
    news_candidates = candidate_filter_news(raw_news, news_profile)
    news_after_candidate_gate = len(news_candidates)
    news = dedup_news(news_candidates)
    news_after_dedup = len(news)
    news = rank_news(news)
    if settings.max_news_candidates > 0:
        news = news[: settings.max_news_candidates]
    progress("news_dedup", "complete", raw=raw_news_before_window, after_window=news_after_window, candidates=news_after_candidate_gate, unique=len(news))

    # v15 uses only a broad deterministic candidate gate before completion.
    # Scholarly LLM/final relevance is deliberately deferred until after
    # cross-source deduplication and metadata/abstract/full-text completion, so a
    # boundary record cannot be rejected merely because one provider omitted its
    # abstract. News keeps its independent pre-fetch relevance pass.
    for paper in papers:
        paper["evidence_level"] = "E1" if clean_space(paper.get("abstract")) else "E0"
        paper["full_text_method"] = "api_abstract" if paper.get("abstract") else "metadata_only"
    if demo:
        for paper in papers:
            paper["paper_id"] = paper.get("paper_id") or "paper-" + sha256_text(paper.get("title", ""))[:16]
        for article in news:
            article["news_id"] = article.get("news_id") or "news-" + sha256_text(article.get("title", ""))[:16]
            article["content"] = article.get("excerpt")
            article["content_status"] = "partial"
            article["resolved_url"] = article.get("url")

    papers_before_final_gate = len(papers)
    news_before_final_gate = len(news)
    relevance_review_cache = state.setdefault("relevance_review_cache", {}) if settings.relevance_review_cache_enabled else {}
    news_review_population = list(news)
    runtime_budget.start_stage("relevance")
    progress("relevance_review", "start", kind="news", candidates=len(news), mode=settings.llm_review_mode)
    news = final_filter(
        news,
        news_profile,
        llm,
        kind="news",
        review_cache=relevance_review_cache,
        review_mode=settings.llm_review_mode,
        compact_batch_tokens=settings.llm_compact_batch_tokens,
        escalation_batch_tokens=settings.llm_escalation_batch_tokens,
        continue_check=lambda: runtime_budget.can_start_expensive("relevance"),
    )
    progress("relevance_review", "complete", kind="news", accepted=len(news))
    news, news_cliff_guard_audit = apply_relevance_cliff_guard(
        news_review_population,
        news,
        news_profile,
        kind="news",
        previous_accepted=baseline_value(state, settings.profile_id, "news"),
    )
    update_baseline(state, settings.profile_id, "news", len(news))
    progress(
        "relevance_cliff_guard", "complete", kind="news",
        triggered=news_cliff_guard_audit.get("triggered"),
        recovered=news_cliff_guard_audit.get("recovered"),
        accepted=len(news),
    )
    news_after_final_gate = len(news)

    # v15 literature lifecycle is orchestrated in ranked batches. Existing
    # verified evidence is reviewed first. Metadata-only records are then
    # completed one batch at a time; every completed batch passes final
    # relevance and identity gates before analysis. If translation/analysis
    # failures leave the primary report below its target, the next batch is
    # completed. Once the target or completion budget is reached, all remaining
    # verified metadata is still final-reviewed for the supplementary catalog.
    prompts_dir = settings.project_root / "prompts"
    analysis_cache = state.setdefault("analysis_cache", {})
    translation_cache = state.setdefault("translation_cache", {})
    # Provider health is intentionally scoped to one profile run.  Translation
    # result caches persist across weeks, but a transient Google/MyMemory
    # outage from the prior run must not keep that provider disabled later.
    translation_cache.pop("__translation_provider_health_v15_2__", None)

    def analysis_cache_key(kind: str, item: dict[str, Any]) -> str:
        identity = clean_space(
            item.get("doi")
            or item.get("paper_id")
            or item.get("resolved_url")
            or item.get("url")
            or item.get("news_id")
            or item.get("title")
        )
        if kind == "paper":
            evidence_material = json.dumps(build_paper_evidence(item), ensure_ascii=False, sort_keys=True)
        else:
            evidence_material = clean_space(item.get("content") or item.get("excerpt"))
        return f"{kind}:{ANALYSIS_POLICY_VERSION}:{sha256_text(identity + '|' + evidence_material)}"

    demo_titles = {
        "Serologic evidence of hantavirus exposure in forest workers": "林业工作者汉坦病毒暴露的血清学证据",
        "Hantavirus infections in a changing world: a narrative review": "变化世界中的汉坦病毒感染：叙述性综述",
        "Health authority reports a suspected hantavirus case": "卫生部门报告一例疑似汉坦病毒病例",
    }

    def demo_translate_paper(item: dict[str, Any]) -> None:
        item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
        item["elements_en"] = dict((item.get("analysis") or {}).get("analysis") or {})
        demo_elements_zh = {
            "research_question_and_background": "研究评估林业工作者的汉坦病毒职业暴露风险。",
            "study_design_and_population": "研究对371名林业工作者开展横断面血清学调查。",
            "methods": "研究采用免疫学方法检测汉坦病毒IgG抗体，并评估啮齿动物接触史。",
            "main_results": "7.3%的参与者检出汉坦病毒IgG，频繁接触啮齿动物与暴露相关。",
            "interpretation_and_novelty": "结果支持职业环境中的啮齿动物暴露可能增加感染风险。",
            "scientific_and_public_health_significance": "研究为职业人群监测和暴露预防提供依据。",
            "limitations_and_evidence_strength": "研究仅覆盖单一区域且缺乏纵向随访，证据强度有限。",
            "scope_and_question": "综述讨论汉坦病毒流行病学、宿主生态、发病机制、诊断与预防。",
            "evidence_base_and_review_method": "综述整合现有研究并比较环境变化与溢出风险证据。",
            "consensus_and_key_conclusions": "环境变化和人类接触啮齿动物宿主会改变病毒溢出风险。",
            "controversies_and_evidence_gaps": "前瞻性监测不足，获批医学对策仍然有限。",
            "research_and_practice_implications": "未来应整合同一个健康监测并开展标准化临床研究。",
        }
        item["elements_zh"] = {
            key: demo_elements_zh.get(key, "该要素依据示例证据生成。")
            for key in item["elements_en"]
        }
        item["analysis_zh"] = dict(item["elements_zh"])
        item["abstract_zh"] = (
            "研究对371名林业工作者开展汉坦病毒抗体检测，7.3%的参与者检出IgG；"
            "频繁接触啮齿动物与暴露相关。"
            if "forest workers" in clean_space(item.get("title")).lower()
            else "本综述讨论汉坦病毒流行病学、宿主生态、诊断与预防，并指出监测和医学对策仍存在缺口。"
        )
        item["summary_zh"] = item["abstract_zh"]
        item["translation_ready"] = bool(item["elements_en"])
        item["translation_audit"] = {
            "policy_version": TRANSLATION_CACHE_VERSION,
            "ready": item["translation_ready"],
            "title": {"status": "demo"},
            "abstract_or_body": {"status": "demo"},
            "fields": {},
        }

    def demo_translate_news(item: dict[str, Any]) -> None:
        item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
        item["elements_en"] = dict((item.get("analysis") or {}).get("analysis") or {})
        item["elements_zh"] = {key: f"示例新闻字段：{value}" for key, value in item["elements_en"].items()}
        item["analysis_zh"] = dict(item["elements_zh"])
        item["content_zh"] = "示例中文新闻摘要，用于离线工程验证。"
        item["summary_zh"] = item["content_zh"]
        item["wechat_summary_zh"] = item["content_zh"]
        item["translation_ready"] = bool(item["elements_en"])
        item["translation_audit"] = {
            "policy_version": TRANSLATION_CACHE_VERSION,
            "ready": item["translation_ready"],
            "title": {"status": "demo"},
            "abstract_or_body": {"status": "demo"},
            "fields": {},
        }

    for item in papers:
        item["document_type_terms"] = post_retrieval_vocabulary.get("document_type_terms") or {}
        item["paper_priority_terms"] = post_retrieval_vocabulary.get("paper_priority_terms") or []
    comparison_target = min(
        max(settings.max_papers, settings.max_fulltexts),
        settings.max_papers + min(max(0, settings.display_candidate_buffer), settings.max_papers),
    )
    comparison_target = max(settings.max_papers, comparison_target)

    final_date_rejections: list[dict[str, Any]] = []
    final_date_passes: list[dict[str, Any]] = []

    def _final_publication_date_gate(items: list[dict[str, Any]], *, stage: str) -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        for source_item in items:
            item = normalize_literature_record(source_item)
            decision = assess_publication_date(
                item, start, end, future_days=settings.publication_future_days
            )
            item["canonical_publication_date"] = decision.canonical_date
            item["canonical_publication_date_basis"] = decision.canonical_basis
            item["availability_date"] = decision.canonical_date
            item["availability_date_basis"] = decision.canonical_basis
            item["publication_date_status"] = decision.status
            item["publication_date_gate_final"] = {**decision.to_dict(), "stage": stage}
            row = {
                "paper_id": item.get("paper_id"), "doi": item.get("doi"),
                "title": item.get("title"), "stage": stage, "decision": decision.to_dict(),
            }
            if decision.accepted:
                final_date_passes.append(row)
                accepted.append(item)
            else:
                final_date_rejections.append(row)
        return accepted

    full_paper_catalog = [normalize_literature_record(item) for item in rank_papers(papers, profile)]
    full_paper_catalog = _final_publication_date_gate(full_paper_catalog, stage="post_dedup_recalculation")
    for item in full_paper_catalog:
        metadata_verification(item)
        verified_evidence_status(item)
        if demo:
            item["content_completion"] = {
                "policy_version": "v15-dedup-first-dynamic-completion-2",
                "status": "evidence_ready" if (item.get("evidence_status") or {}).get("has_verified_evidence") else "metadata_only",
                "attempted": False,
                "reason": "demo_fixture",
            }

    catalog_order = [item.get("paper_id") for item in full_paper_catalog]
    catalog_by_id = {item.get("paper_id"): item for item in full_paper_catalog}
    accepted_ids: set[str] = set()
    reviewed_ids: set[str] = set()
    paper_review_population: list[dict[str, Any]] = []
    primary_ready: list[dict[str, Any]] = []
    analyzed_papers: list[dict[str, Any]] = []
    paper_analysis_processed = 0
    paper_gate_input = 0
    paper_gate_rejected = 0
    evidence_gate_batches: list[dict[str, Any]] = []
    completion_batches: list[dict[str, Any]] = []
    completion_processed = 0
    oa_email = secrets.get("UNPAYWALL_EMAIL") or secrets.get("CROSSREF_MAILTO", "")

    def _review_paper_batch(batch: list[dict[str, Any]], *, batch_label: str) -> list[dict[str, Any]]:
        if not batch:
            return []
        paper_review_population.extend(batch)
        reviewed_ids.update(str(item.get("paper_id")) for item in batch if item.get("paper_id"))
        progress(
            "relevance_review", "start", kind="paper", candidates=len(batch),
            mode=settings.llm_review_mode, stage_order="after_completion", batch=batch_label,
        )
        reviewed = final_filter(
            batch,
            profile,
            llm,
            kind="paper",
            review_cache=relevance_review_cache,
            review_mode=settings.llm_review_mode,
            compact_batch_tokens=settings.llm_compact_batch_tokens,
            escalation_batch_tokens=settings.llm_escalation_batch_tokens,
            continue_check=lambda: runtime_budget.can_start_expensive("relevance"),
        )
        progress(
            "relevance_review", "complete", kind="paper", accepted=len(reviewed),
            stage_order="after_completion", batch=batch_label,
        )
        return reviewed

    def _identity_gate_paper_batch(batch: list[dict[str, Any]], *, batch_label: str) -> list[dict[str, Any]]:
        nonlocal paper_gate_input, paper_gate_rejected
        if not batch:
            return []
        evidence = [item for item in batch if (item.get("evidence_status") or {}).get("has_verified_evidence")]
        metadata_only = [item for item in batch if not (item.get("evidence_status") or {}).get("has_verified_evidence")]
        accepted_evidence, summary = filter_post_enrichment(evidence, profile, "paper")
        accepted_metadata = [
            item for item in metadata_only
            if (item.get("metadata_verification") or {}).get("verified")
        ]
        rejected = len(evidence) - len(accepted_evidence) + len(metadata_only) - len(accepted_metadata)
        paper_gate_input += len(batch)
        paper_gate_rejected += rejected
        evidence_gate_batches.append({
            "batch": batch_label,
            "input": len(batch),
            "accepted": len(accepted_evidence) + len(accepted_metadata),
            "rejected": rejected,
            "evidence": summary,
            "metadata_only": {
                "input": len(metadata_only),
                "accepted_verified": len(accepted_metadata),
                "rejected_unverified": len(metadata_only) - len(accepted_metadata),
            },
        })
        return accepted_evidence + accepted_metadata

    def _analyze_translate_paper(item: dict[str, Any]) -> bool:
        nonlocal paper_analysis_processed
        if len(primary_ready) >= settings.max_papers:
            return False
        attempt_limit = max(settings.max_papers, int(round(comparison_target * max(1.0, settings.paper_analysis_attempt_multiplier))))
        if paper_analysis_processed >= attempt_limit:
            return False
        if not (item.get("evidence_status") or {}).get("has_verified_evidence"):
            return False
        paper_analysis_processed += 1
        ordinal = paper_analysis_processed
        if ordinal <= max(0, settings.analysis_crosscheck_top_n):
            item["analysis_level"] = "L3_cross_provider_verified"
        elif ordinal <= max(0, settings.analysis_fulltext_top_n):
            item["analysis_level"] = "L2_retrieved_fulltext_evidence"
        else:
            item["analysis_level"] = "L1_abstract_only"
        key = analysis_cache_key("paper", item)
        cached = analysis_cache.get(key) if settings.analysis_cache_enabled else None
        cached_analysis = (cached or {}).get("analysis") if isinstance(cached, dict) else None
        if isinstance(cached_analysis, dict) and (
            not settings.analysis_cache_success_only or cached_analysis.get("status") == "passed"
        ):
            source_language = annotate_source_language(item, kind="paper")
            item["analysis"] = sanitize_english_analysis(
                dict(cached_analysis), kind="paper", source_language=source_language
            )
            item["paper_type"] = cached.get("paper_type") or item.get("paper_type") or "research"
            item["analysis_cache"] = "hit_language_contract_checked"
        else:
            analyze_paper(item, llm, prompts_dir)
            item["analysis_cache"] = "miss"
            if settings.analysis_cache_enabled and (
                not settings.analysis_cache_success_only or (item.get("analysis") or {}).get("status") == "passed"
            ):
                analysis_cache[key] = {"analysis": item.get("analysis") or {}, "paper_type": item.get("paper_type")}
        if demo:
            demo_translate_paper(item)
        else:
            translate_record(
                item,
                profile=profile,
                llm=llm,
                prompts_dir=prompts_dir,
                cache=translation_cache,
                kind=item.get("paper_type") or "research",
                wechat_news_max_zh_chars=settings.wechat_news_max_zh_chars,
            )
        analyzed_papers.append(item)
        if item.get("translation_ready") and item.get("analysis_ready", True):
            primary_ready.append(item)
            return True
        return False

    progress(
        "literature_lifecycle", "start", catalog=len(full_paper_catalog),
        primary_target=settings.max_papers, comparison_target=comparison_target, completion_budget=settings.max_fulltexts,
        batch_size=settings.fulltext_batch_size,
    )
    progress("deep_analysis", "start", papers=len(full_paper_catalog), news=len(news))

    # Phase 1: establish a globally ranked, evidence-bearing comparison pool.
    # No paper is analyzed or translated until this pool is assembled.
    preexisting_evidence = [
        item for item in full_paper_catalog
        if (item.get("evidence_status") or {}).get("has_verified_evidence")
    ]
    reviewed = _review_paper_batch(preexisting_evidence, batch_label="preexisting_evidence")
    accepted = _identity_gate_paper_batch(reviewed, batch_label="preexisting_evidence")
    for item in accepted:
        accepted_ids.add(str(item.get("paper_id")))
        catalog_by_id[item.get("paper_id")] = item

    runtime_budget.start_stage("paper_processing")
    pending = [
        item for item in full_paper_catalog
        if not (item.get("evidence_status") or {}).get("has_verified_evidence")
    ]
    batch_size = max(1, settings.fulltext_batch_size)

    def _accepted_evidence_count() -> int:
        return sum(
            1 for paper_id in accepted_ids
            if (catalog_by_id.get(paper_id, {}).get("evidence_status") or {}).get("has_verified_evidence")
        )

    completion_stop_reason = "candidate_catalog_exhausted"
    while (
        _accepted_evidence_count() < comparison_target
        and pending
        and completion_processed < settings.max_fulltexts
    ):
        allowed, reason = runtime_budget.can_start_expensive("paper_processing")
        if not allowed:
            completion_stop_reason = reason
            break
        remaining_budget = settings.max_fulltexts - completion_processed
        current = pending[: min(batch_size, remaining_budget)]
        pending = pending[len(current):]
        batch_number = len(completion_batches) + 1
        batch_label = f"completion_{batch_number}"
        if demo:
            completed = current
            batch_audit = {
                "policy_version": "v16-dedup-first-comparison-pool-1",
                "catalog_size": len(current), "processed": 0, "evidence_ready": 0,
                "metadata_only": len(current), "batches": [], "stop_reason": "demo_fixture",
            }
        else:
            completed, batch_audit = complete_literature_catalog(
                current,
                enrich_one=lambda item: complete_scholarly_work(
                    http, item, oa_email, secrets.get("NCBI_API_KEY", "")
                ),
                primary_target=10**9, max_budget=len(current),
                batch_size=len(current), workers=5,
            )
        completion_processed += int(batch_audit.get("processed") or 0)
        completed = _final_publication_date_gate(completed, stage=f"post_completion_{batch_number}")
        completion_batches.append({
            "batch": batch_number, "input": len(current),
            "processed": int(batch_audit.get("processed") or 0),
            "evidence_ready": int(batch_audit.get("evidence_ready") or 0),
            "metadata_only": int(batch_audit.get("metadata_only") or 0),
            "comparison_evidence_before": _accepted_evidence_count(),
        })
        for item in completed:
            catalog_by_id[item.get("paper_id")] = item
        reviewed = _review_paper_batch(completed, batch_label=batch_label)
        accepted = _identity_gate_paper_batch(reviewed, batch_label=batch_label)
        for item in accepted:
            accepted_ids.add(str(item.get("paper_id")))
            catalog_by_id[item.get("paper_id")] = item
        completion_batches[-1]["comparison_evidence_after"] = _accepted_evidence_count()
        progress(
            "comparison_pool_replenishment", "batch_complete", batch=batch_number,
            completion_processed=completion_processed, comparison_evidence=_accepted_evidence_count(),
            comparison_target=comparison_target,
        )
    else:
        if _accepted_evidence_count() >= comparison_target:
            completion_stop_reason = "comparison_pool_target_reached"
        elif completion_processed >= settings.max_fulltexts:
            completion_stop_reason = "completion_budget_exhausted"

    # Phase 2: retain verified metadata as supplementary literature, but stop
    # this lower-value review when its dedicated five-minute budget is exhausted.
    runtime_budget.start_stage("supplementary_review")
    supplementary_review_stop_reason = "candidate_catalog_exhausted"
    for offset in range(0, len(pending), batch_size):
        allowed, reason = runtime_budget.can_start_expensive("supplementary_review")
        if not allowed:
            supplementary_review_stop_reason = reason
            break
        metadata_batch = pending[offset: offset + batch_size]
        label = f"metadata_only_{offset // batch_size + 1}"
        reviewed = _review_paper_batch(metadata_batch, batch_label=label)
        accepted = _identity_gate_paper_batch(reviewed, batch_label=label)
        for item in accepted:
            accepted_ids.add(str(item.get("paper_id")))
            catalog_by_id[item.get("paper_id")] = item
    runtime_budget.finish_stage("supplementary_review", supplementary_review_stop_reason)
    runtime_budget.finish_stage("relevance", "completed_or_deterministic_after_budget")

    paper_catalog = [
        catalog_by_id[paper_id]
        for paper_id in catalog_order
        if paper_id in accepted_ids and paper_id in catalog_by_id
    ]
    paper_catalog, paper_cliff_guard_audit = apply_relevance_cliff_guard(
        paper_review_population,
        paper_catalog,
        profile,
        kind="paper",
        previous_accepted=baseline_value(state, settings.profile_id, "paper"),
    )
    # Recovered scholarly records must still pass the existing post-enrichment
    # identity/metadata gate; the cliff guard only relaxes topical exclusion.
    recovered_ids = {
        str(item.get("paper_id")) for item in paper_catalog
        if clean_space(item.get("relevance_decision")).startswith("accept_cliff_guard_level_")
    }
    if recovered_ids:
        original_ids = set(accepted_ids)
        recovered_rows = [item for item in paper_catalog if str(item.get("paper_id")) in recovered_ids]
        gated_recovered = _identity_gate_paper_batch(recovered_rows, batch_label="cliff_guard_recovery")
        accepted_recovered_ids = {str(item.get("paper_id")) for item in gated_recovered}
        paper_catalog = [
            item for item in paper_catalog
            if str(item.get("paper_id")) in original_ids or str(item.get("paper_id")) in accepted_recovered_ids
        ]
        paper_cliff_guard_audit["identity_gate_recovered_input"] = len(recovered_rows)
        paper_cliff_guard_audit["identity_gate_recovered_accepted"] = len(gated_recovered)
        paper_cliff_guard_audit["final_accepted"] = len(paper_catalog)
    update_baseline(state, settings.profile_id, "paper", len(paper_catalog))
    progress(
        "relevance_cliff_guard", "complete", kind="paper",
        triggered=paper_cliff_guard_audit.get("triggered"),
        recovered=paper_cliff_guard_audit.get("recovered"),
        accepted=len(paper_catalog),
    )

    # Phase 3: analyze only the globally ranked comparison pool. The success
    # target is 50 primary reports; the comparison pool and attempt/time budgets
    # are independent hard ceilings.
    comparison_pool = rank_papers(
        [item for item in paper_catalog if (item.get("evidence_status") or {}).get("has_verified_evidence")],
        profile,
    )
    analysis_ranked_catalog = comparison_pool
    comparison_pool = comparison_pool[:comparison_target]
    analysis_attempt_limit = max(
        comparison_target,
        int(round(comparison_target * max(1.0, settings.paper_analysis_attempt_multiplier))),
    )
    analysis_queue = analysis_ranked_catalog[:analysis_attempt_limit]
    analysis_stop_reason = "analysis_candidate_queue_exhausted"
    for item in analysis_queue:
        if len(primary_ready) >= settings.max_papers:
            analysis_stop_reason = "primary_success_target_reached"
            break
        if paper_analysis_processed >= analysis_attempt_limit:
            analysis_stop_reason = "analysis_attempt_budget_exhausted"
            break
        allowed, reason = runtime_budget.can_start_expensive("paper_processing")
        if not allowed:
            analysis_stop_reason = reason
            break
        _analyze_translate_paper(item)
    runtime_budget.finish_stage("paper_processing", analysis_stop_reason)
    papers_after_final_gate = len(paper_catalog)
    paper_post_enrichment_rejected = paper_gate_rejected
    paper_content_rejected = 0
    paper_enrichment_selected = completion_processed
    evidence_ready_final = sum(
        bool((item.get("evidence_status") or {}).get("has_verified_evidence"))
        for item in full_paper_catalog
    )
    literature_completion_audit = {
        "policy_version": "v15-dedup-first-dynamic-completion-2",
        "catalog_size": len(full_paper_catalog),
        "primary_target": settings.max_papers,
        "comparison_target": comparison_target,
        "max_budget": settings.max_fulltexts,
        "batch_size": batch_size,
        "processed": completion_processed,
        "evidence_ready": evidence_ready_final,
        "metadata_only": len(full_paper_catalog) - evidence_ready_final,
        "comparison_pool_size": len(comparison_pool),
        "analysis_candidate_queue_size": len(analysis_queue),
        "primary_ready": len(primary_ready),
        "analysis_attempt_limit": analysis_attempt_limit,
        "analysis_attempts": paper_analysis_processed,
        "analysis_stop_reason": analysis_stop_reason,
        "supplementary_review_stop_reason": supplementary_review_stop_reason,
        "batches": completion_batches,
        "stop_reason": completion_stop_reason,
    }
    relevance_review_summary = {
        "policy_version": "v15-completion-before-final-relevance-2",
        "mode": settings.llm_review_mode,
        "compact_batch_tokens": settings.llm_compact_batch_tokens,
        "escalation_batch_tokens": settings.llm_escalation_batch_tokens,
        "papers": _review_summary(paper_review_population, papers_after_final_gate),
        "news": _review_summary(news_review_population, news_after_final_gate),
        "cliff_guard": {"papers": paper_cliff_guard_audit, "news": news_cliff_guard_audit},
        "paper_stage_order": [
            "candidate_gate", "cross_source_dedup", "ranked_batch",
            "content_completion", "final_relevance", "identity_gate",
            "analysis_translation", "dynamic_replenishment",
        ],
        "news_stage_order": [
            "candidate_gate", "dedup", "pre_fetch_relevance",
            "body_resolution", "post_fetch_relevance",
        ],
    }
    paper_post_enrichment_summary = {
        "policy_version": "v15-evidence-or-verified-metadata-final-gate-2",
        "input": paper_gate_input,
        "accepted": len(paper_catalog),
        "rejected": paper_post_enrichment_rejected,
        "batches": evidence_gate_batches,
        "metadata_only_retained": sum(
            not (item.get("evidence_status") or {}).get("has_verified_evidence")
            for item in paper_catalog
        ),
    }
    if len(relevance_review_cache) > 12000:
        for stale_key in list(relevance_review_cache)[: len(relevance_review_cache) - 12000]:
            relevance_review_cache.pop(stale_key, None)
    progress(
        "literature_lifecycle", "complete", catalog=len(full_paper_catalog),
        final_relevant=len(paper_catalog), completion_processed=completion_processed,
        primary_ready=len(primary_ready), comparison_target=comparison_target, stop_reason=literature_completion_audit["stop_reason"],
    )

    # News is split into deep main-news records and a supplementary catalog.
    # Every candidate here already passed date, source, topic, URL/title and
    # semantic deduplication plus pre-fetch relevance. Failure to retrieve a
    # publisher body no longer erases a trustworthy RSS-level record.
    news_queue = rank_news(news)
    news_queue_cap = min(
        len(news_queue),
        settings.max_news_fetches,
        settings.max_news + max(0, settings.display_candidate_buffer),
    )
    news_queue = news_queue[:news_queue_cap]
    news_enrichment_selected = len(news_queue)
    news_content_rejected = 0
    news_resolver_rejected_records: list[dict[str, Any]] = []
    supplementary_news_candidates: list[dict[str, Any]] = []
    fetched_main_candidates: list[dict[str, Any]] = []

    runtime_budget.start_stage("news_enrichment")
    news_enrichment_stop_reason = "completed"
    progress("display_content_enrichment", "start", kind="news", selected=len(news_queue), cap=settings.max_news_fetches)
    if demo:
        fetched_main_candidates = list(news_queue)
    else:
        news_workers = max(1, min(6, int(os.getenv("PIF_NEWS_ENRICH_WORKERS", "4"))))
        chunk_size = max(news_workers, news_workers * 2)
        for offset in range(0, len(news_queue), chunk_size):
            allowed, reason = runtime_budget.can_start_expensive("news_enrichment")
            if not allowed:
                news_enrichment_stop_reason = reason
                for pending_article in news_queue[offset:]:
                    pending_article = dict(pending_article)
                    pending_article["supplementary_reason"] = "news_enrichment_time_budget_not_reached"
                    supplementary_news_candidates.append(pending_article)
                break
            chunk = news_queue[offset: offset + chunk_size]
            fetched_news = _parallel_map(
                list(chunk),
                lambda item: resolve_and_extract_news(http, item, news_profile),
                workers=news_workers,
            )
            fetched_by_id = {item.get("news_id"): item for item in fetched_news}
            for original_article in chunk:
                article = fetched_by_id.get(original_article.get("news_id"), original_article)
                has_body = article.get("content_status") in {"full", "partial", "syndicated_summary"} and bool(clean_space(article.get("content")))
                if has_body:
                    fetched_main_candidates.append(article)
                    continue
                news_content_rejected += 1
                rejected_row = {
                    "news_id": article.get("news_id"), "title": article.get("title"),
                    "source": article.get("source"), "url": article.get("url"),
                    "resolved_url": article.get("resolved_url"), "content_status": article.get("content_status"),
                    "content_identity": article.get("content_identity"), "content_audit": article.get("content_audit"),
                }
                news_resolver_rejected_records.append(rejected_row)
                supplementary = dict(article)
                supplementary["supplementary_reason"] = "publisher_body_unavailable_rss_record_retained"
                supplementary_news_candidates.append(supplementary)
    runtime_budget.finish_stage("news_enrichment", news_enrichment_stop_reason)
    progress(
        "display_content_enrichment", "complete", kind="news",
        enriched=len(fetched_main_candidates), retained=len(fetched_main_candidates),
        rejected_no_body=news_content_rejected, supplementary_candidates=len(supplementary_news_candidates),
        stop_reason=news_enrichment_stop_reason,
    )

    news, news_circuit_summary = apply_news_content_circuit_breaker(fetched_main_candidates)
    circuit_kept_ids = {item.get("news_id") for item in news}
    for rejected in fetched_main_candidates:
        if rejected.get("news_id") not in circuit_kept_ids:
            fallback = dict(rejected)
            fallback["supplementary_reason"] = "main_news_content_circuit_rejected_metadata_retained"
            supplementary_news_candidates.append(fallback)
    news, news_post_enrichment_summary = filter_post_enrichment(news, news_profile, "news")
    post_kept_ids = {item.get("news_id") for item in news}
    for rejected in fetched_main_candidates:
        if rejected.get("news_id") in circuit_kept_ids and rejected.get("news_id") not in post_kept_ids:
            fallback = dict(rejected)
            fallback["supplementary_reason"] = "post_enrichment_deep_news_rejected_metadata_retained"
            supplementary_news_candidates.append(fallback)

    for article in news:
        article["relevance_ready"] = True
        mark_source_qualified(article, True, reason="source_date_body_identity_and_final_relevance_passed")
    news_post_enrichment_rejected = news_post_enrichment_summary["rejected"]
    news_circuit_rejected = news_circuit_summary["rejected"]
    news_content_gate_summary = {
        "policy_version": "v16-main-and-supplementary-news-1",
        "resolver": {
            "input": news_enrichment_selected,
            "rejected": news_content_rejected,
            "rejected_records": news_resolver_rejected_records,
            "stop_reason": news_enrichment_stop_reason,
        },
        "circuit_breaker": news_circuit_summary,
        "post_enrichment": news_post_enrichment_summary,
        "supplementary_candidates": len(supplementary_news_candidates),
    }
    progress(
        "post_enrichment_gate", "complete",
        papers_retained=len(paper_catalog), papers_rejected=paper_post_enrichment_rejected,
        news_retained=len(news), news_resolver_rejected=news_content_rejected,
        news_circuit_rejected=news_circuit_rejected,
        news_relevance_rejected=news_post_enrichment_rejected,
        supplementary_news_candidates=len(supplementary_news_candidates),
    )

    news, paper_catalog = attach_news_to_papers(news, paper_catalog)

    # Analyze and translate main news until either the 30-minute stage budget or
    # the global finalization reserve is reached. Remaining qualified records are
    # preserved as supplementary news rather than lost.
    analyzed_news: list[dict[str, Any]] = []
    runtime_budget.start_stage("news_analysis")
    news_analysis_stop_reason = "completed"
    ranked_main_news = rank_news(news)
    for index, article in enumerate(ranked_main_news):
        allowed, reason = runtime_budget.can_start_expensive("news_analysis")
        if not allowed:
            news_analysis_stop_reason = reason
            for pending_article in ranked_main_news[index:]:
                fallback = dict(pending_article)
                fallback["supplementary_reason"] = "news_analysis_time_budget_not_reached"
                supplementary_news_candidates.append(fallback)
            break
        key = analysis_cache_key("news", article)
        cached = analysis_cache.get(key) if settings.analysis_cache_enabled else None
        cached_analysis = (cached or {}).get("analysis") if isinstance(cached, dict) else None
        if isinstance(cached_analysis, dict) and (
            not settings.analysis_cache_success_only or cached_analysis.get("status") == "passed"
        ):
            source_language = annotate_source_language(article, kind="news")
            article["analysis"] = sanitize_english_analysis(
                dict(cached_analysis), kind="news", source_language=source_language
            )
            article["analysis_cache"] = "hit_language_contract_checked"
        else:
            analyze_news(article, llm, prompts_dir)
            article["analysis_cache"] = "miss"
            if settings.analysis_cache_enabled and (
                not settings.analysis_cache_success_only or (article.get("analysis") or {}).get("status") == "passed"
            ):
                analysis_cache[key] = {"analysis": article.get("analysis") or {}}
        if demo:
            demo_translate_news(article)
        else:
            translate_record(
                article, profile=profile, llm=llm, prompts_dir=prompts_dir,
                cache=translation_cache, kind="news",
                wechat_news_max_zh_chars=settings.wechat_news_max_zh_chars,
            )
        finalize_news_state(article)
        analyzed_news.append(article)
    runtime_budget.finish_stage("news_analysis", news_analysis_stop_reason)
    progress(
        "deep_analysis", "complete", papers=paper_analysis_processed, news=len(analyzed_news),
        news_stop_reason=news_analysis_stop_reason,
    )

    # Rank the full evidence-bearing comparison pool globally only after content
    # completion, final relevance, analysis and translation. Top50 is selected from
    # this 75-100 candidate pool rather than from the first 50 completed records.
    ranked_primary_ready = rank_papers(primary_ready, profile)

    # Translate supplementary titles only; no abstract, conclusion, or structured
    # element is generated from metadata-only evidence.
    primary_candidate_ids = {item.get("paper_id") for item in ranked_primary_ready[: settings.max_papers]}
    supplementary_title_candidates = [
        item for item in paper_catalog
        if item.get("paper_id") not in primary_candidate_ids
        and (item.get("metadata_verification") or {}).get("verified")
    ][: settings.max_supplementary_papers]
    for item in supplementary_title_candidates:
        if demo:
            item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
            item["supplementary_translation_audit"] = {"ready": True, "title": {"status": "demo"}}
        elif runtime_budget.should_finalize():
            item["title_zh"] = clean_space(item.get("title"))
            item["supplementary_translation_audit"] = {
                "ready": False,
                "title": {"status": "english_fallback", "reason": "finalization_reserve_entered"},
            }
        else:
            translate_title_only(
                item, profile=profile, llm=llm, prompts_dir=prompts_dir,
                cache=translation_cache,
            )

    papers, supplementary_papers, literature_selection_audit = select_primary_and_supplementary(
        paper_catalog,
        primary_ready=ranked_primary_ready,
        primary_limit=settings.max_papers,
        supplementary_limit=settings.max_supplementary_papers,
    )
    literature_selection_audit["comparison_target"] = comparison_target
    literature_selection_audit["comparison_pool_ready"] = len(ranked_primary_ready)
    literature_selection_audit["global_rerank_after_completion"] = True
    ranked_paper_ready_pool = list(ranked_primary_ready)
    paper_top_n_excluded = max(0, len(ranked_primary_ready) - len(papers))
    translation_rejected_papers = max(0, paper_analysis_processed - len(primary_ready))

    # News display eligibility depends on qualified source evidence plus an
    # English analysis. Chinese translation completeness is an independent
    # quality state and may fall back to English without deleting the news item.
    news_ready_pool = [item for item in analyzed_news if item.get("display_ready")]
    ranked_news_ready_pool = rank_news(news_ready_pool)
    for display_rank, item in enumerate(ranked_news_ready_pool, start=1):
        item["display_rank"] = display_rank
        item["selected_for_display"] = display_rank <= settings.max_news
    news = ranked_news_ready_pool[: settings.max_news]

    # Build a separate, metadata-safe supplementary news catalog. Main-news IDs
    # always win. Title translation is the only LLM/translation work performed.
    main_news_ids = {clean_space(item.get("news_id")) for item in news}
    supplementary_news: list[dict[str, Any]] = []
    supplementary_seen: set[str] = set()
    for candidate in rank_news(supplementary_news_candidates):
        key = clean_space(candidate.get("news_id")) or sha256_text(
            clean_space(candidate.get("title")).casefold() + "|" + clean_space(candidate.get("published_date"))
        )
        if not key or key in main_news_ids or key in supplementary_seen:
            continue
        title = clean_space(candidate.get("title"))
        if not title:
            continue
        supplementary_seen.add(key)
        item = dict(candidate)
        item["display_mode"] = "supplementary_news"
        item["analysis"] = {}
        item.pop("elements_en", None)
        item.pop("elements_zh", None)
        item.pop("analysis_en", None)
        item.pop("analysis_zh", None)
        if item.get("snippet_duplicate_of_title"):
            item["excerpt"] = ""
        if demo:
            item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
            item["supplementary_translation_audit"] = {"ready": True, "title": {"status": "demo"}}
        elif runtime_budget.should_finalize():
            item["title_zh"] = clean_space(item.get("title"))
            item["translation_status"] = "english_fallback"
            item["supplementary_translation_audit"] = {
                "ready": False,
                "title": {"status": "english_fallback", "reason": "finalization_reserve_entered"},
            }
        else:
            translate_title_only(
                item, profile=profile, llm=llm, prompts_dir=prompts_dir,
                cache=translation_cache,
            )
        supplementary_news.append(item)
        if len(supplementary_news) >= settings.max_supplementary_news:
            break

    translation_rejected_news = sum(1 for item in analyzed_news if not item.get("translation_complete"))
    news_display_rejected = len(analyzed_news) - len(news_ready_pool)
    news_top_n_excluded = max(0, len(ranked_news_ready_pool) - len(news))
    progress(
        "translation_gate", "complete",
        primary_ready_pool=len(primary_ready), papers_retained=len(papers), papers_rejected=translation_rejected_papers,
        paper_top_n_limit=settings.max_papers, paper_top_n_excluded=paper_top_n_excluded,
        supplementary_retained=len(supplementary_papers), supplementary_limit=settings.max_supplementary_papers,
        news_ready_pool=len(news_ready_pool), news_retained=len(news), supplementary_news_retained=len(supplementary_news), news_display_rejected=news_display_rejected,
        news_translation_incomplete=translation_rejected_news,
        news_top_n_limit=settings.max_news, news_top_n_excluded=news_top_n_excluded,
        selection_policy="primary_top_n_plus_supplementary_catalog",
    )

    analysis_quality_pool = summarize_analysis_quality(
        analyzed_papers,
        analyzed_news,
        warning_ratio=settings.analysis_fallback_warning_ratio,
        critical_ratio=settings.analysis_fallback_critical_ratio,
        preflight=llm_preflight,
        scope="processed_candidate_pool",
    )

    def has_real_title_translation(item: dict[str, Any]) -> bool:
        title = clean_space(item.get("title_zh"))
        if not title or "翻译暂不可用" in title or "中文标题暂不可用" in title:
            return False
        status = clean_space(((item.get("translation_audit") or {}).get("title") or {}).get("status"))
        return status not in {"translation_unavailable", "empty_source"}

    translated_primary_and_news = sum(1 for item in papers + news if has_real_title_translation(item))
    translated_supplementary_titles = sum(
        1 for item in supplementary_papers if clean_space(item.get("title_zh"))
    )
    translated_supplementary_news_titles = sum(
        1 for item in supplementary_news if clean_space(item.get("title_zh"))
    )
    analysis_quality_displayed = summarize_analysis_quality(
        papers,
        news,
        warning_ratio=settings.analysis_fallback_warning_ratio,
        critical_ratio=settings.analysis_fallback_critical_ratio,
        preflight=llm_preflight,
        scope="displayed_primary_and_news",
    )
    # v15 keeps complete quality diagnostics in backend audit files, but does
    # not occupy the public report with an analysis-quality banner. GitHub logs
    # still surface a non-blocking warning for maintainers.
    if analysis_quality_displayed.get("severity") in {"warning", "critical", "unavailable"}:
        progress(
            "analysis_quality",
            "final_displayed_degraded",
            severity=analysis_quality_displayed.get("severity"),
            message=analysis_quality_displayed.get("message_zh"),
            fallback_ratio=(analysis_quality_displayed.get("combined") or {}).get("fallback_ratio"),
            top_failures=analysis_quality_displayed.get("top_failure_categories"),
            scope="displayed_primary_and_news",
        )
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
            print(
                f"::warning title=Structured analysis degraded::{analysis_quality_displayed.get('message_zh')}",
                flush=True,
            )
    analysis_quality = {
        key: value for key, value in analysis_quality_displayed.items()
        if key != "fallback_records"
    }
    analysis_quality["scope"] = "displayed_primary_and_news"
    analysis_quality["candidate_pool"] = {
        key: value for key, value in analysis_quality_pool.items()
        if key not in {"fallback_records", "preflight"}
    }

    issue_date = end.isoformat()
    # The overview is evidence-locked: only final primary reports and qualified
    # news may contribute. Supplementary metadata can never be summarized into
    # methods, findings, conclusions or public-health claims.
    overview = build_overviews(
        profile,
        papers,
        news,
        llm,
        prompts_dir,
        minimum=settings.overview_min_items,
        maximum=settings.overview_max_items,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        allow_llm=not runtime_budget.should_finalize(),
    )
    source_status = source_audit.summary()
    anchor_coverage = _anchor_coverage(profile, query_sets, source_audit.entries)
    evidence_ready_catalog = sum(
        bool((item.get("evidence_status") or {}).get("has_verified_evidence"))
        for item in paper_catalog
    )
    metadata_only_catalog = sum(
        (item.get("evidence_status") or {}).get("level") == "metadata_only"
        for item in paper_catalog
    )
    supplementary_metadata_only = sum(
        item.get("supplementary_reason") == "verified_metadata_without_public_abstract_or_full_text"
        for item in supplementary_papers
    )
    supplementary_evidence_not_top_n = len(supplementary_papers) - supplementary_metadata_only

    paper_final_date_gate_summary = {
        "policy_version": "v15.1-two-pass-canonical-publication-date-1",
        "initial_gate": paper_date_gate_summary,
        "post_dedup_and_completion": {
            "accepted_assessments": len(final_date_passes),
            "rejected": len(final_date_rejections),
            "rejected_records": final_date_rejections,
            "basis_counts": dict(Counter(
                clean_space((row.get("decision") or {}).get("canonical_basis")) or "missing"
                for row in final_date_passes
            )),
        },
    }

    retrieval_funnel = {
        "papers": {
            "raw": raw_papers_before_window,
            "after_window": papers_after_window,
            "after_type_gate": papers_after_type_gate,
            "type_gate_rejected": scholarly_record_type_gate_summary.get("rejected", 0),
            "after_candidate_gate": papers_after_candidate_gate,
            "after_dedup": papers_after_dedup,
            "before_final_relevance": papers_before_final_gate,
            "after_final_relevance": papers_after_final_gate,
            "relevant_catalog_after_completion_and_identity_gate": len(paper_catalog),
            "content_completion_processed": paper_enrichment_selected,
            "content_completion_budget": settings.max_fulltexts,
            "content_completion_batch_size": settings.fulltext_batch_size,
            "evidence_ready_catalog": evidence_ready_catalog,
            "metadata_only_catalog": metadata_only_catalog,
            "post_completion_rejected": paper_post_enrichment_rejected,
            "primary_analysis_processed": paper_analysis_processed,
            "primary_ready_before_top_n": len(ranked_primary_ready),
            "primary_comparison_target": comparison_target,
            "primary_top_n_limit": settings.max_papers,
            "primary_displayed": len(papers),
            "primary_top_n_excluded_to_supplementary": supplementary_evidence_not_top_n,
            "supplementary_limit": settings.max_supplementary_papers,
            "supplementary_displayed": len(supplementary_papers),
            "supplementary_metadata_only": supplementary_metadata_only,
            "selection_policy": "verified_primary_top_n_plus_verified_supplementary_catalog",
        },
        "news": {
            "raw": raw_news_before_window,
            "after_window": news_after_window,
            "after_candidate_gate": news_after_candidate_gate,
            "after_dedup": news_after_dedup,
            "before_final_gate": news_before_final_gate,
            "after_final_gate": news_after_final_gate,
            "selected_for_content_enrichment": news_enrichment_selected,
            "content_rejected": news_content_rejected,
            "content_circuit_rejected": news_circuit_rejected,
            "post_enrichment_rejected": news_post_enrichment_rejected,
            "translation_incomplete": translation_rejected_news,
            "display_rejected": news_display_rejected,
            "ready_before_top_n": len(ranked_news_ready_pool),
            "top_n_limit": settings.max_news,
            "top_n_excluded": news_top_n_excluded,
            "selection_policy": "qualification_independent_from_wechat_length",
            "displayed": len(news),
            "supplementary_displayed": len(supplementary_news),
            "supplementary_limit": settings.max_supplementary_news,
        },
    }

    core_term_contract = validate_frozen_core_terms(profile, strict=True)
    post_retrieval_vocabulary = build_post_retrieval_vocabulary(profile)
    publication_continuity = {
        "policy_version": "v17.1-valid-output-continuity-1",
        "papers": {
            "status": paper_cliff_guard_audit.get("continuity_status", "standard_output"),
            "resolved_to_target": bool(paper_cliff_guard_audit.get("resolved", True)),
            "accepted": len(paper_catalog),
            "target": paper_cliff_guard_audit.get("target_accepted", len(paper_catalog)),
        },
        "news": {
            "status": news_cliff_guard_audit.get("continuity_status", "standard_output"),
            "resolved_to_target": bool(news_cliff_guard_audit.get("resolved", True)),
            "accepted": len(news),
            "target": news_cliff_guard_audit.get("target_accepted", len(news)),
        },
        "publication_continues_when_low_volume": True,
        "empty_sections_are_valid": True,
        "hard_identity_conflicts_never_relaxed": True,
        "fabricated_records_forbidden": True,
    }

    issue = {
        "schema_version": "6.3",
        "issue_id": f"{settings.profile_id}-{issue_date}",
        "profile_id": settings.profile_id,
        "issue_date": issue_date,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "publication_search_end": scholarly_search_end.isoformat(),
        "publication_future_days": settings.publication_future_days,
        "generated_at": utc_now_iso(),
        "title_zh": f"{profile.get('display_name_zh') or settings.profile_id}每周情报",
        "title_en": f"{profile.get('display_name_en') or settings.profile_id} Weekly Intelligence",
        "profile": profile,
        "core_term_contract": core_term_contract,
        "post_retrieval_vocabulary": post_retrieval_vocabulary,
        "review_vocabulary_lifecycle": review_vocabulary_audit,
        "runtime_budget": runtime_budget.audit(),
        "controlled_supplemental_query_audit": controlled_supplemental_query_audit,
        "query_plan": plan,
        "source_status": source_status,
        "anchor_coverage": anchor_coverage,
        "relevance_review": relevance_review_summary,
        "publication_continuity": publication_continuity,
        "analysis_quality": analysis_quality,
        "llm_usage": llm.usage_snapshot(),
        "retrieval_funnel": retrieval_funnel,
        "literature_completion": literature_completion_audit,
        "literature_selection": literature_selection_audit,
        "publication_date_gate": {
            "policy_version": paper_final_date_gate_summary.get("policy_version"),
            "initial_gate": {
                key: value for key, value in paper_date_gate_summary.items()
                if key != "rejected_records"
            },
            "post_dedup_and_completion": {
                key: value for key, value in paper_final_date_gate_summary.get("post_dedup_and_completion", {}).items()
                if key != "rejected_records"
            },
        },
        "scholarly_record_type_gate": {
            key: value for key, value in scholarly_record_type_gate_summary.items()
            if key != "rejected_records"
        },
        "event_query_expansion": event_query_plan,
        "scarce_news_mode": scarce_news_mode,
        "news_content_gate": {
            "resolver_rejected": news_content_gate_summary["resolver"]["rejected"],
            "circuit_rejected": news_content_gate_summary["circuit_breaker"]["rejected"],
            "post_enrichment_rejected": news_content_gate_summary["post_enrichment"]["rejected"],
        },
        "overview": overview,
        "papers": papers,
        "supplementary_papers": supplementary_papers,
        "news": news,
        "supplementary_news": supplementary_news,
        "metrics": {
            "raw_papers": raw_papers_before_window,
            # Backward-compatible alias: `papers` remains the number of deep
            # primary reports used by the existing WeChat package consumer.
            "papers": len(papers),
            "primary_papers": len(papers),
            "supplementary_papers": len(supplementary_papers),
            "relevant_paper_catalog": len(paper_catalog),
            "evidence_ready_catalog": evidence_ready_catalog,
            "metadata_only_catalog": metadata_only_catalog,
            "research": sum(1 for p in papers if p.get("paper_type") == "research"),
            "reviews": sum(1 for p in papers if p.get("paper_type") == "review"),
            "raw_news": raw_news_before_window,
            "news": len(news),
            "supplementary_news": len(supplementary_news),
            "translated": translated_primary_and_news,
            "supplementary_titles_translated": translated_supplementary_titles,
            "supplementary_news_titles_translated": translated_supplementary_news_titles,
            "analysis_passed": (analysis_quality_displayed.get("combined") or {}).get("passed", 0),
            "analysis_fallback": (analysis_quality_displayed.get("combined") or {}).get("fallback", 0),
            "analysis_fallback_ratio": (analysis_quality_displayed.get("combined") or {}).get("fallback_ratio", 0.0),
            "paper_analysis_processed": paper_analysis_processed,
            "paper_ready_before_top_n": len(ranked_primary_ready),
            "paper_comparison_target": comparison_target,
            "paper_top_n_limit": settings.max_papers,
            "paper_top_n_excluded": paper_top_n_excluded,
            "supplementary_limit": settings.max_supplementary_papers,
            "news_ready_before_top_n": len(ranked_news_ready_pool),
            "news_top_n_limit": settings.max_news,
            "news_top_n_excluded": news_top_n_excluded,
            "supplementary_news_limit": settings.max_supplementary_news,
        },
    }
    write_issue(settings.output_dir, issue)
    cover_meta = ensure_profile_cover(settings, profile, issue_date)
    issue["cover"] = cover_meta
    write_issue(settings.output_dir, issue)
    runtime_budget.start_stage("finalization")
    render_site(issue, settings.output_dir)
    wechat_content_budget = render_wechat_package(issue, settings.output_dir, cover_meta)
    runtime_budget.finish_stage("finalization", "completed")
    issue["wechat_content_budget"] = wechat_content_budget
    issue["runtime_budget"] = runtime_budget.audit()
    write_issue(settings.output_dir, issue)

    audit_dir = settings.output_dir / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    dump_json(audit_dir / "query_plan.json", plan)
    dump_json(audit_dir / "controlled_supplemental_queries.json", controlled_supplemental_query_audit)
    dump_json(audit_dir / "profile.json", profile)
    dump_json(audit_dir / "core_term_contract.json", core_term_contract)
    dump_json(audit_dir / "post_retrieval_vocabulary.json", post_retrieval_vocabulary)
    dump_json(audit_dir / "source_status.json", source_status)
    dump_json(audit_dir / "anchor_coverage.json", anchor_coverage)
    dump_json(audit_dir / "relevance_review.json", relevance_review_summary)
    dump_json(audit_dir / "relevance_cliff_guard.json", {"papers": paper_cliff_guard_audit, "news": news_cliff_guard_audit})
    dump_json(audit_dir / "publication_continuity.json", publication_continuity)
    dump_json(audit_dir / "analysis_quality.json", {
        "candidate_pool": analysis_quality_pool,
        "displayed_primary_and_news": analysis_quality_displayed,
        "run": analysis_quality_pool,
    })
    dump_json(audit_dir / "llm_provider_usage.json", llm.usage_snapshot())
    dump_json(audit_dir / "retrieval_funnel.json", retrieval_funnel)
    dump_json(audit_dir / "literature_content_completion.json", literature_completion_audit)
    dump_json(audit_dir / "literature_selection.json", literature_selection_audit)
    dump_json(audit_dir / "publication_date_gate.json", paper_final_date_gate_summary)
    dump_json(audit_dir / "scholarly_record_type_gate.json", scholarly_record_type_gate_summary)
    dump_json(audit_dir / "event_query_expansion.json", event_query_plan)
    dump_json(audit_dir / "news_content_gate.json", news_content_gate_summary)
    dump_json(audit_dir / "paper_post_enrichment_gate.json", paper_post_enrichment_summary)
    dump_json(audit_dir / "runtime_budget.json", runtime_budget.audit())
    dump_json(audit_dir / "review_vocabulary_lifecycle.json", review_vocabulary_audit)
    dump_json(audit_dir / "wechat_content_budget.json", wechat_content_budget)
    dump_json(audit_dir / "display_selection.json", {
        "policy_version": "v15.1-global-comparison-pool-top50-supplementary-top100-1",
        "selection_policy": "verified_primary_top_n_plus_verified_supplementary_catalog",
        "papers": {
            "relevant_catalog": len(paper_catalog),
            "evidence_ready_catalog": evidence_ready_catalog,
            "metadata_only_catalog": metadata_only_catalog,
            "analysis_processed": paper_analysis_processed,
            "primary_ready_before_top_n": len(ranked_primary_ready),
            "primary_comparison_target": comparison_target,
            "primary_top_n_limit": settings.max_papers,
            "primary_displayed": len(papers),
            "primary_ids": [item.get("paper_id") for item in papers],
            "supplementary_limit": settings.max_supplementary_papers,
            "supplementary_displayed": len(supplementary_papers),
            "supplementary_ids": [item.get("paper_id") for item in supplementary_papers],
        },
        "news": {
            "ready_before_top_n": len(ranked_news_ready_pool),
            "top_n_limit": settings.max_news,
            "displayed": len(news),
            "excluded_by_top_n": news_top_n_excluded,
            "selected_ids": [item.get("news_id") for item in news],
            "excluded_ids": [item.get("news_id") for item in ranked_news_ready_pool[settings.max_news:]],
            "supplementary_limit": settings.max_supplementary_news,
            "supplementary_displayed": len(supplementary_news),
            "supplementary_ids": [item.get("news_id") for item in supplementary_news],
        },
    })

    jsonl_paths = [
        audit_dir / "papers.jsonl",
        audit_dir / "supplementary_papers.jsonl",
        audit_dir / "literature_catalog.jsonl",
        audit_dir / "news.jsonl",
        audit_dir / "supplementary_news.jsonl",
        audit_dir / "eligible_news.jsonl",
    ]
    for jsonl_path in jsonl_paths:
        jsonl_path.unlink(missing_ok=True)
    for candidate in paper_catalog:
        append_jsonl(audit_dir / "literature_catalog.jsonl", candidate)
    for candidate in ranked_news_ready_pool:
        append_jsonl(audit_dir / "eligible_news.jsonl", {
            "news_id": candidate.get("news_id"),
            "news_state": candidate.get("news_state"),
            "title": candidate.get("title"),
            "publisher": candidate.get("publisher") or candidate.get("source"),
            "published_date": candidate.get("published_date"),
            "resolved_url": candidate.get("resolved_url") or candidate.get("url"),
            "priority_tier": candidate.get("priority_tier"),
            "quality_score": candidate.get("quality_score"),
            "quality_reasons": candidate.get("quality_reasons"),
            "display_rank": candidate.get("display_rank"),
            "selected_for_display": candidate.get("selected_for_display"),
        })
    for paper in papers:
        append_jsonl(audit_dir / "papers.jsonl", {
            "paper_id": paper.get("paper_id"),
            "doi": paper.get("doi"),
            "title": paper.get("title"),
            "analysis": paper.get("analysis"),
            "translation_audit": paper.get("translation_audit"),
            "content_audit": paper.get("content_audit"),
            "metadata_verification": paper.get("metadata_verification"),
            "evidence_status": paper.get("evidence_status"),
        })
    for paper in supplementary_papers:
        append_jsonl(audit_dir / "supplementary_papers.jsonl", paper)
    for article in supplementary_news:
        append_jsonl(audit_dir / "supplementary_news.jsonl", article)
    for article in news:
        append_jsonl(audit_dir / "news.jsonl", {
            "news_id": article.get("news_id"),
            "news_state": article.get("news_state"),
            "title": article.get("title"),
            "analysis": article.get("analysis"),
            "translation_audit": article.get("translation_audit"),
            "content_audit": article.get("content_audit"),
            "content_identity": article.get("content_identity"),
            "relevance_post_enrichment": article.get("relevance_post_enrichment"),
        })
    dump_json(audit_dir / "supplementary_news.json", {
        "policy_version": "v16-supplementary-news-1",
        "candidate_count": len(supplementary_news_candidates),
        "displayed": len(supplementary_news),
        "limit": settings.max_supplementary_news,
        "records": supplementary_news,
    })
    save_state(settings.state_dir, state)
    progress("pipeline", "complete", profile=settings.profile_id, issue_id=issue.get("issue_id"), metrics=issue.get("metrics"))
    return issue
