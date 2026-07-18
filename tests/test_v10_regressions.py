from __future__ import annotations

from datetime import date

import src.pifactory.content as content
from src.pifactory.overview import _overview_validator, select_overview_items
from src.pifactory.postprocess import (
    RESEARCH_FIELDS,
    complete_text,
    contains_cross_field_overlap,
    deduplicate_structured_analysis,
)
from src.pifactory.render import _overview_statlines


class FailingHTTP:
    def request(self, *args, **kwargs):
        raise RuntimeError("static extraction failed")


def test_literature_overview_selection_prioritizes_current_window_not_input_order():
    items = []
    for i in range(20):
        items.append({
            "paper_id": f"old-{i}",
            "title": f"Older paper {i}",
            "published_date": "2024-01-01",
            "quality_score": 500 - i,
            "priority_tier": "A",
            "evidence_level": "E2",
            "journal": f"Old Journal {i}",
        })
    for i in range(18):
        items.append({
            "paper_id": f"new-{i}",
            "title": f"Current-week paper {i}",
            "published_date": f"2026-07-{12 + (i % 7):02d}",
            "quality_score": 80 - i,
            "priority_tier": "B",
            "evidence_level": "E1",
            "journal": f"Current Journal {i}",
        })
    selected = select_overview_items(
        items,
        minimum=15,
        maximum=25,
        window_start=date(2026, 7, 12),
        window_end=date(2026, 7, 18),
        kind="literature",
    )
    recent_ids = [x["paper_id"] for x in selected if x.get("overview_recent_window")]
    assert len(recent_ids) >= 15
    assert selected[0]["paper_id"].startswith("new-")


def test_overview_validator_rejects_internal_reservation_sentence():
    validator = _overview_validator({"p1", "p2", "p3"}, "literature")
    data = {
        "headline_zh": "本期汉坦病毒研究形成多项可核验进展",
        "lead_zh": "本期文献围绕临床干预、宿主监测和分子流行病学展开，并优先纳入报告窗口内发表的高质量研究。",
        "key_findings_zh": [
            "多中心研究保留了样本量、结局指标和效应方向，为临床证据评价提供依据。[p1]",
            "宿主调查报告了明确地点和检测方法，补充了近期传播风险证据。[p2]",
            "综述系统梳理了监测标准和证据缺口，为后续研究设计提供方向。[p3]",
        ],
        "trend_or_risk_zh": "近期研究重点正由单点描述转向临床、生态与分子监测的综合评价。",
        "caveats_zh": "部分研究为观察性设计或仅有摘要证据，结果不应被解释为确定因果关系。",
        "headline_en": "Recent hantavirus literature",
        "brief_en": "This literature briefing prioritizes papers published within the active reporting window and integrates study design, quantitative results, evidence strength, and source convergence across primary studies and reviews.",
        "source_ids": ["p1", "p2", "p3"],
    }
    assert validator(data)[0]
    bad = dict(data)
    bad["trend_or_risk_zh"] = "无法根据提供的证据可靠地确定主要共识。"
    assert validator(bad)[0] is False


def test_analysis_postprocess_removes_cross_field_duplicates_and_repairs_endings():
    duplicated = "本研究在三个中心纳入32名疑似患者并开展非随机开放试验。"
    payload = {
        "evidence": [
            {"id": "A1", "role": "background", "text": "汉坦病毒心肺综合征病死率较高，现有特异治疗证据有限。"},
            {"id": "A2", "role": "design_population", "text": duplicated},
            {"id": "A3", "role": "methods", "text": "研究者输注免疫血浆，并比较治疗组与历史队列的临床结局。"},
            {"id": "A4", "role": "results", "text": "29名确诊患者中4名死亡，报告病死率为14%。"},
            {"id": "A5", "role": "conclusion", "text": "结果支持进一步开展随机对照研究。"},
            {"id": "A6", "role": "implications", "text": "该研究为被动免疫治疗提供了早期临床证据。"},
            {"id": "A7", "role": "limitations", "text": "非随机设计和样本量较小限制了因果解释与外推性。"},
        ]
    }
    data = {
        "analysis": {
            "research_question_and_background": duplicated,
            "study_design_and_population": duplicated,
            "methods": duplicated,
            "main_results": "29名确诊患者中4名死亡……",
            "interpretation_and_novelty": "结果支持进一步研究。之前关联的替换",
            "scientific_and_public_health_significance": "该研究为被动免疫治疗提供早期证据",
            "limitations_and_evidence_strength": duplicated,
        },
        "evidence_ids": {field: [] for field in RESEARCH_FIELDS},
        "summary_en": "Complete summary.",
    }
    fixed = deduplicate_structured_analysis(data, payload, "research")
    overlap, _ = contains_cross_field_overlap(fixed["analysis"], RESEARCH_FIELDS)
    assert overlap is False
    assert "……" not in str(fixed)
    assert "之前关联的替换" not in str(fixed)
    assert all(fixed["analysis"][field].endswith(("。", ".", "！", "!", "？", "?")) for field in RESEARCH_FIELDS)


def test_complete_text_never_publishes_ellipsis_or_cut_clause():
    value, changed = complete_text("这是完整句子。下一部分内容……之前关联的替换", max_chars=80)
    assert changed
    assert "…" not in value
    assert "之前关联的替换" not in value
    assert value.endswith("。")


def test_overview_statistics_are_rendered_as_prominent_separate_lines():
    issue = {
        "metrics": {"research": 37, "reviews": 13},
        "retrieval_funnel": {
            "papers": {"raw": 2208, "after_window": 1800, "after_candidate_gate": 1200, "after_dedup": 224, "after_final_gate": 100, "ready_before_top_n": 82, "top_n_limit": 50, "top_n_excluded": 32, "displayed": 50},
            "news": {"raw": 111, "after_window": 90, "after_candidate_gate": 70, "after_dedup": 54, "after_final_gate": 28, "ready_before_top_n": 23, "top_n_limit": 20, "top_n_excluded": 3, "displayed": 20},
        },
    }
    html = _overview_statlines(issue)
    assert 'class="overview-statline"' in html
    assert "数据库记录 2,208 条" in html
    assert "去除重复 976 条" in html
    assert "相关性复核通过 100 条" in html
    assert "正文、分析与翻译门禁后可展示 82 条" in html
    assert "PIF_MAX_PAPERS=50" in html
    assert "取前 50 篇展示（研究 37、综述 13；其余 32 篇保留在审计数据中）" in html
    assert "PIF_MAX_NEWS=20" in html
    assert "取前 20 条展示（其余 3 条保留在审计数据中）" in html
    assert "统计口径" in html


def test_news_url_cleaning_removes_tracking_parameters():
    cleaned = content.clean_news_url("https://publisher.example/article?id=7&utm_source=rss&utm_medium=email&fbclid=abc")
    assert "utm_source" not in cleaned
    assert "utm_medium" not in cleaned
    assert "fbclid" not in cleaned
    assert "id=7" in cleaned


def test_playwright_fallback_is_used_only_after_static_failure(monkeypatch):
    body = " ".join([
        "The health authority confirmed a hantavirus case in the reporting region on Tuesday.",
        "The patient was hospitalized while laboratory confirmation and exposure investigation continued.",
        "Officials advised residents to avoid rodent urine and droppings and to ventilate closed buildings.",
        "No secondary cases were confirmed and the exposure source remained under investigation.",
    ] * 5)
    html = f"<html><head><title>Confirmed hantavirus case</title></head><body><article><p>{body}</p></article></body></html>"

    monkeypatch.setenv("PIF_NEWS_BROWSER_ENABLED", "true")
    monkeypatch.setenv("PIF_NEWS_BROWSER_MAX_PAGES", "2")
    monkeypatch.setattr(content, "fetch_rendered_html", lambda url: {"status": "success", "url": url, "title": "Confirmed hantavirus case", "html": html})
    record = content.resolve_and_extract_news(
        FailingHTTP(),
        {"title": "Confirmed hantavirus case", "url": "https://publisher.example/article", "excerpt": "Confirmed hantavirus case"},
    )
    assert record["content_status"] in {"full", "partial"}
    assert record["content_method"].startswith("playwright:")
    assert len(record["content"]) > 500
    assert record["content"] != record["title"]
