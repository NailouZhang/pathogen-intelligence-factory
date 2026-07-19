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
    assert "15 条可核验目录" in html
    assert "有摘要或全文 12 条" in html
    assert "仅元数据 3 条" in html
    assert "主报告 10 篇" in html
    assert "补充文献 5 篇" in html
    assert "Top50表示进入深度主报告，而不是删除阈值" in html


def test_wechat_receives_primary_and_supplementary_definition():
    html = _overview_statlines(_issue(), wechat=True)
    assert "主报告 10 篇" in html
    assert "补充文献 5 篇" in html
    assert "完整审计保存在 data/audit" in html


def test_source_health_is_backend_compatibility_only():
    html = _source_health(_issue())
    assert "Backend source audit" in html
    assert "PubMed: healthy" in html
