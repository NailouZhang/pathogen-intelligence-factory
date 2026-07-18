from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .analysis import analyze_news, analyze_paper
from .bootstrap import _fallback_profile, build_profile
from .config import Settings, load_profile, load_seed
from .content import enrich_scholarly_work, resolve_and_extract_news
from .dates import date_window
from .dedup import attach_news_to_papers, dedup_news, dedup_papers, llm_review_ambiguous_duplicates
from .http import HttpClient
from .llm import LLMRouter
from .news import filter_news_window, search_bing_news, search_gdelt, search_google_news, search_reliefweb, search_who
from .query_plan import build_query_plan, compile_query_sets
from .relevance import candidate_filter_news, candidate_filter_papers, final_filter, relevance_assessment
from .source_status import SourceAudit
from .ranking import rank_news, rank_papers
from .render import render_site, render_wechat_package
from .scholarly import (
    filter_window,
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
from .translation import translate_record
from .overview import build_overview
from .cover import ensure_profile_cover
from .utils import append_jsonl, clean_space, dump_json, sha256_text, utc_now_iso, unique_strings


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


def _anchor_coverage(profile: dict[str, Any], query_sets: dict[str, list[str]], entries: list[dict[str, Any]]) -> dict[str, Any]:
    vocabulary = profile.get("vocabulary") or {}
    identities: list[str] = []
    for key in ("identity_anchor_terms", "member_identity_terms", "disease_identity_terms"):
        for item in vocabulary.get(key) or []:
            if isinstance(item, dict) and item.get("safe_to_use_alone", key == "member_identity_terms"):
                term = clean_space(item.get("term"))
                if term:
                    identities.append(term)
    identities = unique_strings(identities)
    providers = {
        "pubmed": query_sets.get("pubmed_single_anchor_exact") or [],
        "europe_pmc": query_sets.get("europe_pmc_single_anchor_exact") or [],
        "crossref": query_sets.get("crossref") or [],
        "semantic_scholar": query_sets.get("semantic_scholar") or [],
        "openalex_exact": query_sets.get("openalex_exact") or [],
        "news_en": query_sets.get("general_news_single_en") or [],
    }
    rows: list[dict[str, Any]] = []
    for term in identities:
        item: dict[str, Any] = {"identity": term, "providers": {}}
        low = term.casefold()
        for provider, queries in providers.items():
            matching_queries = [q for q in queries if low in clean_space(q).casefold()]
            matching_entries = [
                entry for entry in entries
                if any(clean_space(entry.get("query")) == clean_space(q) for q in matching_queries)
            ]
            item["providers"][provider] = {
                "queries_planned": len(matching_queries),
                "queries_executed": len(matching_entries),
                "records_reported": sum(int(x.get("records") or 0) for x in matching_entries),
                "failed_queries": sum(x.get("status") == "failed" for x in matching_entries),
            }
        rows.append(item)
    return {
        "profile_id": profile.get("profile_id"),
        "identity_count": len(rows),
        "identities": rows,
    }


def run_pipeline(settings: Settings, *, demo: bool = False) -> dict[str, Any]:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    http = HttpClient(settings.user_agent)
    secrets = settings.secrets
    llm = LLMRouter(http, gemini_key=secrets.get("GEMINI_API_KEY", ""), groq_key=secrets.get("GROQ_API_KEY", ""))
    state = load_state(settings.state_dir)
    profile = load_profile(settings)
    seed = load_seed(settings.project_root, settings.profile_id)
    seed_hash = sha256_text(__import__("json").dumps(seed, ensure_ascii=False, sort_keys=True))
    profile_stale = not profile or profile.get("seed_hash") != seed_hash
    if demo and (not profile or profile_stale):
        profile = _fallback_profile(seed, [])
        profile["seed_hash"] = seed_hash
    elif settings.refresh_profile or profile_stale or (profile and profile.get("generated_by") == "bundled_seed"):
        profile = build_profile(settings, http, llm)
    if profile.get("status") != "ready":
        raise RuntimeError(
            f"profile {settings.profile_id} is not ready: "
            f"{profile.get('blocking_issues') or profile.get('status')}"
        )
    query_sets = profile.get("query_sets") or compile_query_sets(profile)
    plan = build_query_plan(profile, max_groups=240)
    start, end = date_window(settings.window_days, timezone_name=settings.timezone)
    source_audit = SourceAudit()

    if demo:
        raw_papers, raw_news = _demo_records()
        source_audit.add(source="Demo scholarly", status="success", records=len(raw_papers))
        source_audit.add(source="Demo news", status="success", records=len(raw_news))
    else:
        raw_papers: list[dict[str, Any]] = []
        scholarly_calls = [
            lambda: search_pubmed(
                http,
                unique_strings(
                    (query_sets.get("pubmed_single_anchor_exact") or [])
                    + (query_sets.get("pubmed_single_qualified") or [])
                    + (query_sets.get("pubmed_core_high_precision") or [])
                    + (query_sets.get("pubmed_core_high_recall") or [])
                    + (query_sets.get("pubmed_identity_fallback") or [])
                    + (query_sets.get("pubmed_molecular") or [])
                    + (query_sets.get("pubmed_epidemiology") or [])
                    + (query_sets.get("pubmed_clinical") or [])
                ),
                start,
                end,
                secrets.get("NCBI_API_KEY", ""),
                per_query=settings.pubmed_per_query,
                max_total=settings.pubmed_total_limit,
                audit=source_audit,
            ),
            lambda: search_europe_pmc(
                http, unique_strings(
                    (query_sets.get("europe_pmc_single_anchor_exact") or [])
                    + (query_sets.get("europe_pmc_single_qualified") or [])
                    + (query_sets.get("europe_pmc") or [])
                    + (query_sets.get("europe_pmc_identity_fallback") or [])
                ), start, end,
                per_query=settings.europe_pmc_per_query, audit=source_audit,
            ),
            lambda: search_crossref(
                http, query_sets.get("crossref") or [], start, end,
                secrets.get("CROSSREF_MAILTO", ""),
                per_query=settings.crossref_per_query,
                include_indexed=settings.crossref_include_indexed,
                audit=source_audit,
            ),
            lambda: search_semantic_scholar(
                http, query_sets.get("semantic_scholar") or [], start, end,
                secrets.get("SEMANTIC_SCHOLAR_API_KEY", ""),
                per_query=settings.semantic_per_query,
                anonymous_query_limit=settings.semantic_anonymous_query_limit,
                anonymous_delay_ms=settings.semantic_anonymous_delay_ms,
                audit=source_audit,
            ),
            lambda: search_openalex(
                http,
                query_sets.get("openalex_exact") or [],
                query_sets.get("openalex_normal") or [],
                start, end,
                secrets.get("OPENALEX_API_KEY", ""),
                per_query=settings.openalex_per_query, audit=source_audit,
            ),
            lambda: search_biorxiv_medrxiv(http, start, end, audit=source_audit),
        ]
        with ThreadPoolExecutor(max_workers=len(scholarly_calls)) as executor:
            for future in as_completed([executor.submit(call) for call in scholarly_calls]):
                try:
                    raw_papers.extend(future.result())
                except Exception as exc:
                    source_audit.add(source="scholarly orchestration", status="failed", error=exc)

        raw_news: list[dict[str, Any]] = []
        general_news_queries = unique_strings(
            (query_sets.get("general_news_single_en") or [])
            + (query_sets.get("general_news_single_zh") or [])
            + (query_sets.get("general_news_en") or [])
            + (query_sets.get("general_news_zh") or [])
            + (query_sets.get("authoritative_web_queries") or [])
        )
        identity_terms = [
            x.get("term")
            for x in (profile.get("vocabulary") or {}).get("identity_anchor_terms", [])
            if isinstance(x, dict) and x.get("term")
        ]
        news_calls = [
            lambda: search_google_news(http, general_news_queries, start, end, audit=source_audit),
            lambda: search_bing_news(http, general_news_queries, start, end, audit=source_audit),
            lambda: search_gdelt(http, query_sets.get("gdelt") or [], start, end, audit=source_audit),
            lambda: search_reliefweb(
                http, query_sets.get("reliefweb") or [], start, end,
                appname=secrets.get("RELIEFWEB_APPNAME", ""), audit=source_audit,
            ),
            lambda: search_who(http, identity_terms, start, end, audit=source_audit),
        ]
        with ThreadPoolExecutor(max_workers=len(news_calls)) as executor:
            for future in as_completed([executor.submit(call) for call in news_calls]):
                try:
                    raw_news.extend(future.result())
                except Exception as exc:
                    source_audit.add(source="news orchestration", status="failed", error=exc)


        # When both primary biomedical indexes are genuinely empty in the
        # seven-day window, run count-only 90-day single-anchor probes.  Probe
        # hits are diagnostic only and never enter the daily report.  They
        # distinguish a quiet week from a broken profile/query or provider.
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
                http, query_sets.get("pubmed_single_anchor_exact") or [],
                probe_start, end, secrets.get("NCBI_API_KEY", ""), audit=source_audit,
            )
            probe_europe_pmc_anchor_counts(
                http, query_sets.get("europe_pmc_single_anchor_exact") or [],
                probe_start, end, audit=source_audit,
            )

        scholarly_names = {"PubMed", "Europe PMC", "Crossref", "Semantic Scholar", "OpenAlex", "bioRxiv", "medRxiv"}
        scholarly_success = any(
            row.get("source") in scholarly_names and row.get("status") == "success"
            for row in source_audit.entries
        )
        if not scholarly_success:
            raise RuntimeError("All scholarly source adapters failed or were skipped; inspect data/audit/source_status.json and GitHub logs")

    raw_papers_before_window = len(raw_papers)
    raw_papers = filter_window(raw_papers, start, end)
    papers_after_window = len(raw_papers)
    paper_candidates = candidate_filter_papers(raw_papers, profile)
    papers_after_candidate_gate = len(paper_candidates)
    papers = dedup_papers(paper_candidates)
    dedup_prompt = (settings.project_root / "prompts" / "ambiguous_dedup.md").read_text(encoding="utf-8")
    papers = llm_review_ambiguous_duplicates(papers, llm, dedup_prompt)
    papers = rank_papers(papers)
    if settings.max_paper_candidates > 0:
        papers = papers[: settings.max_paper_candidates]

    raw_news_before_window = len(raw_news)
    raw_news = filter_news_window(raw_news, start, end)
    news_after_window = len(raw_news)
    strict_news_candidates = candidate_filter_news(raw_news, profile)
    strict_ids = {id(x) for x in strict_news_candidates}
    # An anchored search result can have a generic title while naming the virus
    # only in the landing-page body. Preserve a bounded prefetch pool so that
    # body extraction happens before final rejection.
    query_anchored_prefetch: list[dict[str, Any]] = []
    for record in raw_news:
        if id(record) in strict_ids:
            continue
        if record.get("retrieval_queries") and record.get("title"):
            record["relevance_decision"] = "query_anchored_prefetch"
            query_anchored_prefetch.append(record)
    news_candidates = strict_news_candidates + query_anchored_prefetch
    news_after_candidate_gate = len(news_candidates)
    news = dedup_news(news_candidates)
    news = llm_review_ambiguous_duplicates(news, llm, dedup_prompt)
    news = rank_news(news)
    if settings.max_news_candidates > 0:
        news = news[: settings.max_news_candidates]

    if demo:
        for paper in papers:
            paper["paper_id"] = paper.get("paper_id") or "paper-" + sha256_text(paper.get("title", ""))[:16]
            paper["evidence_level"] = "E1" if paper.get("abstract") else "E0"
        for article in news:
            article["news_id"] = article.get("news_id") or "news-" + sha256_text(article.get("title", ""))[:16]
            article["content"] = article.get("excerpt")
            article["content_status"] = "partial"
            article["resolved_url"] = article.get("url")
    else:
        # Relevance needs an abstract/body, not necessarily an entire full text.
        # Enrich every candidate whose metadata lacks an abstract.  A positive
        # max_fulltexts remains available as an emergency runtime cap; zero is
        # the normal unlimited mode.
        missing_abstract = [item for item in papers if not clean_space(item.get("abstract"))]
        enrich_targets = missing_abstract
        if settings.max_fulltexts > 0:
            enrich_targets = enrich_targets[: settings.max_fulltexts]
        enriched = _parallel_map(
            enrich_targets,
            lambda item: enrich_scholarly_work(http, item, secrets.get("CROSSREF_MAILTO", "")),
            workers=6,
        )
        enriched_by_id = {item.get("paper_id"): item for item in enriched}
        updated_papers: list[dict[str, Any]] = []
        for item in papers:
            if item.get("paper_id") in enriched_by_id:
                updated_papers.append(enriched_by_id[item.get("paper_id")])
            else:
                item["evidence_level"] = "E1" if item.get("abstract") else "E0"
                item["full_text_method"] = "abstract_sufficient" if item.get("abstract") else "metadata_only"
                updated_papers.append(item)
        papers = updated_papers

        news_fetch_targets = news if settings.max_news_fetches <= 0 else news[: settings.max_news_fetches]
        fetched_news = _parallel_map(
            news_fetch_targets,
            lambda item: resolve_and_extract_news(http, item),
            workers=8,
        )
        fetched_ids = {id(item) for item in news_fetch_targets}
        news = fetched_news + [item for item in news if id(item) not in fetched_ids]

    papers_before_final_gate = len(papers)
    news_before_final_gate = len(news)
    paper_review_population = list(papers)
    news_review_population = list(news)
    relevance_review_cache = state.setdefault("relevance_review_cache", {}) if settings.relevance_review_cache_enabled else {}
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
    news = final_filter(
        news,
        profile,
        llm,
        kind="news",
        review_cache=relevance_review_cache,
        review_mode=settings.llm_review_mode,
        compact_batch_tokens=settings.llm_compact_batch_tokens,
        escalation_batch_tokens=settings.llm_escalation_batch_tokens,
    )
    if len(relevance_review_cache) > 12000:
        for stale_key in list(relevance_review_cache)[: len(relevance_review_cache) - 12000]:
            relevance_review_cache.pop(stale_key, None)
    papers_after_final_gate = len(papers)
    news_after_final_gate = len(news)
    relevance_review_summary = {
        "policy_version": "v6-compact-all-1",
        "mode": settings.llm_review_mode,
        "compact_batch_tokens": settings.llm_compact_batch_tokens,
        "escalation_batch_tokens": settings.llm_escalation_batch_tokens,
        "papers": _review_summary(paper_review_population, papers_after_final_gate),
        "news": _review_summary(news_review_population, news_after_final_gate),
    }
    news, papers = attach_news_to_papers(news, papers)

    # Final ranking is applied after metadata/full-text enrichment so that
    # study design, evidence completeness and official-source authority can
    # influence the displayed top 50. If fewer records exist, all are shown.
    papers = rank_papers(papers)[: settings.max_papers]
    news = rank_news(news)[: settings.max_news]

    # Full-text work is reserved for displayed papers after relevance and
    # quality ranking.  This keeps LLM/deep-fetch cost focused on what appears
    # on Pages and in WeChat while every candidate has already received compact
    # relevance review.
    if not demo:
        deep_targets = [item for item in papers if item.get("evidence_level") != "E2"]
        deep_enriched = _parallel_map(
            deep_targets,
            lambda item: enrich_scholarly_work(http, item, secrets.get("CROSSREF_MAILTO", "")),
            workers=5,
        )
        deep_by_id = {item.get("paper_id"): item for item in deep_enriched}
        papers = [deep_by_id.get(item.get("paper_id"), item) for item in papers]

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
        evidence = clean_space(
            item.get("full_text")
            or item.get("abstract")
            or item.get("content")
            or item.get("excerpt")
        )
        return f"{kind}:{sha256_text(identity + '|' + evidence)}"

    for paper in papers:
        key = analysis_cache_key("paper", paper)
        cached = analysis_cache.get(key) if settings.analysis_cache_enabled else None
        if isinstance(cached, dict):
            paper["analysis"] = cached.get("analysis") or {}
            paper["paper_type"] = cached.get("paper_type") or paper.get("paper_type") or "research"
            paper["analysis_cache"] = "hit"
        else:
            analyze_paper(paper, llm, prompts_dir)
            paper["analysis_cache"] = "miss"
            if settings.analysis_cache_enabled:
                analysis_cache[key] = {"analysis": paper.get("analysis") or {}, "paper_type": paper.get("paper_type")}

    for article in news:
        key = analysis_cache_key("news", article)
        cached = analysis_cache.get(key) if settings.analysis_cache_enabled else None
        if isinstance(cached, dict):
            article["analysis"] = cached.get("analysis") or {}
            article["analysis_cache"] = "hit"
        else:
            analyze_news(article, llm, prompts_dir)
            article["analysis_cache"] = "miss"
            if settings.analysis_cache_enabled:
                analysis_cache[key] = {"analysis": article.get("analysis") or {}}

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
        demo_analysis = {
            "Serologic evidence of hantavirus exposure in forest workers": {
                "background": "林业工作者经常接触啮齿动物，可能存在未识别的汉坦病毒暴露风险。",
                "methods": "采用横断面设计，对371名林业工作者检测汉坦病毒抗体并评估啮齿动物接触史。",
                "results": "7.3%的参与者检出汉坦病毒IgG；频繁接触啮齿动物与血清阳性相关。",
                "contribution": "结果提示职业人群应加强啮齿动物暴露监测和针对性防护。",
                "limitations": "研究仅覆盖单一区域，且缺少纵向随访，不能直接推断感染时间和因果关系。",
            },
            "Hantavirus infections in a changing world: a narrative review": {
                "background": "环境变化与人兽接触增加正在改变汉坦病毒的传播和溢出风险。",
                "main_directions": "综述流行病学、储存宿主生态、致病机制、诊断和预防等主要方向。",
                "current_state": "现有证据支持环境与宿主生态变化会影响人群暴露，但不同地区的监测能力不均。",
                "gaps": "前瞻性监测、标准化临床研究和获批干预措施仍然不足。",
                "future_research": "应推进一体化健康监测、长期宿主生态研究和可比的临床队列研究。",
            },
            "Health authority reports a suspected hantavirus case": {
                "time": "本周二报告。",
                "location": "A县。",
                "event": "地区卫生部门报告1例疑似汉坦病毒病例，正在进行确证检测。",
                "impact": "患者病情稳定；部门同时提醒居民避免接触啮齿动物排泄物。",
                "status": "截至报道时未发现新增病例，事件仍处于调查和实验室确认阶段。",
            },
        }
        for item in papers + news:
            item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
            analysis = (item.get("analysis") or {}).get("analysis") or {}
            if item in papers and item.get("paper_type") == "review":
                labels = [("背景", "background"), ("主要方向", "main_directions"), ("研究现状", "current_state"), ("不足", "gaps"), ("后续研究", "future_research")]
            elif item in papers:
                labels = [("背景", "background"), ("方法", "methods"), ("结果", "results"), ("贡献", "contribution"), ("局限", "limitations")]
            else:
                labels = [("时间", "time"), ("地点", "location"), ("事件", "event"), ("影响", "impact"), ("状态", "status")]
            zh_source = demo_analysis.get(item.get("title"), {})
            item["analysis_zh"] = {key: clean_space(zh_source.get(key)) or clean_space(analysis.get(key)) or "未报告" for _, key in labels}
            if item in papers:
                demo_body = {
                    "Serologic evidence of hantavirus exposure in forest workers": "研究对371名林业工作者开展汉坦病毒抗体检测，并结合职业性啮齿动物接触史评估暴露风险。结果显示7.3%的参与者检出汉坦病毒IgG，频繁接触啮齿动物与血清阳性相关，提示职业人群需要加强暴露监测和针对性防护。",
                    "Hantavirus infections in a changing world: a narrative review": "该综述总结了环境变化背景下汉坦病毒的流行病学、储存宿主生态、致病机制、诊断和预防研究。现有证据表明，气候、土地利用和人兽接触变化可能重塑病毒溢出风险，但区域监测能力和临床研究质量仍不均衡。",
                }.get(item.get("title"), "原始记录未提供可展示的摘要译文。")
                item["abstract_zh"] = demo_body
                item["summary_zh"] = demo_body
            else:
                demo_body = "地区卫生部门报告1例疑似汉坦病毒病例，患者病情稳定，正在进行实验室确认；截至报道时未发现新增病例。"
                item["content_zh"] = demo_body
                item["summary_zh"] = demo_body
            item["translation_audit"] = {
                "title": {"status": "demo", "provider": "deterministic_demo"},
                "abstract_or_body": {"status": "demo", "provider": "deterministic_demo"},
                "fields": {},
            }
    else:
        for paper in papers:
            translate_record(
                paper,
                profile=profile,
                llm=llm,
                prompts_dir=prompts_dir,
                cache=translation_cache,
                kind=paper.get("paper_type") or "research",
            )
        for article in news:
            translate_record(article, profile=profile, llm=llm, prompts_dir=prompts_dir, cache=translation_cache, kind="news")

    def has_real_title_translation(item: dict[str, Any]) -> bool:
        title = clean_space(item.get("title_zh"))
        if not title or "翻译暂不可用" in title or "中文标题暂不可用" in title:
            return False
        status = clean_space(((item.get("translation_audit") or {}).get("title") or {}).get("status"))
        return status not in {"translation_unavailable", "empty_source"}

    translated = sum(1 for item in papers + news if has_real_title_translation(item))
    issue_date = end.isoformat()
    overview = build_overview(profile, papers, news, llm)
    source_status = source_audit.summary()
    anchor_coverage = _anchor_coverage(profile, query_sets, source_audit.entries)
    retrieval_funnel = {
        "papers": {
            "raw": raw_papers_before_window,
            "after_window": papers_after_window,
            "after_candidate_gate": papers_after_candidate_gate,
            "before_final_gate": papers_before_final_gate,
            "after_final_gate": papers_after_final_gate,
            "displayed": len(papers),
        },
        "news": {
            "raw": raw_news_before_window,
            "after_window": news_after_window,
            "after_candidate_gate": news_after_candidate_gate,
            "before_final_gate": news_before_final_gate,
            "after_final_gate": news_after_final_gate,
            "displayed": len(news),
        },
    }

    issue = {
        "schema_version": "3.0",
        "issue_id": f"{settings.profile_id}-{issue_date}",
        "profile_id": settings.profile_id,
        "issue_date": issue_date,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "generated_at": utc_now_iso(),
        "title_zh": f"{profile.get('display_name_zh') or settings.profile_id}每日情报",
        "title_en": f"{profile.get('display_name_en') or settings.profile_id} Daily Intelligence",
        "profile": profile,
        "query_plan": plan,
        "source_status": source_status,
        "anchor_coverage": anchor_coverage,
        "relevance_review": relevance_review_summary,
        "retrieval_funnel": retrieval_funnel,
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
    dump_json(audit_dir / "retrieval_funnel.json", retrieval_funnel)
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
        })
    save_state(settings.state_dir, state)
    return issue
