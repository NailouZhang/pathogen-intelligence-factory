from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import yaml

from pifactory.analysis import NEWS_FIELDS, analyze_news
from pifactory.config import Settings
from pifactory.dedup import dedup_news
from pifactory.http import HttpClient
from pifactory.llm import LLMRouter
from pifactory.overview import _paper_citation_id
from pifactory.profile_contract import deterministic_profile
from pifactory.render import render_site, render_wechat_package, visible_text_count
from pifactory.runtime_budget import RuntimeBudget
from pifactory.vocabulary_lifecycle import ensure_review_vocabulary, semantic_fingerprints
from scripts.classify_profile_failure import classify

ROOT = Path(__file__).resolve().parents[1]


class UnavailableLLM:
    available = False


class FakeNewsLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def provider_order(self, purpose: str):
        return ("gemini", "groq")

    def json_task(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["prompt"])
        ids = [row["id"] for row in payload["evidence"]]
        analysis = {field: f"Verified {field.replace('_', ' ')} information from the source." for field in NEWS_FIELDS}
        brief = " ".join(["Verified public-health reporting describes the event and its supported context."] * 16)
        data = {
            "analysis": analysis,
            "evidence_ids": {field: [ids[0]] for field in NEWS_FIELDS},
            "brief_en": brief if payload.get("generate_brief") else "",
            "source_assessment": "reputable_media",
            "confidence": "moderate",
        }
        ok, reason = kwargs["validator"](data)
        assert ok, reason
        return SimpleNamespace(data=data, provider="gemini", model="gemini-2.5-flash", attempts=[])


def _settings(tmp_path: Path) -> Settings:
    project = tmp_path / "project"
    (project / "profiles" / "hantavirus").mkdir(parents=True)
    (project / "prompts").mkdir(parents=True)
    source_seed = ROOT / "profiles" / "hantavirus" / "seed.yaml"
    (project / "profiles" / "hantavirus" / "seed.yaml").write_text(source_seed.read_text(encoding="utf-8"), encoding="utf-8")
    (project / "prompts" / "review_vocabulary_v1.md").write_text("test", encoding="utf-8")
    output = tmp_path / "output"
    return Settings(profile_id="hantavirus", project_root=project, output_dir=output, state_dir=output / "data" / "state")


def _paper(index: int, detail: str = "") -> dict:
    return {
        "paper_id": f"paper-{index}",
        "title": f"Hantavirus study {index}",
        "title_zh": f"汉坦病毒研究 {index}",
        "doi": f"10.1000/hanta.{index}",
        "pmid": str(1000 + index),
        "pmcid": f"PMC{1000 + index}",
        "journal": "Journal of Hantavirus",
        "canonical_publication_date": "2026-07-20",
        "abstract": detail or "Verified abstract.",
        "abstract_zh": detail or "经核验的摘要。",
        "paper_type": "research",
        "analysis": {
            "analysis": {"main_results": detail or "经核验的研究结果。", "methods": detail or "经核验的方法。"},
            "summary_en": "Verified summary.",
        },
        "analysis_zh": {"main_results": detail or "经核验的研究结果。", "methods": detail or "经核验的方法。"},
        "sources": ["PubMed"],
    }


def _issue(papers: list[dict], news: list[dict] | None = None) -> dict:
    return {
        "schema_version": "6.2",
        "issue_id": "hantavirus-2026-07-20",
        "profile_id": "hantavirus",
        "issue_date": "2026-07-20",
        "generated_at": "2026-07-20T00:00:00Z",
        "window_start": "2026-07-14",
        "window_end": "2026-07-20",
        "title_zh": "汉坦病毒每周情报",
        "title_en": "Hantavirus Weekly Intelligence",
        "papers": papers,
        "supplementary_papers": [{"paper_id": "supp-1", "title": "Supplemental hantavirus metadata", "title_zh": "汉坦病毒补充元数据", "journal": "Journal", "canonical_publication_date": "2026-07-20", "doi": "10.1000/supp"}],
        "news": news or [],
        "supplementary_news": [{"news_id": "sn-1", "title": "Hantavirus notice", "title_zh": "汉坦病毒简讯", "publisher": "Authority", "published_date": "2026-07-20", "url": "https://example.org/news"}],
        "overview": {"literature": {}, "news": {}},
        "metrics": {"translated": len(papers)},
        "retrieval_funnel": {"papers": {}, "news": {}},
    }


def test_runtime_budget_enforces_stage_limit_and_finalization_reserve() -> None:
    budget = RuntimeBudget(150, 30, {"paper_processing": 90})
    budget.start_stage("paper_processing")
    budget.stages["paper_processing"].started_at_monotonic -= 91 * 60
    allowed, reason = budget.can_start_expensive("paper_processing")
    assert allowed is False
    assert reason == "paper_processing_time_budget_exhausted"

    reserve = RuntimeBudget(150, 30, {"news_analysis": 30})
    reserve.started_at_monotonic = time.monotonic() - 121 * 60
    allowed, reason = reserve.can_start_expensive("news_analysis")
    assert allowed is False
    assert reason == "finalization_reserve_entered"


def test_profile_semantic_fingerprint_ignores_schedule_but_tracks_core_terms() -> None:
    seed = yaml.safe_load((ROOT / "profiles/hantavirus/seed.yaml").read_text(encoding="utf-8"))
    profile = deterministic_profile(seed, [])
    baseline = semantic_fingerprints(seed, profile)
    schedule_changed = copy.deepcopy(seed)
    schedule_changed["schedule"] = {"day": "monday", "order": 1}
    assert semantic_fingerprints(schedule_changed, profile)["profile_semantic_fingerprint"] == baseline["profile_semantic_fingerprint"]

    profile_scope_changed = copy.deepcopy(profile)
    profile_scope_changed["target_scope"]["scope_included"] = ["changed runtime scope"]
    assert semantic_fingerprints(seed, profile_scope_changed)["profile_semantic_fingerprint"] != baseline["profile_semantic_fingerprint"]

    profile_vocab_changed = copy.deepcopy(profile)
    profile_vocab_changed["vocabulary"]["identity_anchor_terms"].append({"term": "new related identity"})
    assert semantic_fingerprints(seed, profile_vocab_changed)["profile_semantic_fingerprint"] != baseline["profile_semantic_fingerprint"]

    changed_seed = copy.deepcopy(seed)
    changed_profile = copy.deepcopy(profile)
    changed_seed["search_strategy"]["concepts"][0]["scholarly"] += " changed"
    changed_profile["search_strategy"]["concepts"][0]["scholarly"] += " changed"
    assert semantic_fingerprints(changed_seed, changed_profile)["profile_semantic_fingerprint"] != baseline["profile_semantic_fingerprint"]


def test_review_vocabulary_builds_once_then_rebuilds_on_semantic_change(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    seed_path = settings.project_root / "profiles/hantavirus/seed.yaml"
    seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    profile = deterministic_profile(seed, [])
    first, audit1 = ensure_review_vocabulary(settings, profile, HttpClient("test"), UnavailableLLM(), demo=False)
    assert audit1["rebuild_required"] is True
    assert audit1["generated_by"].startswith("deterministic_seed_vocabulary")

    second, audit2 = ensure_review_vocabulary(settings, deterministic_profile(seed, []), HttpClient("test"), UnavailableLLM(), demo=False)
    assert audit2["rebuild_required"] is False
    assert second["profile_semantic_fingerprint"] == first["profile_semantic_fingerprint"]

    seed["search_strategy"]["concepts"][0]["scholarly"] += " changed"
    seed_path.write_text(yaml.safe_dump(seed, allow_unicode=True, sort_keys=False), encoding="utf-8")
    changed, audit3 = ensure_review_vocabulary(settings, deterministic_profile(seed, []), HttpClient("test"), UnavailableLLM(), demo=False)
    assert audit3["rebuild_required"] is True
    assert audit3["trigger"] == "profile_semantic_change"
    assert changed["profile_semantic_fingerprint"] != first["profile_semantic_fingerprint"]


def test_all_llm_routes_start_gemini_and_end_groq(monkeypatch) -> None:
    monkeypatch.setenv("PIF_LLM_ALLOW_PAID", "false")
    router = LLMRouter(
        HttpClient("test"),
        gemini_key="x", groq_key="x", openrouter_key="x", mistral_key="x",
        siliconflow_key="x", bigmodel_key="x", deepseek_key="x",
    )
    for purpose in ("extract", "relevance", "rescue", "overview", "translation"):
        order = router.provider_order(purpose)
        assert order[0] == "gemini"
        assert order[-1] == "groq"
    assert router._billing_guard("openrouter", "paid-model") == (False, "openrouter_non_free_model_blocked")


def test_news_analysis_single_call_and_short_source_no_expansion(tmp_path: Path, monkeypatch) -> None:
    prompts = ROOT / "prompts"
    llm = FakeNewsLLM()
    long_article = {"news_id": "n1", "title": "Hantavirus public-health update", "publisher": "Authority", "published_date": "2026-07-20", "content": " ".join(["Verified hantavirus reporting provides public health evidence."] * 90)}
    analyzed = analyze_news(long_article, llm, prompts)
    assert len(llm.calls) == 1
    assert analyzed["analysis"]["brief_generation"] == "llm_from_verified_body"
    assert 100 <= len(analyzed["analysis"]["brief_en"].split()) <= 220

    short_llm = FakeNewsLLM()
    short_text = "Official hantavirus notice with limited verified details."
    short = analyze_news({"news_id": "n2", "title": "Hantavirus notice", "content": short_text}, short_llm, prompts)
    assert len(short_llm.calls) == 1
    assert short["analysis"]["brief_en"] == short_text
    assert short["analysis"]["brief_generation"] == "source_short_evidence_no_llm_expansion"


def test_rss_title_snippet_duplicate_is_retained_as_title_only() -> None:
    rows = dedup_news([{"source": "RSS", "title": "Hantavirus outbreak update", "excerpt": "Hantavirus outbreak update", "url": "https://example.org/a"}])
    assert len(rows) == 1
    assert rows[0]["excerpt"] == ""
    assert rows[0]["snippet_duplicate_of_title"] is True


def test_wechat_budget_compacts_bottom_papers_and_removes_identifiers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIF_WECHAT_MAX_VISIBLE_CHARS", "8000")
    monkeypatch.setenv("PIF_WECHAT_MIN_FULL_PAPERS", "10")
    papers = [_paper(i) for i in range(10)] + [_paper(10, "大段内容" * 1500), _paper(11, "大段内容" * 1500)]
    issue = _issue(papers, news=[{"news_id": "n1", "title": "Hantavirus news", "title_zh": "汉坦病毒新闻", "published_date": "2026-07-20", "publisher": "Authority", "analysis": {"brief_en": "Brief", "analysis": {"event": "Verified event"}}, "analysis_zh": {"event": "经核验事件"}, "brief_zh": "新闻简报"}])
    audit = render_wechat_package(issue, tmp_path, {"cover_sha256": "abc", "generator": "test", "profile_fingerprint": "fp"})
    html = (tmp_path / "wechat-package/article.html").read_text(encoding="utf-8")
    assert audit["within_budget"] is True
    assert audit["compacted_primary_papers"] == 2 or audit["truncated_display_fields"]["paper_abstracts"] >= 2
    assert visible_text_count(html) <= 8000
    assert "DOI" not in html and "PubMed" not in html and "PMCID" not in html
    assert "摘要尚未公开或本条未进入深度主报告" not in html

    render_site(issue, tmp_path)
    page = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert "10.1000/hanta.0" in page
    assert "补充新闻 / Supplementary News" in page


def test_literature_overview_identifier_never_uses_internal_paper_id() -> None:
    assert _paper_citation_id({"paper_id": "paper-internal", "doi": "10.1000/x"}) == "10.1000/x"
    assert _paper_citation_id({"paper_id": "paper-internal", "pmid": "123"}) == "PMID:123"
    assert "paper-" not in _paper_citation_id({"paper_id": "paper-internal", "title": "Verified title"})


def test_failure_classification_only_workload_failures_affect_schedule(tmp_path: Path) -> None:
    timeout_result = classify("starting with 150m profile timeout", tmp_path, 124)
    assert timeout_result["classification"] == "runtime_timeout"
    assert timeout_result["schedule_relevant"] is True
    auth_result = classify("HTTP 401 invalid API key", tmp_path, 1)
    assert auth_result["classification"] == "provider_authentication_failure"
    assert auth_result["schedule_relevant"] is False
