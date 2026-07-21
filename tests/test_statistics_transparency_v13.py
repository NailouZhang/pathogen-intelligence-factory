from __future__ import annotations

from pifactory.render import _overview_statlines, _source_health


def _issue() -> dict:
    return {
        "metrics": {"research": 6, "reviews": 4},
        "retrieval_funnel": {
            "papers": {
                "raw": 2620,
                "after_window": 24,
                "after_type_gate": 22,
                "after_dedup": 18,
                "after_final_relevance": 16,
                "relevant_catalog_after_completion_and_identity_gate": 15,
                "evidence_ready_catalog": 12,
                "metadata_only_catalog": 3,
                "primary_top_n_limit": 10,
                "primary_displayed": 10,
                "supplementary_limit": 100,
                "supplementary_displayed": 5,
            },
            "news": {
                "raw": 87,
                "after_window": 30,
                "after_dedup": 21,
                "after_final_gate": 18,
                "ready_before_top_n": 12,
                "top_n_limit": 10,
                "displayed": 10,
            },
        },
        "source_status": {"sources": [{"source": "PubMed", "health": "healthy"}]},
    }


def test_top_n_means_deep_report_not_deletion():
    html = _overview_statlines(_issue())
    assert "文献：检索2,620｜日期窗24｜去重18｜终审15｜主报告10｜补充5" in html
    assert "新闻：检索87｜日期窗30｜主新闻10｜补充0" in html
    assert "Top50" not in html
    assert "完整审计" not in html


def test_wechat_receives_primary_and_supplementary_definition():
    html = _overview_statlines(_issue(), wechat=True)
    assert "font-size:12px" in html
    assert "color:#888888" in html
    assert "主报告10" in html
    assert "补充5" in html
    assert "data/audit" not in html


def test_source_health_is_backend_compatibility_only():
    html = _source_health(_issue())
    assert "Backend source audit" in html
    assert "PubMed: healthy" in html
