from __future__ import annotations

import json
from pathlib import Path

from pifactory.analysis import _news_validator, _paper_validator
from pifactory.event_query import derive_event_queries, is_scarce_profile, news_relevance_profile
from pifactory.llm import LLMRouter
from pifactory.provider_state import ProviderStateStore
from pifactory.render import render_site
from pifactory.scholarly_gate import assess_scholarly_record, filter_scholarly_records
from pifactory.utils import split_sentences


def test_abbreviation_tail_is_removed_before_sentence_pool() -> None:
    text = (
        "CAMKV reduced rabies virus replication in infected cells. "
        "The intervention delayed disease progression in mice. "
        "Abbreviations: LAMP1: lysosome-associated membrane protein 1; "
        "MTOR: mechanistic target of rapamycin; qPCR: quantitative PCR."
    )
    rows = split_sentences(text)
    joined = " ".join(rows)
    assert "delayed disease progression" in joined
    assert "LAMP1" not in joined
    assert "MTOR" not in joined
    assert "qPCR" not in joined


def test_dataset_and_repository_records_are_rejected() -> None:
    figshare = {
        "title": "Dataset of figures",
        "journal": "Figshare",
        "doi": "10.6084/m9.figshare.32984561.v1",
        "publication_types": ["dataset"],
        "url": "https://figshare.com/articles/dataset/example/1",
    }
    decision = assess_scholarly_record(figshare)
    assert decision["accepted"] is False
    assert "rejected_publication_type" in decision["reasons"]
    assert "rejected_repository_platform" in decision["reasons"]
    accepted, audit = filter_scholarly_records([figshare])
    assert accepted == []
    assert audit["rejected"] == 1


def test_validators_reject_nested_dict_and_list_values() -> None:
    paper_data = {
        "analysis": {
            "research_question_and_background": {"text": "nested"},
            "study_design_and_population": "A prospective cohort of 120 participants was studied.",
            "methods": "Serum samples were tested using a validated immunoassay.",
            "main_results": "Twelve participants had a positive result.",
            "interpretation_and_novelty": "The authors interpreted the result cautiously.",
            "scientific_and_public_health_significance": "The findings may inform surveillance design.",
            "limitations_and_evidence_strength": "The single-centre design limits generalisability.",
        },
        "summary_en": "This is a sufficiently long integrated English summary of the study design, methods, results and limitations for strict validation without relying on nested objects.",
        "evidence_ids": {key: ["A1"] for key in (
            "research_question_and_background", "study_design_and_population", "methods", "main_results",
            "interpretation_and_novelty", "scientific_and_public_health_significance", "limitations_and_evidence_strength",
        )},
        "confidence": "moderate",
    }
    valid, reason = _paper_validator("research", {"A1"})(paper_data)
    assert valid is False
    assert reason["issue_counts"]["field_type_error"] == 1

    news_data = {
        "analysis": {
            "time": {"publication_date": "2026-07-15"},
            "location_and_population": "Hancock County, Ohio, United States; one bat.",
            "event": "A bat tested positive for rabies according to local reporting.",
            "scale_impact_and_risk": "No human cases were reported in the supplied evidence.",
            "response_status_and_uncertainty": "The report did not describe further public-health actions.",
        },
        "brief_en": "A local report stated that a bat in Hancock County, Ohio, tested positive for rabies. The supplied report did not identify human cases or describe a complete public-health response. The event should therefore be treated as a confirmed animal detection with important unresolved details rather than as evidence of human transmission or a broader outbreak.",
        "evidence_ids": {key: ["N1"] for key in (
            "time", "location_and_population", "event", "scale_impact_and_risk", "response_status_and_uncertainty",
        )},
        "source_assessment": "secondary_media",
        "confidence": "low",
    }
    valid, reason = _news_validator({"N1"})(news_data)
    assert valid is False
    assert reason["issue_counts"]["field_type_error"] == 1


def test_doi_landing_is_attempted_without_unpaywall_email(monkeypatch) -> None:
    # Import here so lightweight dependency shims can be used in constrained CI.
    from pifactory.content import enrich_scholarly_work

    class FakeHttp:
        def get_json(self, *args, **kwargs):
            raise AssertionError("Unpaywall must not be called without mailto")

        def request(self, method, url, **kwargs):
            raise RuntimeError(f"offline test: {url}")

    work = {
        "title": "A valid hantavirus paper",
        "doi": "10.1234/example",
        "abstract": "This abstract provides valid evidence about hantavirus infection and surveillance.",
        "authors": ["Jane Doe"],
    }
    result = enrich_scholarly_work(FakeHttp(), work, mailto="")
    attempts = (result.get("content_audit") or {}).get("attempts") or []
    assert any(row.get("method") == "doi_landing" for row in attempts)
    assert not any(row.get("method") == "unpaywall" for row in attempts)


def test_provider_state_is_loaded_by_next_router_process(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "provider_quota_daily.json"
    monkeypatch.setenv("PIF_PROVIDER_STATE_FILE", str(state_file))
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    class FakeHttp:
        pass

    first = LLMRouter(FakeHttp())
    first.states["groq"].mark_failure("model-a", "quota_exhausted")
    first._persist_states()

    second = LLMRouter(FakeHttp())
    assert second.states["groq"].status == "quota_exhausted"
    assert second.states["groq"].available() is False
    raw = json.loads(state_file.read_text(encoding="utf-8"))
    assert raw["providers"]["groq"]["status"] == "quota_exhausted"


def test_marburg_literature_event_expands_news_queries() -> None:
    profile = {
        "profile_id": "marburg_virus",
        "display_name_en": "Marburg virus",
        "post_retrieval_relevance_rules": {
            "identity_anchor_patterns": ["Marburg virus", "Marburg virus disease"],
            "minimum_relevance_score": 6,
            "review_score_min": 3,
        },
    }
    papers = [{
        "paper_id": "p1",
        "title": "Ethiopia 2025 Marburg virus outbreak exposes health-system vulnerabilities",
        "abstract": "The outbreak in Ethiopia had a high case-fatality ratio and required contact tracing.",
    }]
    plan = derive_event_queries(papers, profile, max_queries=4)
    assert plan["queries"]
    query = " ".join(plan["queries"]).lower()
    assert "marburg" in query
    assert "ethiopia" in query
    assert "outbreak" in query
    assert is_scarce_profile("marburg_virus") is True
    adjusted = news_relevance_profile(profile, scarce=True)
    assert adjusted["post_retrieval_relevance_rules"]["minimum_relevance_score"] == 5


def test_public_page_renders_real_english_elements(tmp_path: Path) -> None:
    paper_elements = {
        "research_question_and_background": "The study addressed incomplete genomic surveillance.",
        "study_design_and_population": "A retrospective genomic study included 58 infected people.",
        "methods": "Researchers used sequencing, phylogenetics and variant calling.",
        "main_results": "Two geographically structured viral variants were identified.",
        "interpretation_and_novelty": "Regional diversification better explained the observed structure.",
        "scientific_and_public_health_significance": "The findings support continued integrated genomic surveillance.",
        "limitations_and_evidence_strength": "The retrospective design limits causal interpretation.",
    }
    paper = {
        "paper_id": "paper-1", "paper_type": "research", "title": "English source title",
        "title_zh": "中文标题", "abstract": "Original English abstract.", "abstract_zh": "中文摘要。",
        "authors": ["Jane Doe"], "journal": "Journal", "publication_date_status": "in_window",
        "availability_date": "2026-07-15", "availability_date_basis": "online_date", "online_date": "2026-07-15",
        "elements_en": paper_elements, "elements_zh": {key: "中文结构化内容。" for key in paper_elements},
        "analysis": {"status": "passed", "analysis_level": "L1_abstract_only"},
        "priority_tier": "A", "priority_tier_reason": "test", "evidence_level": "E1",
        "translation_audit": {},
    }
    issue = {
        "title_zh": "测试每周情报", "title_en": "Test Weekly Intelligence", "issue_date": "2026-07-19",
        "window_start": "2026-07-13", "window_end": "2026-07-19", "papers": [paper], "news": [],
        "metrics": {"research": 1, "reviews": 0, "translated": 1}, "overview": {}, "source_status": {},
        "retrieval_funnel": {"papers": {"raw": 1, "after_window": 1, "after_type_gate": 1, "after_candidate_gate": 1, "after_final_gate": 1, "ready_before_top_n": 1, "top_n_limit": 50, "displayed": 1}, "news": {}},
        "analysis_quality": {"severity": "ok"},
    }
    render_site(issue, tmp_path)
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert "The study addressed incomplete genomic surveillance." in html
    assert "Not reported in the supplied evidence." not in html
    assert "Deep bilingual records" in html


def test_workflow_persists_daily_provider_state_and_runs_profiles_sequentially() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "Weekly 21-Virus Intelligence Cycle v17.4" in text
    assert "for PROFILE_ID in" in text
    assert "PIF_PROVIDER_STATE_FILE: /tmp/pif_data_repo/shared/state/provider_quota_daily.json" in text
    assert 'git add -A "profiles/$PROFILE_ID" "shared/state"' in text
    assert text.count('--state-dir "$OUT/data/state"') == 1
    assert "PIF_NEWS_EVENT_QUERY_LIMIT" in text
    assert "PIF_SCARCE_NEWS_PROFILES" in text
    assert "PIF_REJECT_PUBLICATION_TYPES" in text
    assert "PIF_REJECT_REPOSITORY_HOSTS" in text


def test_scholarly_abstract_removes_encoded_markup_and_long_repeats() -> None:
    from pifactory.utils import clean_scholarly_abstract

    repeated = (
        "&lt;b&gt;Background&lt;/b&gt; This sufficiently long result sentence reports that twelve of one hundred participants were positive in the study. "
        "This sufficiently long result sentence reports that twelve of one hundred participants were positive in the study. "
        "Abbreviations: LAMP1: lysosome membrane protein; MTOR: mechanistic target of rapamycin."
    )
    cleaned = clean_scholarly_abstract(repeated)
    assert "<b>" not in cleaned
    assert cleaned.count("twelve of one hundred") == 1
    assert "LAMP1" not in cleaned


def test_author_abbreviation_and_full_name_are_merged() -> None:
    from pifactory.dedup import dedup_papers

    rows = [
        {"title": "A study", "doi": "10.1234/a", "authors": ["Hade Ramos", "Pranav S. Pandit"], "source": "A"},
        {"title": "A study", "doi": "10.1234/a", "authors": ["Ramos H", "Pandit PS"], "source": "B"},
    ]
    merged = dedup_papers(rows)
    assert len(merged) == 1
    assert merged[0]["authors"] == ["Hade Ramos", "Pranav S. Pandit"]


def test_rendered_html_auditor_rejects_dict_repository_and_placeholder(tmp_path: Path) -> None:
    from scripts.audit_rendered_html import audit_html

    bad = tmp_path / "bad.html"
    bad.write_text(
        '''<html><body>
        <button data-language="zh"></button><button data-language="en"></button>
        <article class="card paper"><div class="meta-strip">Journal: Figshare</div>
        <div class="lang-en"><dl><dd>{'publication_date': '2026-07-15'}</dd>
        <dd>Not reported in the supplied evidence.</dd></dl></div></article>
        </body></html>''', encoding="utf-8"
    )
    result = audit_html(bad)
    codes = {row["code"] for row in result["findings"]}
    assert result["status"] == "failed"
    assert "python_dict_literal" in codes
    assert "english_placeholder" in codes
    placeholder = next(row for row in result["findings"] if row["code"] == "english_placeholder")
    assert placeholder["severity"] == "warning"
    assert "repository_object_rendered_as_paper" in codes


def test_rendered_html_auditor_accepts_bilingual_render(tmp_path: Path) -> None:
    from scripts.audit_rendered_html import audit_html

    paper_elements = {
        "research_question_and_background": "The question addressed surveillance gaps.",
        "study_design_and_population": "The cohort included 120 participants.",
        "methods": "The team used sequencing and regression.",
        "main_results": "Two variants were identified.",
        "interpretation_and_novelty": "Regional diversification explained the pattern.",
        "scientific_and_public_health_significance": "The results support surveillance.",
        "limitations_and_evidence_strength": "The retrospective design limits inference.",
    }
    paper = {
        "paper_id": "p1", "paper_type": "research", "title": "English title", "title_zh": "中文标题",
        "abstract": "A complete English abstract.", "abstract_zh": "完整中文摘要。", "authors": ["Jane Doe"],
        "journal": "Journal", "availability_date": "2026-07-15", "availability_date_basis": "online_date",
        "publication_date_status": "in_window", "elements_en": paper_elements,
        "elements_zh": {key: "中文结构内容。" for key in paper_elements},
        "analysis": {"status": "passed"}, "priority_tier": "A", "priority_tier_reason": "test", "evidence_level": "E1",
        "translation_audit": {},
    }
    issue = {
        "title_zh": "测试周报", "title_en": "Test Weekly Report", "issue_date": "2026-07-19",
        "window_start": "2026-07-13", "window_end": "2026-07-19", "papers": [paper], "news": [],
        "metrics": {"research": 1, "reviews": 0, "translated": 1}, "overview": {}, "source_status": {},
        "retrieval_funnel": {"papers": {"raw": 1, "after_window": 1, "after_type_gate": 1, "after_candidate_gate": 1, "after_final_gate": 1, "ready_before_top_n": 1, "top_n_limit": 50, "displayed": 1}, "news": {}},
        "analysis_quality": {"severity": "ok"},
    }
    render_site(issue, tmp_path)
    result = audit_html(tmp_path / "site/index.html")
    assert result["status"] == "passed"
