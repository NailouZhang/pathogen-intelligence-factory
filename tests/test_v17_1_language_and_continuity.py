from __future__ import annotations

from pathlib import Path

from pifactory.bundled_vocabulary import load_bundled_vocabulary
from pifactory.language_contract import (
    annotate_source_language,
    detect_text_language,
    is_verified_english,
    sanitize_english_analysis,
)
from pifactory.query_plan import build_relevance_rules
from pifactory.relevance_guard import apply_relevance_cliff_guard
from pifactory.render import render_site
from scripts.audit_rendered_html import audit_html

ROOT = Path(__file__).resolve().parents[1]
JAPANESE = "短報 波長の異なる深紫外線による高病原性鳥インフルエンザウイルスの不活化 2026 年 100 巻 4 号 p."


def _avian_profile() -> dict:
    profile = load_bundled_vocabulary(ROOT, "avian_influenza")["profile"]
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    return profile


def test_japanese_source_is_not_treated_as_verified_english() -> None:
    record = {"title": JAPANESE, "abstract": JAPANESE}
    language = annotate_source_language(record, kind="paper")
    assert language == "ja"
    assert detect_text_language(JAPANESE) == "ja"
    assert detect_text_language(JAPANESE, "en") == "ja"
    assert is_verified_english(JAPANESE) is False

    data = {
        "analysis": {"methods": JAPANESE, "main_results": JAPANESE},
        "summary_en": JAPANESE,
    }
    repaired = sanitize_english_analysis(data, kind="paper", source_language=language)
    assert is_verified_english(repaired["analysis"]["methods"])
    assert is_verified_english(repaired["summary_en"])
    assert repaired["source_language_evidence"]["methods"] == JAPANESE
    assert repaired["language_contract"]["source_language"] == "ja"


def test_render_defense_replaces_non_english_structured_elements_and_preserves_original(tmp_path: Path) -> None:
    paper = {
        "paper_id": "ja-1",
        "paper_type": "research",
        "title": JAPANESE,
        "title_zh": "不同波长深紫外线对高致病性禽流感病毒的灭活",
        "abstract": JAPANESE,
        "abstract_zh": "该研究评估深紫外线对高致病性禽流感病毒的灭活效果。",
        "source_language": "ja",
        "authors": ["A"],
        "journal": "Japanese Journal",
        "availability_date": "2026-07-21",
        "publication_date_status": "in_window",
        "elements_en": {
            "research_question_and_background": JAPANESE,
            "study_design_and_population": JAPANESE,
            "methods": JAPANESE,
            "main_results": JAPANESE,
            "interpretation_and_novelty": JAPANESE,
            "scientific_and_public_health_significance": JAPANESE,
            "limitations_and_evidence_strength": JAPANESE,
        },
        "elements_zh": {
            "research_question_and_background": "研究问题。",
            "study_design_and_population": "研究设计。",
            "methods": "研究方法。",
            "main_results": "主要结果。",
            "interpretation_and_novelty": "研究解释。",
            "scientific_and_public_health_significance": "公共卫生意义。",
            "limitations_and_evidence_strength": "研究局限。",
        },
    }
    issue = {
        "title_zh": "禽流感每周情报",
        "title_en": "Avian Influenza Weekly Intelligence",
        "issue_date": "2026-07-21",
        "window_start": "2026-07-14",
        "window_end": "2026-07-21",
        "papers": [paper],
        "supplementary_papers": [],
        "news": [],
        "supplementary_news": [],
        "metrics": {"primary_papers": 1, "translated": 1},
        "overview": {},
        "retrieval_funnel": {"papers": {"raw": 1, "after_window": 1, "after_dedup": 1, "relevant_catalog_after_completion_and_identity_gate": 1, "primary_displayed": 1}, "news": {}},
    }
    render_site(issue, tmp_path)
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'data-source-language="ja"' in html
    result = audit_html(tmp_path / "site/index.html")
    assert result["status"] == "passed"
    assert not any(row["code"] == "chinese_text_in_english_element" for row in result["findings"])


def test_auditor_classifies_japanese_separately_from_chinese(tmp_path: Path) -> None:
    bad = tmp_path / "bad.html"
    bad.write_text(
        '<button data-language="zh"></button><button data-language="en"></button>'
        '<article class="card paper"><div class="lang-en"><dl><dd lang="en">'
        + JAPANESE
        + '</dd></dl></div></article>',
        encoding="utf-8",
    )
    result = audit_html(bad)
    codes = {row["code"] for row in result["findings"]}
    assert "japanese_text_in_english_element" in codes
    assert "chinese_text_in_english_element" not in codes


def test_first_run_acceptance_ratio_triggers_recovery(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "100")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED", "10")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.15")
    candidates = [
        {
            "paper_id": f"p{i}",
            "title": "H5N1 avian influenza virus surveillance",
            "abstract": "Avian influenza virus infection was investigated in poultry with epidemiological surveillance.",
            "doi": f"10.1000/{i}",
            "metadata_verification": {"verified": True},
        }
        for i in range(156)
    ]
    output, audit = apply_relevance_cliff_guard(
        candidates,
        candidates[:19],
        _avian_profile(),
        kind="paper",
        previous_accepted=None,
    )
    assert "candidate_acceptance_ratio" in audit["trigger_reasons"]
    assert audit["target_accepted"] == 24
    assert len(output) >= 24
    assert audit["continuity_status"] == "recovered_output"


def test_no_safe_candidate_still_returns_valid_empty_issue(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "1")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED", "1")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.50")
    candidates = [{"paper_id": "x", "title": "Unrelated plant study", "abstract": "No pathogen identity is present."}]
    output, audit = apply_relevance_cliff_guard(candidates, [], _avian_profile(), kind="paper")
    assert output == []
    assert audit["continuity_status"] == "empty_valid_issue"
    assert audit["publication_must_continue"] is True
    assert audit["fabricated_acceptance_forbidden"] is True


def test_zero_candidates_are_explicit_empty_valid_output() -> None:
    output, audit = apply_relevance_cliff_guard([], [], _avian_profile(), kind="paper")
    assert output == []
    assert audit["continuity_status"] == "empty_valid_issue"
    assert audit["publication_must_continue"] is True
    assert audit["fabricated_acceptance_forbidden"] is True


def test_medium_news_pool_acceptance_ratio_triggers_recovery(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "100")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_RATIO_MIN_CANDIDATES", "20")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED", "10")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.15")
    candidates = [
        {
            "news_id": f"n{i}",
            "title": "H5N1 avian influenza outbreak update",
            "excerpt": (
                "Health authorities reported avian influenza virus infections "
                "in poultry and continued epidemiological surveillance."
            ),
            "url": f"https://example.org/news/{i}",
        }
        for i in range(87)
    ]
    output, audit = apply_relevance_cliff_guard(
        candidates,
        candidates[:1],
        _avian_profile(),
        kind="news",
        previous_accepted=None,
    )
    assert audit["triggered"] is True
    assert "candidate_acceptance_ratio" in audit["trigger_reasons"]
    assert audit["target_accepted"] == 14
    assert len(output) >= 14


def test_supplementary_original_non_english_title_is_metadata_not_deep_content(
    tmp_path: Path,
) -> None:
    supplementary = {
        "paper_id": "supp-uk",
        "paper_type": "research",
        "title": "КЛІНІКО-ПАТОГЕНЕТИЧНІ ТЕНДЕНЦІЇ ПНЕВМОНІЙ У ДІТЕЙ",
        "title_zh": "儿童肺炎的临床与发病机制趋势",
        "title_en": "Clinical and pathogenetic trends of pneumonia in children",
        "source_language": "uk",
        "authors": ["A"],
        "journal": "Journal",
        "availability_date": "2026-07-21",
        "publication_date_status": "in_window",
    }
    issue = {
        "title_zh": "测试周报",
        "title_en": "Test Weekly Intelligence",
        "issue_date": "2026-07-21",
        "window_start": "2026-07-14",
        "window_end": "2026-07-21",
        "papers": [],
        "supplementary_papers": [supplementary],
        "news": [],
        "supplementary_news": [],
        "metrics": {"supplementary_papers": 1},
        "overview": {},
        "retrieval_funnel": {"papers": {}, "news": {}},
    }
    render_site(issue, tmp_path)
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'data-metadata-role="title"' in html
    result = audit_html(tmp_path / "site/index.html")
    assert result["status"] == "passed"
    assert not any(
        row["code"] == "supplementary_card_contains_deep_content"
        for row in result["findings"]
    )
