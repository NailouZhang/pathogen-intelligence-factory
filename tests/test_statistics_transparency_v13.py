from __future__ import annotations

from pifactory.render import _overview_statlines, _source_health


def _issue() -> dict:
    return {
        "metrics": {"research": 34, "reviews": 16},
        "retrieval_funnel": {
            "papers": {
                "raw": 2620,
                "after_window": 24,
                "after_candidate_gate": 20,
                "after_dedup": 18,
                "after_final_gate": 16,
                "ready_before_top_n": 14,
                "top_n_limit": 10,
                "top_n_excluded": 4,
                "selection_policy": "priority_evidence_recency_source_quality",
                "displayed": 10,
            },
            "news": {
                "raw": 87,
                "after_window": 30,
                "after_candidate_gate": 25,
                "after_dedup": 21,
                "after_final_gate": 18,
                "ready_before_top_n": 12,
                "top_n_limit": 10,
                "top_n_excluded": 2,
                "selection_policy": "priority_evidence_recency_source_quality",
                "displayed": 10,
            },
        },
        "source_status": {
            "sources": [
                {
                    "source": "PubMed",
                    "health": "healthy",
                    "successful_queries": 5,
                    "zero_result_queries": 0,
                    "failed_queries": 0,
                    "skipped_queries": 0,
                    "records_reported": 20,
                }
            ]
        },
        "relevance_review": {
            "papers": {"candidates_reviewed_by_python": 18},
            "news": {"candidates_reviewed_by_python": 21},
        },
        "anchor_coverage": {
            "concept_count": 2,
            "concepts": [
                {
                    "concept_id": "hantavirus",
                    "providers": {
                        "pubmed": {"query": "hantavirus", "executed": True, "records_reported": 20}
                    },
                },
                {
                    "concept_id": "hfrs",
                    "providers": {
                        "pubmed": {"query": "HFRS", "executed": False, "records_reported": 0}
                    },
                },
            ],
        },
    }


def test_top_n_is_explicitly_separated_from_quality_gates():
    html = _overview_statlines(_issue())
    assert "相关性复核通过 16 条" in html
    assert "门禁后可展示 14 条" in html
    assert "PIF_MAX_PAPERS=10" in html
    assert "其余 4 篇保留在审计数据中" in html
    assert "Top-N 仅控制网页与公众号篇幅" in html


def test_wechat_receives_the_same_counting_definition():
    html = _overview_statlines(_issue(), wechat=True)
    assert "PIF_MAX_PAPERS=10" in html
    assert "PIF_MAX_NEWS=10" in html
    assert "完整漏斗见 data/audit/retrieval_funnel.json" in html


def test_source_health_uses_current_concept_schema_not_legacy_identity_schema():
    html = _source_health(_issue())
    assert "核心检索概念：共 2 个" in html
    assert "至少一个来源返回记录的概念 1 个" in html
    assert "已计划但未执行查询的概念 1 个" in html
    assert "Top-10" in html
