from __future__ import annotations

from datetime import date
from pathlib import Path

from pifactory.analysis_quality import summarize_analysis_quality
from pifactory.config import Settings
from pifactory.render import paper_card
from pifactory.scholarly import search_biorxiv_medrxiv
from scripts.audit_rendered_html import audit_html


def test_honest_partial_absence_sentence_is_not_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "report.html"
    path.write_text(
        '<button data-language="zh"></button><button data-language="en"></button>'
        '<article class="card news"><div class="lang-en"><dl>'
        '<dd>Publication date: 2026-07-17. Event date: Not reported in the supplied evidence; investigation occurred on or before 2026-07-17.</dd>'
        '</dl></div></article>',
        encoding="utf-8",
    )
    result = audit_html(path)
    assert result["critical_count"] == 0
    assert not any(row["code"] == "english_placeholder" for row in result["findings"])
    assert result["status"] == "passed"


def test_dominant_placeholder_is_warning_and_publishable(tmp_path: Path) -> None:
    path = tmp_path / "report.html"
    path.write_text(
        '<button data-language="zh"></button><button data-language="en"></button>'
        '<article class="card paper"><div class="lang-en"><dl>'
        '<dd>Not reported in the supplied evidence.</dd>'
        '</dl></div></article>',
        encoding="utf-8",
    )
    result = audit_html(path)
    finding = next(row for row in result["findings"] if row["code"] == "english_placeholder")
    assert finding["severity"] == "warning"
    assert finding["coverage"] >= 0.70
    assert result["critical_count"] == 0
    assert result["status"] == "passed_with_warnings"


def test_final_quality_summary_uses_displayed_denominator() -> None:
    def item(status: str) -> dict:
        return {"analysis": {"status": status, "failure_category": "validation_failed"}}
    candidate = [item("fallback_source_extract") for _ in range(17)] + [item("passed") for _ in range(16)]
    displayed = [item("fallback_source_extract") for _ in range(17)] + [item("passed") for _ in range(9)]
    pool = summarize_analysis_quality(candidate, [], scope="candidate_pool")
    final = summarize_analysis_quality(displayed, [], scope="displayed")
    assert pool["combined"]["fallback_ratio"] == 0.5152
    assert final["combined"]["fallback_ratio"] == 0.6538
    assert "17/26" in final["message_zh"]


def test_preprint_bulk_feed_is_capped_and_identity_filtered() -> None:
    rows = [
        {"doi": f"10.1101/{i}", "title": "Unrelated cancer study", "abstract": "oncology", "authors": "A", "date": "2026-07-18"}
        for i in range(4)
    ] + [
        {"doi": "10.1101/hanta", "title": "Hantavirus spillover surveillance", "abstract": "An orthohantavirus study", "authors": "B", "date": "2026-07-18"}
    ]

    class Http:
        def get_json(self, url: str):
            cursor = int(url.rsplit("/", 1)[-1])
            page = rows[cursor:cursor + 2]
            return {"collection": page, "messages": [{"total": len(rows)}]}

    result = search_biorxiv_medrxiv(
        Http(), date(2026, 7, 13), date(2026, 7, 19),
        max_records_per_server=5, identity_terms=["hantavirus", "orthohantavirus"],
    )
    assert len(result) == 2
    assert all("Hantavirus" in row["title"] for row in result)


def test_preprint_large_feed_samples_head_and_latest_tail() -> None:
    rows = [
        {"doi": f"10.1101/{i}", "title": f"Hantavirus record {i}", "abstract": "hantavirus", "authors": "A", "date": "2026-07-18"}
        for i in range(10)
    ]
    requested: list[int] = []

    class Http:
        def get_json(self, url: str):
            cursor = int(url.rsplit("/", 1)[-1])
            requested.append(cursor)
            return {"collection": rows[cursor:cursor + 2], "messages": [{"total": len(rows)}]}

    result = search_biorxiv_medrxiv(
        Http(), date(2026, 7, 13), date(2026, 7, 19),
        max_records_per_server=4, identity_terms=["hantavirus"],
    )
    assert len(result) == 8  # four records sampled for each of two servers
    assert requested == [0, 8, 0, 8]


def test_fallback_badge_is_visible_on_card() -> None:
    fields = {
        "research_question_and_background": "The question was described.",
        "study_design_and_population": "The population was described.",
        "methods": "Methods were described.",
        "main_results": "Results were described.",
        "interpretation_and_novelty": "Interpretation was described.",
        "scientific_and_public_health_significance": "Significance was described.",
        "limitations_and_evidence_strength": "Not reported in the supplied evidence.",
    }
    work = {
        "paper_type": "research", "title": "Title", "title_zh": "标题", "abstract": "Abstract",
        "abstract_zh": "摘要", "authors": ["A"], "elements_en": fields, "elements_zh": fields,
        "analysis": {"status": "fallback_source_extract", "analysis_level": "L1_abstract_only"},
        "priority_tier": "B", "evidence_level": "E1", "translation_audit": {},
    }
    html = paper_card(work)
    assert "规则兜底·低置信" in html
    assert "Deterministic fallback · low confidence" in html
    assert "evidence gap" in html


def test_settings_expose_preprint_bounds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIF_PREPRINT_MAX_RECORDS_PER_SERVER", "123")
    monkeypatch.setenv("PIF_PREPRINT_IDENTITY_FILTER", "true")
    settings = Settings("hantavirus", tmp_path, tmp_path / "out", tmp_path / "state")
    assert settings.preprint_max_records_per_server == 123
    assert settings.preprint_identity_filter_enabled is True


def test_computational_fallback_uses_modeling_track() -> None:
    from pifactory.analysis import _fallback_research

    payload = {
        "evidence_scope": "abstract_only",
        "evidence": [
            {"id": "E1", "role": "background", "text": "This study developed a compartmental mathematical model for hantavirus transmission."},
            {"id": "E2", "role": "methods", "text": "Sensitivity analysis and Bayesian regression estimated model parameters under four intervention scenarios."},
            {"id": "E3", "role": "results", "text": "The model predicted that combined prevention and isolation reduced the reproduction number below one."},
            {"id": "E4", "role": "conclusion", "text": "The simulation supports targeted prevention and surveillance."},
        ],
    }
    result = _fallback_research(payload, "validation failed", failure_category="validation_failed")
    assert result["fallback_track"] == "computational_or_modeling"
    assert "model" in result["analysis"]["methods"].casefold() or "regression" in result["analysis"]["methods"].casefold()


def test_credential_preflight_emits_authentication_repair_hint() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/check_credentials.py").read_text(encoding="utf-8")
    assert "action_hint" in text
    assert "Regenerate the provider key" in text
    assert "SILICONFLOW_API_KEY" in text


def test_workflow_uses_siliconflow_cn_endpoint() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "SILICONFLOW_BASE_URL" in workflow
    assert "https://api.siliconflow.cn/v1" in workflow
    assert "api.siliconflow.com" not in workflow
