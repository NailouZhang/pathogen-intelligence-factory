from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import os
from typing import Any

from .analysis import ANALYSIS_POLICY_VERSION, analyze_news, analyze_paper, build_paper_evidence
from .analysis_quality import summarize_analysis_quality
from .bootstrap import _fallback_profile, build_profile
from .config import Settings, load_profile, load_seed
from .content import apply_news_content_circuit_breaker, enrich_scholarly_work, resolve_and_extract_news
from .dates import date_window, publication_search_end
from .dedup import attach_news_to_papers, dedup_news, dedup_papers
from .http import HttpClient
from .llm import LLMRouter
from .news import filter_news_window, search_bing_news, search_gdelt, search_google_news, search_reliefweb, search_who
from .query_plan import build_query_plan, compile_query_sets
from .event_query import (
    append_event_queries_to_plan,
    augment_news_query_sets,
    derive_event_queries,
    is_scarce_profile,
    news_relevance_profile,
)
from .relevance import candidate_filter_news, candidate_filter_papers, filter_post_enrichment, final_filter
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
from .translation import TRANSLATION_CACHE_VERSION, translate_record
from .overview import build_overviews
from .progress import progress
from .cover import ensure_profile_cover
from .utils import append_jsonl, clean_space, dump_json, load_json, sha256_text, utc_now_iso, unique_strings


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
        "pubmed": query_sets.get("pubmed_core") or [],
        "europe_pmc": query_sets.get("europe_pmc_core") or [],
        "crossref": query_sets.get("crossref_core") or [],
        "semantic_scholar": query_sets.get("semantic_scholar_core") or [],
        "openalex": query_sets.get("openalex_core") or [],
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


def run_pipeline(settings: Settings, *, demo: bool = False) -> dict[str, Any]:
    progress("pipeline", "start", profile=settings.profile_id, demo=demo)
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
    )
    for provider_name in llm.configured_providers():
        if provider_name in {"openrouter", "siliconflow"}:
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
    profile = load_profile(settings)
    seed = load_seed(settings.project_root, settings.profile_id)
    seed_hash = sha256_text(__import__("json").dumps(seed, ensure_ascii=False, sort_keys=True))
    profile_stale = not profile or profile.get("seed_hash") != seed_hash
    progress(
        "profile",
        "decision",
        refresh=settings.refresh_profile,
        stale=profile_stale,
        cached=bool(profile),
    )
    if demo and (not profile or profile_stale):
        profile = _fallback_profile(seed, [])
        profile["seed_hash"] = seed_hash
    elif settings.refresh_profile or profile_stale or (profile and profile.get("generated_by") == "bundled_seed"):
        profile = build_profile(settings, http, llm)
    progress("profile", "ready_check", status=(profile or {}).get("status"), generated_by=(profile or {}).get("generated_by"))
    if profile.get("status") != "ready":
        raise RuntimeError(
            f"profile {settings.profile_id} is not ready: "
            f"{profile.get('blocking_issues') or profile.get('status')}"
        )
    query_sets = compile_query_sets(profile)
    profile["query_sets"] = query_sets
    plan = build_query_plan(profile, max_groups=120)
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

    if demo:
        raw_papers, raw_news = _demo_records()
        source_audit.add(source="Demo scholarly", status="success", records=len(raw_papers))
        source_audit.add(source="Demo news", status="success", records=len(raw_news))
    else:
        raw_papers: list[dict[str, Any]] = []
        scholarly_calls = [
            ("PubMed", lambda: search_pubmed(
                http, query_sets.get("pubmed_core") or [], start, scholarly_search_end,
                secrets.get("NCBI_API_KEY", ""),
                per_query=settings.pubmed_per_query,
                max_total=settings.pubmed_total_limit,
                audit=source_audit,
            )),
            ("Europe PMC", lambda: search_europe_pmc(
                http, query_sets.get("europe_pmc_core") or [], start, scholarly_search_end,
                per_query=settings.europe_pmc_per_query, audit=source_audit,
            )),
            ("Crossref", lambda: search_crossref(
                http, query_sets.get("crossref_core") or [], start, scholarly_search_end,
                secrets.get("CROSSREF_MAILTO", ""),
                per_query=settings.crossref_per_query,
                include_indexed=settings.crossref_include_indexed,
                audit=source_audit,
            )),
            ("Semantic Scholar", lambda: search_semantic_scholar(
                http, query_sets.get("semantic_scholar_core") or [], start, scholarly_search_end,
                secrets.get("SEMANTIC_SCHOLAR_API_KEY", ""),
                per_query=settings.semantic_per_query,
                anonymous_query_limit=settings.semantic_anonymous_query_limit,
                anonymous_delay_ms=settings.semantic_anonymous_delay_ms,
                audit=source_audit,
            )),
            ("OpenAlex", lambda: search_openalex(
                http, [], query_sets.get("openalex_core") or [], start, scholarly_search_end,
                secrets.get("OPENALEX_API_KEY", ""),
                per_query=settings.openalex_per_query, audit=source_audit,
            )),
            ("bioRxiv/medRxiv", lambda: search_biorxiv_medrxiv(http, start, scholarly_search_end, audit=source_audit)),
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
                http, query_sets.get("pubmed_core") or [], probe_start, end,
                secrets.get("NCBI_API_KEY", ""), audit=source_audit,
            )
            probe_europe_pmc_anchor_counts(
                http, query_sets.get("europe_pmc_core") or [], probe_start, end, audit=source_audit,
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
    papers = rank_papers(papers)
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

    # No network content completion occurs before relevance review and Top-N
    # selection. API-provided abstracts/excerpts and query provenance are the
    # only evidence at this stage.
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
    paper_review_population = list(papers)
    news_review_population = list(news)
    relevance_review_cache = state.setdefault("relevance_review_cache", {}) if settings.relevance_review_cache_enabled else {}
    progress("relevance_review", "start", kind="paper", candidates=len(papers), mode=settings.llm_review_mode)
    papers = final_filter(
        papers,
        profile,
        llm,
        kind="paper",
        review_cache=relevance_review_cache,
        review_mode=settings.llm_review_mode,
        compact_batch_tokens=settings.llm_compact_batch_tokens,
        escalation_batch_tokens=settings.llm_escalation_batch_tokens,
    )
    progress("relevance_review", "complete", kind="paper", accepted=len(papers))
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
    )
    progress("relevance_review", "complete", kind="news", accepted=len(news))
    if len(relevance_review_cache) > 12000:
        for stale_key in list(relevance_review_cache)[: len(relevance_review_cache) - 12000]:
            relevance_review_cache.pop(stale_key, None)
    papers_after_final_gate = len(papers)
    news_after_final_gate = len(news)
    relevance_review_summary = {
        "policy_version": "v7-python-first-ambiguous-llm-1",
        "mode": settings.llm_review_mode,
        "compact_batch_tokens": settings.llm_compact_batch_tokens,
        "escalation_batch_tokens": settings.llm_escalation_batch_tokens,
        "papers": _review_summary(paper_review_population, papers_after_final_gate),
        "news": _review_summary(news_review_population, news_after_final_gate),
    }
    news, papers = attach_news_to_papers(news, papers)

    # Rank all accepted metadata first. Enrichment is delayed until this stage.
    # A small bounded replacement buffer is included so records with no usable
    # abstract/full text or no extractable news body do not leave avoidable gaps
    # in the final Top-N. The buffer is never used for broad corpus enrichment.
    paper_queue = rank_papers(papers)
    news_queue = rank_news(news)
    paper_queue_cap = min(
        len(paper_queue),
        settings.max_fulltexts,
        settings.max_papers + max(0, settings.display_candidate_buffer),
    )
    news_queue_cap = min(
        len(news_queue),
        settings.max_news_fetches,
        settings.max_news + max(0, settings.display_candidate_buffer),
    )
    papers = paper_queue[:paper_queue_cap]
    news = news_queue[:news_queue_cap]
    paper_enrichment_selected = len(papers)
    news_enrichment_selected = len(news)
    progress(
        "display_selection", "complete",
        paper_queue=len(paper_queue), news_queue=len(news_queue),
        paper_enrichment_candidates=paper_enrichment_selected,
        news_enrichment_candidates=news_enrichment_selected,
        max_papers=settings.max_papers, max_news=settings.max_news,
        replacement_buffer=settings.display_candidate_buffer,
    )

    paper_content_rejected = 0
    news_content_rejected = 0
    news_resolver_rejected_records: list[dict[str, Any]] = []
    if not demo:
        oa_email = secrets.get("UNPAYWALL_EMAIL") or secrets.get("CROSSREF_MAILTO", "")
        paper_targets = list(papers)
        progress("display_content_enrichment", "start", kind="paper", selected=len(paper_targets), cap=settings.max_fulltexts)
        deep_enriched = _parallel_map(
            paper_targets,
            lambda item: enrich_scholarly_work(http, item, oa_email),
            workers=5,
        )
        deep_by_id = {item.get("paper_id"): item for item in deep_enriched}
        papers = [deep_by_id.get(item.get("paper_id"), item) for item in papers]
        paper_content_rejected = sum(
            1 for item in papers
            if not clean_space(item.get("abstract") or item.get("full_text"))
        )
        papers = [
            item for item in papers
            if clean_space(item.get("abstract") or item.get("full_text"))
        ]
        progress(
            "display_content_enrichment", "complete", kind="paper",
            enriched=len(deep_enriched), retained=len(papers), rejected_no_evidence=paper_content_rejected,
        )

        news_targets = list(news)
        progress("display_content_enrichment", "start", kind="news", selected=len(news_targets), cap=settings.max_news_fetches)
        news_workers = max(1, min(6, int(os.getenv("PIF_NEWS_ENRICH_WORKERS", "4"))))
        fetched_news = _parallel_map(
            news_targets,
            lambda item: resolve_and_extract_news(http, item, news_profile),
            workers=news_workers,
        )
        fetched_by_id = {item.get("news_id"): item for item in fetched_news}
        news = [fetched_by_id.get(item.get("news_id"), item) for item in news]
        news_resolver_rejected_records = [
            {
                "news_id": item.get("news_id"),
                "title": item.get("title"),
                "source": item.get("source"),
                "url": item.get("url"),
                "resolved_url": item.get("resolved_url"),
                "content_status": item.get("content_status"),
                "content_identity": item.get("content_identity"),
                "content_audit": item.get("content_audit"),
            }
            for item in news
            if item.get("content_status") not in {"full", "partial", "syndicated_summary"} or not clean_space(item.get("content"))
        ]
        news_content_rejected = len(news_resolver_rejected_records)
        news = [
            item for item in news
            if item.get("content_status") in {"full", "partial", "syndicated_summary"} and clean_space(item.get("content"))
        ]
        progress(
            "display_content_enrichment", "complete", kind="news",
            enriched=len(fetched_news), retained=len(news), rejected_no_body=news_content_rejected,
        )

    # Shared URL/body circuit breaker runs after parallel extraction, when the
    # complete set can reveal a common error page reused by unrelated headlines.
    news, news_circuit_summary = apply_news_content_circuit_breaker(news)

    # The post-enrichment assessment is a hard gate, not a passive audit. News
    # is evaluated from the body alone so the RSS title cannot rescue unrelated
    # navigation or standards text. Papers are also dropped when their enriched
    # evidence is explicitly irrelevant.
    papers, paper_post_enrichment_summary = filter_post_enrichment(papers, profile, "paper")
    news, news_post_enrichment_summary = filter_post_enrichment(news, news_profile, "news")
    paper_post_enrichment_rejected = paper_post_enrichment_summary["rejected"]
    news_post_enrichment_rejected = news_post_enrichment_summary["rejected"]
    news_circuit_rejected = news_circuit_summary["rejected"]
    news_content_gate_summary = {
        "policy_version": "v11-news-content-hard-gates-1",
        "resolver": {
            "input": locals().get("news_enrichment_selected", len(news)),
            "rejected": news_content_rejected,
            "rejected_records": news_resolver_rejected_records,
        },
        "circuit_breaker": news_circuit_summary,
        "post_enrichment": news_post_enrichment_summary,
    }
    progress(
        "post_enrichment_gate", "complete",
        papers_retained=len(papers), papers_rejected=paper_post_enrichment_rejected,
        news_retained=len(news), news_resolver_rejected=news_content_rejected,
        news_circuit_rejected=news_circuit_rejected,
        news_relevance_rejected=news_post_enrichment_rejected,
    )

    # Keep the bounded replacement pool through analysis and translation.
    # Final Top-N selection occurs only after translation/content gates so
    # failed translations cannot reduce a healthy result set.
    papers = rank_papers(papers)
    news = rank_news(news)

    # Token-aware analysis tiers are assigned after final relevance/content ranking.
    # L1 uses abstract only; L2 uses a locally selected <= evidence-char budget;
    # L3 is the small top subset independently checked by a second provider.
    for paper_index, paper in enumerate(papers, start=1):
        if paper_index <= max(0, settings.analysis_crosscheck_top_n):
            paper["analysis_level"] = "L3_cross_provider_verified"
        elif paper_index <= max(0, settings.analysis_fulltext_top_n):
            paper["analysis_level"] = "L2_retrieved_fulltext_evidence"
        else:
            paper["analysis_level"] = "L1_abstract_only"

    prompts_dir = settings.project_root / "prompts"
    analysis_cache = state.setdefault("analysis_cache", {})

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
        policy = ANALYSIS_POLICY_VERSION
        return f"{kind}:{policy}:{sha256_text(identity + '|' + evidence_material)}"

    progress("deep_analysis", "start", papers=len(papers), news=len(news))
    for paper_index, paper in enumerate(papers, start=1):
        if paper_index == 1 or paper_index % 5 == 0 or paper_index == len(papers):
            progress("deep_analysis", "paper_progress", completed=paper_index-1, total=len(papers))
        key = analysis_cache_key("paper", paper)
        cached = analysis_cache.get(key) if settings.analysis_cache_enabled else None
        cached_analysis = (cached or {}).get("analysis") if isinstance(cached, dict) else None
        if isinstance(cached_analysis, dict) and (
            not settings.analysis_cache_success_only or cached_analysis.get("status") == "passed"
        ):
            paper["analysis"] = cached_analysis
            paper["paper_type"] = cached.get("paper_type") or paper.get("paper_type") or "research"
            paper["analysis_cache"] = "hit"
        else:
            analyze_paper(paper, llm, prompts_dir)
            paper["analysis_cache"] = "miss"
            if settings.analysis_cache_enabled and (
                not settings.analysis_cache_success_only or (paper.get("analysis") or {}).get("status") == "passed"
            ):
                analysis_cache[key] = {"analysis": paper.get("analysis") or {}, "paper_type": paper.get("paper_type")}

    for news_index, article in enumerate(news, start=1):
        if news_index == 1 or news_index % 5 == 0 or news_index == len(news):
            progress("deep_analysis", "news_progress", completed=news_index-1, total=len(news))
        key = analysis_cache_key("news", article)
        cached = analysis_cache.get(key) if settings.analysis_cache_enabled else None
        cached_analysis = (cached or {}).get("analysis") if isinstance(cached, dict) else None
        if isinstance(cached_analysis, dict) and (
            not settings.analysis_cache_success_only or cached_analysis.get("status") == "passed"
        ):
            article["analysis"] = cached_analysis
            article["analysis_cache"] = "hit"
        else:
            analyze_news(article, llm, prompts_dir)
            article["analysis_cache"] = "miss"
            if settings.analysis_cache_enabled and (
                not settings.analysis_cache_success_only or (article.get("analysis") or {}).get("status") == "passed"
            ):
                analysis_cache[key] = {"analysis": article.get("analysis") or {}}

    progress("deep_analysis", "complete", papers=len(papers), news=len(news))
    analysis_quality_pool = summarize_analysis_quality(
        papers,
        news,
        warning_ratio=settings.analysis_fallback_warning_ratio,
        critical_ratio=settings.analysis_fallback_critical_ratio,
        preflight=llm_preflight,
        scope="candidate_pool",
    )
    if analysis_quality_pool.get("severity") in {"warning", "critical", "unavailable"}:
        progress(
            "analysis_quality",
            "degraded",
            severity=analysis_quality_pool.get("severity"),
            message=analysis_quality_pool.get("message_zh"),
            fallback_ratio=(analysis_quality_pool.get("combined") or {}).get("fallback_ratio"),
            top_failures=analysis_quality_pool.get("top_failure_categories"),
        )
        if os.getenv("GITHUB_ACTIONS", "").lower() == "true":
            print(f"::warning title=Structured analysis degraded::{analysis_quality_pool.get('message_zh')}", flush=True)
    # Keep the persistent state bounded across long-running weekly cycles.
    if len(analysis_cache) > 5000:
        for stale_key in list(analysis_cache)[: len(analysis_cache) - 5000]:
            analysis_cache.pop(stale_key, None)

    translation_cache = state.setdefault("translation_cache", {})
    if demo:
        demo_titles = {
            "Serologic evidence of hantavirus exposure in forest workers": "林业工作者汉坦病毒暴露的血清学证据",
            "Hantavirus infections in a changing world: a narrative review": "变化世界中的汉坦病毒感染：叙述性综述",
            "Health authority reports a suspected hantavirus case": "卫生部门报告一例疑似汉坦病毒病例",
        }
        demo_research = {
            "research_question_and_background": "林业工作者经常接触啮齿动物，研究评估其未识别的汉坦病毒暴露风险。",
            "study_design_and_population": "采用横断面设计，纳入371名林业工作者。",
            "methods": "检测汉坦病毒抗体，并结合职业性啮齿动物接触史评估相关因素。",
            "main_results": "7.3%的参与者检出汉坦病毒IgG；频繁接触啮齿动物与血清阳性相关。",
            "interpretation_and_novelty": "结果提示职业暴露可能是该人群血清阳性的相关因素，但不能据此推断因果关系。",
            "scientific_and_public_health_significance": "研究支持在高暴露职业人群中加强啮齿动物接触监测和针对性防护。",
            "limitations_and_evidence_strength": "研究仅覆盖单一区域且缺少纵向随访，证据强度有限。",
        }
        demo_review = {
            "scope_and_question": "综述环境变化背景下汉坦病毒流行病学、宿主生态、致病机制、诊断和预防。",
            "evidence_base_and_review_method": "现有证据仅表明其为叙述性综述，未报告系统检索流程。",
            "consensus_and_key_conclusions": "环境和人兽接触变化可能重塑汉坦病毒溢出风险，但地区监测能力不均衡。",
            "controversies_and_evidence_gaps": "前瞻性监测、标准化临床研究和获批干预措施仍不足。",
            "research_and_practice_implications": "应推进一体化健康监测、长期宿主生态研究和可比的临床队列研究。",
        }
        demo_news = {
            "time": "本周二报告。",
            "location_and_population": "A县一名疑似患者。",
            "event": "地区卫生部门报告1例疑似汉坦病毒病例，正在进行确证检测。",
            "scale_impact_and_risk": "患者病情稳定，截至报道时未发现新增病例。",
            "response_status_and_uncertainty": "卫生部门提醒居民避免接触啮齿动物排泄物，事件仍待实验室确认。",
        }
        for item in papers:
            item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
            if item.get("paper_type") == "review":
                item["analysis_zh"] = dict(demo_review)
                item["elements_zh"] = dict(demo_review)
                item["elements_en"] = dict((item.get("analysis") or {}).get("analysis") or {})
                body = "该综述总结了环境变化背景下汉坦病毒的流行病学、储存宿主生态、致病机制、诊断和预防研究。现有证据表明，气候、土地利用和人兽接触变化可能重塑病毒溢出风险，但区域监测能力和临床研究质量仍不均衡。"
            else:
                item["analysis_zh"] = dict(demo_research)
                item["elements_zh"] = dict(demo_research)
                item["elements_en"] = dict((item.get("analysis") or {}).get("analysis") or {})
                body = "研究对371名林业工作者开展汉坦病毒抗体检测，并结合职业性啮齿动物接触史评估暴露风险。结果显示7.3%的参与者检出汉坦病毒IgG，频繁接触啮齿动物与血清阳性相关。"
            item["abstract_zh"] = body
            item["summary_zh"] = body
            item["translation_ready"] = True
            item["translation_audit"] = {"policy_version": TRANSLATION_CACHE_VERSION, "ready": True, "title": {"status": "demo"}, "abstract_or_body": {"status": "demo"}, "fields": {}}
        for item in news:
            item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
            item["analysis_zh"] = dict(demo_news)
            item["elements_zh"] = dict(demo_news)
            item["elements_en"] = dict((item.get("analysis") or {}).get("analysis") or {})
            item["content_zh"] = "地区卫生部门报告1例疑似汉坦病毒病例，患者病情稳定，正在进行实验室确认；截至报道时未发现新增病例。"
            item["wechat_summary_zh"] = "时间：本周二；地点与对象：A县一名疑似患者；事件：卫生部门报告1例疑似汉坦病毒病例；影响与风险：患者稳定且未发现新增病例；应对与不确定性：正在确证检测并提示避免接触啮齿动物排泄物。"
            item["summary_zh"] = item["content_zh"]
            item["translation_ready"] = True
            item["translation_audit"] = {"policy_version": TRANSLATION_CACHE_VERSION, "ready": True, "title": {"status": "demo"}, "abstract_or_body": {"status": "demo"}, "fields": {}}
    else:
        progress("translation", "start", papers=len(papers), news=len(news), policy=TRANSLATION_CACHE_VERSION)
        for paper_index, paper in enumerate(papers, start=1):
            if paper_index == 1 or paper_index % 5 == 0 or paper_index == len(papers):
                progress("translation", "paper_progress", completed=paper_index-1, total=len(papers))
            translate_record(
                paper,
                profile=profile,
                llm=llm,
                prompts_dir=prompts_dir,
                cache=translation_cache,
                kind=paper.get("paper_type") or "research",
                wechat_news_max_zh_chars=settings.wechat_news_max_zh_chars,
            )
        for news_index, article in enumerate(news, start=1):
            if news_index == 1 or news_index % 5 == 0 or news_index == len(news):
                progress("translation", "news_progress", completed=news_index-1, total=len(news))
            translate_record(
                article, profile=profile, llm=llm, prompts_dir=prompts_dir,
                cache=translation_cache, kind="news",
                wechat_news_max_zh_chars=settings.wechat_news_max_zh_chars,
            )
        progress("translation", "complete", papers=len(papers), news=len(news))

    translation_rejected_papers = sum(not bool(item.get("translation_ready")) for item in papers)
    translation_rejected_news = sum(not bool(item.get("translation_ready")) for item in news)
    paper_ready_pool = [item for item in papers if item.get("translation_ready")]
    news_ready_pool = [
        item for item in news
        if item.get("translation_ready")
        and len(clean_space(item.get("wechat_summary_zh"))) <= settings.wechat_news_max_zh_chars
    ]
    # Replacement candidates remain available until after translation. Rank the
    # complete ready pool and only now select the final display limits. This
    # directly prevents translation failures in the original Top 50 from
    # shrinking the published literature count when lower-ranked valid records
    # are available.
    ranked_paper_ready_pool = rank_papers(paper_ready_pool)
    ranked_news_ready_pool = rank_news(news_ready_pool)
    for display_rank, item in enumerate(ranked_paper_ready_pool, start=1):
        item["display_rank"] = display_rank
        item["selected_for_display"] = display_rank <= settings.max_papers
    for display_rank, item in enumerate(ranked_news_ready_pool, start=1):
        item["display_rank"] = display_rank
        item["selected_for_display"] = display_rank <= settings.max_news
    papers = ranked_paper_ready_pool[: settings.max_papers]
    news = ranked_news_ready_pool[: settings.max_news]
    paper_top_n_excluded = max(0, len(ranked_paper_ready_pool) - len(papers))
    news_top_n_excluded = max(0, len(ranked_news_ready_pool) - len(news))
    progress(
        "translation_gate", "complete",
        paper_ready_pool=len(paper_ready_pool), papers_retained=len(papers), papers_rejected=translation_rejected_papers,
        paper_top_n_limit=settings.max_papers, paper_top_n_excluded=paper_top_n_excluded,
        news_ready_pool=len(news_ready_pool), news_retained=len(news), news_rejected=translation_rejected_news,
        news_top_n_limit=settings.max_news, news_top_n_excluded=news_top_n_excluded,
        paper_target=settings.max_papers, news_target=settings.max_news,
        selection_policy="priority_evidence_recency_source_quality",
    )

    def has_real_title_translation(item: dict[str, Any]) -> bool:
        title = clean_space(item.get("title_zh"))
        if not title or "翻译暂不可用" in title or "中文标题暂不可用" in title:
            return False
        status = clean_space(((item.get("translation_audit") or {}).get("title") or {}).get("status"))
        return status not in {"translation_unavailable", "empty_source"}

    translated = sum(1 for item in papers + news if has_real_title_translation(item))
    analysis_quality_displayed = summarize_analysis_quality(
        papers,
        news,
        warning_ratio=settings.analysis_fallback_warning_ratio,
        critical_ratio=settings.analysis_fallback_critical_ratio,
        preflight=llm_preflight,
        scope="displayed",
    )
    # The run-level alert is based on the complete analyzed candidate pool, not
    # only the final translated Top-N. This prevents downstream filtering from
    # hiding a broad multi-provider LLM outage. Keep the public issue summary compact;
    # full per-record attempts remain in data/audit/analysis_quality.json.
    analysis_quality = {
        key: value for key, value in analysis_quality_pool.items()
        if key != "fallback_records"
    }
    analysis_quality["scope"] = "run"
    analysis_quality["displayed"] = {
        key: value for key, value in analysis_quality_displayed.items()
        if key not in {"fallback_records", "preflight"}
    }
    issue_date = end.isoformat()
    overview = build_overviews(
        profile, papers, news, llm, prompts_dir,
        minimum=settings.overview_min_items,
        maximum=settings.overview_max_items,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
    )
    source_status = source_audit.summary()
    anchor_coverage = _anchor_coverage(profile, query_sets, source_audit.entries)
    retrieval_funnel = {
        "papers": {
            "raw": raw_papers_before_window,
            "after_window": papers_after_window,
            "after_type_gate": papers_after_type_gate,
            "type_gate_rejected": scholarly_record_type_gate_summary.get("rejected", 0),
            "after_candidate_gate": papers_after_candidate_gate,
            "after_dedup": papers_after_dedup,
            "before_final_gate": papers_before_final_gate,
            "after_final_gate": papers_after_final_gate,
            "selected_for_content_enrichment": locals().get("paper_enrichment_selected", len(papers)),
            "content_rejected": locals().get("paper_content_rejected", 0),
            "post_enrichment_rejected": locals().get("paper_post_enrichment_rejected", 0),
            "translation_rejected": translation_rejected_papers,
            "ready_before_top_n": len(ranked_paper_ready_pool),
            "top_n_limit": settings.max_papers,
            "top_n_excluded": paper_top_n_excluded,
            "selection_policy": "priority_evidence_recency_source_quality",
            "displayed": len(papers),
        },
        "news": {
            "raw": raw_news_before_window,
            "after_window": news_after_window,
            "after_candidate_gate": news_after_candidate_gate,
            "after_dedup": news_after_dedup,
            "before_final_gate": news_before_final_gate,
            "after_final_gate": news_after_final_gate,
            "selected_for_content_enrichment": locals().get("news_enrichment_selected", len(news)),
            "content_rejected": locals().get("news_content_rejected", 0),
            "content_circuit_rejected": locals().get("news_circuit_rejected", 0),
            "post_enrichment_rejected": locals().get("news_post_enrichment_rejected", 0),
            "translation_rejected": translation_rejected_news,
            "ready_before_top_n": len(ranked_news_ready_pool),
            "top_n_limit": settings.max_news,
            "top_n_excluded": news_top_n_excluded,
            "selection_policy": "priority_evidence_recency_source_quality",
            "displayed": len(news),
        },
    }

    issue = {
        "schema_version": "5.0",
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
        "query_plan": plan,
        "source_status": source_status,
        "anchor_coverage": anchor_coverage,
        "relevance_review": relevance_review_summary,
        "analysis_quality": analysis_quality,
        "llm_usage": llm.usage_snapshot(),
        "retrieval_funnel": retrieval_funnel,
        "publication_date_gate": {
            key: value for key, value in paper_date_gate_summary.items()
            if key != "rejected_records"
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
        "news": news,
        "metrics": {
            "raw_papers": raw_papers_before_window,
            "papers": len(papers),
            "research": sum(1 for p in papers if p.get("paper_type") == "research"),
            "reviews": sum(1 for p in papers if p.get("paper_type") == "review"),
            "raw_news": raw_news_before_window,
            "news": len(news),
            "translated": translated,
            "analysis_passed": (analysis_quality_displayed.get("combined") or {}).get("passed", 0),
            "analysis_fallback": (analysis_quality_displayed.get("combined") or {}).get("fallback", 0),
            "analysis_fallback_ratio": (analysis_quality_displayed.get("combined") or {}).get("fallback_ratio", 0.0),
            "paper_ready_before_top_n": len(ranked_paper_ready_pool),
            "paper_top_n_limit": settings.max_papers,
            "paper_top_n_excluded": paper_top_n_excluded,
            "news_ready_before_top_n": len(ranked_news_ready_pool),
            "news_top_n_limit": settings.max_news,
            "news_top_n_excluded": news_top_n_excluded,
        },
    }
    write_issue(settings.output_dir, issue)
    cover_meta = ensure_profile_cover(settings, profile, issue_date)
    issue["cover"] = cover_meta
    write_issue(settings.output_dir, issue)
    render_site(issue, settings.output_dir)
    render_wechat_package(issue, settings.output_dir, cover_meta)
    audit_dir = settings.output_dir / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    dump_json(audit_dir / "query_plan.json", plan)
    dump_json(audit_dir / "profile.json", profile)
    dump_json(audit_dir / "source_status.json", source_status)
    dump_json(audit_dir / "anchor_coverage.json", anchor_coverage)
    dump_json(audit_dir / "relevance_review.json", relevance_review_summary)
    dump_json(audit_dir / "analysis_quality.json", {
        "run": analysis_quality_pool,
        "displayed": analysis_quality_displayed,
    })
    dump_json(audit_dir / "llm_provider_usage.json", llm.usage_snapshot())
    dump_json(audit_dir / "retrieval_funnel.json", retrieval_funnel)
    dump_json(audit_dir / "publication_date_gate.json", paper_date_gate_summary)
    dump_json(audit_dir / "scholarly_record_type_gate.json", scholarly_record_type_gate_summary)
    dump_json(audit_dir / "event_query_expansion.json", event_query_plan)
    dump_json(audit_dir / "news_content_gate.json", news_content_gate_summary)
    dump_json(audit_dir / "paper_post_enrichment_gate.json", paper_post_enrichment_summary)
    dump_json(audit_dir / "display_selection.json", {
        "policy_version": "v13-transparent-top-n-1",
        "selection_policy": "priority_evidence_recency_source_quality",
        "papers": {
            "ready_before_top_n": len(ranked_paper_ready_pool),
            "top_n_limit": settings.max_papers,
            "displayed": len(papers),
            "excluded_by_top_n": paper_top_n_excluded,
            "selected_ids": [item.get("paper_id") for item in papers],
            "excluded_ids": [item.get("paper_id") for item in ranked_paper_ready_pool[settings.max_papers:]],
        },
        "news": {
            "ready_before_top_n": len(ranked_news_ready_pool),
            "top_n_limit": settings.max_news,
            "displayed": len(news),
            "excluded_by_top_n": news_top_n_excluded,
            "selected_ids": [item.get("news_id") for item in news],
            "excluded_ids": [item.get("news_id") for item in ranked_news_ready_pool[settings.max_news:]],
        },
    })
    jsonl_paths = [
        audit_dir / "papers.jsonl",
        audit_dir / "news.jsonl",
        audit_dir / "eligible_papers.jsonl",
        audit_dir / "eligible_news.jsonl",
    ]
    for jsonl_path in jsonl_paths:
        jsonl_path.unlink(missing_ok=True)
    for candidate in ranked_paper_ready_pool:
        append_jsonl(audit_dir / "eligible_papers.jsonl", {
            "paper_id": candidate.get("paper_id"),
            "doi": candidate.get("doi"),
            "title": candidate.get("title"),
            "availability_date": candidate.get("availability_date"),
            "priority_tier": candidate.get("priority_tier"),
            "quality_score": candidate.get("quality_score"),
            "quality_reasons": candidate.get("quality_reasons"),
            "display_rank": candidate.get("display_rank"),
            "selected_for_display": candidate.get("selected_for_display"),
        })
    for candidate in ranked_news_ready_pool:
        append_jsonl(audit_dir / "eligible_news.jsonl", {
            "news_id": candidate.get("news_id"),
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
        })
    for article in news:
        append_jsonl(audit_dir / "news.jsonl", {
            "news_id": article.get("news_id"),
            "title": article.get("title"),
            "analysis": article.get("analysis"),
            "translation_audit": article.get("translation_audit"),
            "content_audit": article.get("content_audit"),
            "content_identity": article.get("content_identity"),
            "relevance_post_enrichment": article.get("relevance_post_enrichment"),
        })
    save_state(settings.state_dir, state)
    progress("pipeline", "complete", profile=settings.profile_id, issue_id=issue.get("issue_id"), metrics=issue.get("metrics"))
    return issue
