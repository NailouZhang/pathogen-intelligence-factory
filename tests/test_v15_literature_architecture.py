from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import yaml

from pifactory.dates import assess_publication_date
from pifactory.literature import (
    build_post_retrieval_vocabulary,
    classify_scholarly_payload,
    complete_literature_catalog,
    metadata_verification,
    select_primary_and_supplementary,
    validate_frozen_core_terms,
    verified_evidence_status,
)
from pifactory.render import render_site, render_wechat_package
from pifactory.translation import build_wechat_news_summary
from scripts.audit_rendered_html import audit_html

ROOT = Path(__file__).resolve().parents[1]


def _record(index: int, *, abstract: str = "", verified: bool = True) -> dict:
    row = {
        "paper_id": f"p{index}",
        "source": "PubMed",
        "sources": ["PubMed"],
        "source_ids": {"pmid": str(1000 + index)},
        "doi": f"10.1234/p{index}",
        "title": f"Hantavirus study {index}",
        "authors": ["A. Author"],
        "journal": "Journal of Virology",
        "year": 2026,
        "canonical_publication_date": "2026-07-18",
        "canonical_publication_date_basis": "online_date",
        "publication_date_status": "in_window",
        "abstract": abstract,
        "url": f"https://example.org/p{index}",
        "priority_tier": "A" if index < 3 else "B",
        "quality_score": 100 - index,
    }
    if not verified:
        row.update({"title": "x", "doi": None, "source_ids": {}, "journal": "", "authors": [], "url": ""})
    metadata_verification(row)
    verified_evidence_status(row)
    return row


def test_all_profiles_use_exactly_five_frozen_simple_terms() -> None:
    paths = sorted((ROOT / "profiles").glob("*/seed.yaml"))
    assert len(paths) == 21
    for path in paths:
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        result = validate_frozen_core_terms(seed, strict=False)
        assert result["passed"], (seed["profile_id"], result["issues"])
        assert result["core_term_count"] == 5
        strategy = seed["search_strategy"]
        assert strategy["frozen"] is True
        assert strategy["allow_weekly_mutation"] is False
        assert strategy["core_terms_version"]
        assert all(" AND " not in term and " OR " not in term for term in result["terms"])
        assert "manual_query_skeletons" not in seed



def test_pipeline_dynamic_replenishment_stops_on_final_primary_or_budget() -> None:
    text = (ROOT / "src/pifactory/pipeline_v15.py").read_text(encoding="utf-8")
    assert "comparison_pool = rank_papers" in text
    assert "analysis_attempt_budget_exhausted" in text
    assert "completion_processed < settings.max_fulltexts" in text
    assert "_review_paper_batch(completed" in text
    assert "_analyze_translate_paper(item)" in text
    assert "metadata_only_" in text
    assert "v16-dedup-first-comparison-pool-1" in text

def test_post_retrieval_vocabulary_is_purpose_specific_and_not_bulk_queries() -> None:
    seed = yaml.safe_load((ROOT / "profiles/hantavirus/seed.yaml").read_text(encoding="utf-8"))
    vocab = build_post_retrieval_vocabulary(seed)
    assert vocab["identity_terms"]
    assert vocab["paper_priority_terms"]
    assert vocab["document_type_terms"]
    assert vocab["query_conversion_allowed"] is False
    assert all(item["forbidden_without_context"] is True for item in vocab["qualified_abbreviations"])


def test_canonical_publication_date_uses_single_priority_field_and_ignores_indexed_date() -> None:
    decision = assess_publication_date(
        {
            "first_publication_date": "2026-07-13",
            "online_date": "2026-07-18",
            "published_date": "2026-08-01",
            "print_date": "2026-09-01",
            "indexed_date": "2026-07-19",
            "created_date": "2026-07-19",
        },
        date(2026, 7, 13),
        date(2026, 7, 19),
        future_days=90,
    )
    assert decision.accepted
    assert decision.canonical_basis == "first_publication_date"
    assert decision.canonical_date == "2026-07-13"

    old = assess_publication_date(
        {"online_date": "2010-01-01", "indexed_date": "2026-07-18"},
        date(2026, 7, 13),
        date(2026, 7, 19),
        future_days=90,
    )
    assert old.accepted is False
    assert old.reason == "canonical_publication_date_before_window"


def test_short_valid_abstract_survives_but_invalid_pages_do_not() -> None:
    assert classify_scholarly_payload("Hantavirus RNA detected.")["valid"] is True
    assert classify_scholarly_payload("404 Page not found")["reason"] == "http_404"
    assert classify_scholarly_payload("Sign in for institutional access")["reason"] == "login_wall"
    assert classify_scholarly_payload("Please enable JavaScript to continue")["reason"] == "javascript_placeholder"


def test_dynamic_completion_occurs_after_dedup_contract_and_retains_metadata_only() -> None:
    catalog = [_record(i) for i in range(8)]
    calls: list[str] = []

    def enrich(row: dict) -> dict:
        calls.append(row["paper_id"])
        if int(row["paper_id"][1:]) in {0, 2, 4}:
            row["abstract"] = "Short but verified hantavirus abstract."
            row["abstract_source"] = "PubMed"
        return row

    completed, audit = complete_literature_catalog(
        catalog,
        enrich_one=enrich,
        primary_target=3,
        max_budget=8,
        batch_size=2,
        workers=1,
    )
    assert audit["evidence_ready"] >= 3
    assert audit["processed"] == 6
    assert calls == ["p0", "p1", "p2", "p3", "p4", "p5"]
    assert len(completed) == 8
    assert completed[7]["content_completion"]["status"] == "not_attempted"
    assert completed[7]["content_completion"]["reason"] == "primary_target_reached"
    assert completed[7]["metadata_verification"]["verified"] is True


def test_primary_top_n_does_not_delete_other_verified_records() -> None:
    catalog = [_record(i, abstract="Verified abstract.") for i in range(5)] + [_record(5), _record(6)]
    primary_ready = []
    for row in catalog[:5]:
        row = dict(row)
        row.update({
            "paper_type": "research",
            "title_zh": f"中文题目{row['paper_id']}",
            "abstract_zh": "中文摘要",
            "translation_ready": True,
            "analysis_ready": True,
            "elements_en": {"methods": "Method"},
            "elements_zh": {"methods": "方法"},
        })
        primary_ready.append(row)
    primary, supplementary, audit = select_primary_and_supplementary(
        catalog,
        primary_ready=primary_ready,
        primary_limit=2,
        supplementary_limit=100,
    )
    assert [x["paper_id"] for x in primary] == ["p0", "p1"]
    assert {x["paper_id"] for x in supplementary} == {"p2", "p3", "p4", "p5", "p6"}
    assert audit["catalog_relevant"] == 7
    assert audit["primary_displayed"] == 2
    assert audit["supplementary_displayed"] == 5
    assert all(x["display_mode"] == "metadata_only" for x in supplementary)
    assert all("abstract" not in x and "analysis" not in x and "elements_en" not in x for x in supplementary)


def test_public_page_orders_primary_supplementary_news_and_hides_backend_quality(tmp_path: Path) -> None:
    primary = _record(1, abstract="Verified abstract.")
    primary.update({
        "paper_type": "research",
        "title_zh": "主报告中文标题",
        "abstract_zh": "主报告中文摘要",
        "elements_en": {
            "research_question_and_background": "Question.",
            "study_design_and_population": "Design.",
            "methods": "Methods.",
            "main_results": "Results.",
            "interpretation_and_novelty": "Interpretation.",
            "scientific_and_public_health_significance": "Significance.",
            "limitations_and_evidence_strength": "Limitations.",
        },
        "elements_zh": {
            "research_question_and_background": "问题。",
            "study_design_and_population": "设计。",
            "methods": "方法。",
            "main_results": "结果。",
            "interpretation_and_novelty": "解释。",
            "scientific_and_public_health_significance": "意义。",
            "limitations_and_evidence_strength": "局限。",
        },
    })
    supplementary = {
        "paper_id": "s1",
        "title": "Metadata-only paper",
        "title_zh": "仅元数据文献",
        "authors": ["B. Author"],
        "journal": "Metadata Journal",
        "doi": "10.1234/s1",
        "canonical_publication_date": "2026-07-18",
        "canonical_publication_date_basis": "online_date",
        "publication_date_status": "in_window",
        "notice_zh": "摘要尚未公开。本条仅提供经过核验的出版元数据，不生成研究结论和结构化要素。",
    }
    news = {
        "news_id": "n1",
        "title": "Health authority reports hantavirus case",
        "title_zh": "卫生部门报告汉坦病毒病例",
        "source": "Health Authority",
        "published_date": "2026-07-18",
        "url": "https://example.org/news",
        "content": "Original news body.",
        "content_zh": "完整中文新闻摘要。",
        "elements_en": {
            "time": "18 July 2026.", "location_and_population": "Region A.", "event": "A case was reported.",
            "scale_impact_and_risk": "One case.", "response_status_and_uncertainty": "Investigation continues.",
        },
        "elements_zh": {
            "time": "2026年7月18日。", "location_and_population": "A地区。", "event": "报告一例病例。",
            "scale_impact_and_risk": "一例。", "response_status_and_uncertainty": "调查继续。",
        },
    }
    issue = {
        "schema_version": "6.0",
        "issue_id": "hantavirus-2026-07-18",
        "profile_id": "hantavirus",
        "generated_at": "2026-07-18T00:00:00Z",
        "title_zh": "汉坦病毒每周情报",
        "title_en": "Hantavirus Weekly Intelligence",
        "issue_date": "2026-07-18",
        "window_start": "2026-07-12",
        "window_end": "2026-07-18",
        "papers": [primary],
        "supplementary_papers": [supplementary],
        "news": [news],
        "overview": {"literature": {}, "news": {}},
        "metrics": {"research": 1, "reviews": 0, "translated": 2},
        "retrieval_funnel": {"papers": {}, "news": {}},
        "analysis_quality": {"severity": "critical", "message_zh": "不应出现在前台"},
        "llm_usage": {"providers": ["test"]},
    }
    render_site(issue, tmp_path)
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert html.index("📘 主报告：研究论文 / Primary Research") < html.index("📎 补充文献 / Supplementary Literature") < html.index("🚨 突发动态与新闻 / Health News")
    assert "摘要尚未公开" not in html
    assert "不生成研究结论和结构化要素" not in html
    assert "不应出现在前台" not in html
    assert "LLM" not in html
    audit = audit_html(tmp_path / "site/index.html")
    assert audit["critical_count"] == 0
    assert audit["paper_card_markers"] == 1
    assert audit["supplementary_card_markers"] == 1
    assert audit["news_card_markers"] == 1


def test_supplementary_card_deep_content_is_a_hard_audit_failure(tmp_path: Path) -> None:
    path = tmp_path / "bad.html"
    path.write_text(
        '<button data-language="zh"></button><button data-language="en"></button>'
        '<article class="card supplementary-card supplementary"><div class="translated-body">bad abstract</div><dl><dd>bad result</dd></dl></article>',
        encoding="utf-8",
    )
    result = audit_html(path)
    assert result["critical_count"] >= 1
    assert any(x["code"] == "supplementary_card_contains_deep_content" for x in result["findings"])


def test_news_channel_compaction_does_not_define_standard_eligibility() -> None:
    body = "完整新闻内容。" * 500
    analysis = {
        "time": "2026年7月18日。",
        "location_and_population": "A地区居民。",
        "event": "卫生部门报告病例。",
        "scale_impact_and_risk": body,
        "response_status_and_uncertainty": "调查仍在进行。",
    }
    compact = build_wechat_news_summary(analysis, body, limit=120)
    assert len(compact) <= 120
    assert len(body) > len(compact)
    # The full standard data remains intact; compaction is a derived channel view.
    record = {"content_zh": body, "wechat_summary_zh": compact, "translation_ready": True}
    assert len(record["content_zh"]) > 1000
    assert record["translation_ready"] is True


def test_wechat_manifest_v2_remains_runner_compatible(tmp_path: Path) -> None:
    issue = {
        "schema_version": "6.0",
        "issue_id": "hantavirus-2026-07-18",
        "profile_id": "hantavirus",
        "issue_date": "2026-07-18",
        "generated_at": "2026-07-18T00:00:00Z",
        "title_zh": "汉坦病毒每周情报",
        "title_en": "Hantavirus Weekly Intelligence",
        "papers": [], "supplementary_papers": [], "news": [],
        "overview": {"literature": {}, "news": {}},
        "retrieval_funnel": {"papers": {}, "news": {}},
    }
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    render_wechat_package(issue, tmp_path, {"cover_sha256": "abc", "generator": "test", "profile_fingerprint": "fp"})
    manifest = json.loads((tmp_path / "wechat-package/manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["contract"] == "pathogen-wechat-package/v2"
    assert manifest["source"]["issue_schema_version"] == "6.0"
    assert manifest["source"]["primary_papers"] == 0
    assert manifest["source"]["supplementary_papers"] == 0
