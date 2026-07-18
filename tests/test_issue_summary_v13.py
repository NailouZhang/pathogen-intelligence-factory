from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "issue_summary.py"
    spec = importlib.util.spec_from_file_location("issue_summary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_issue_summary_keeps_relevance_and_top_n_as_separate_stages():
    module = _module()
    issue = {
        "issue_id": "hantavirus-2026-07-18",
        "profile_id": "hantavirus",
        "retrieval_funnel": {
            "papers": {
                "raw": 2600,
                "after_window": 20,
                "after_candidate_gate": 18,
                "after_dedup": 16,
                "after_final_gate": 15,
                "ready_before_top_n": 12,
                "top_n_limit": 10,
                "top_n_excluded": 2,
                "displayed": 10,
            },
            "news": {
                "raw": 80,
                "after_window": 30,
                "after_candidate_gate": 25,
                "after_dedup": 20,
                "after_final_gate": 18,
                "ready_before_top_n": 9,
                "top_n_limit": 10,
                "top_n_excluded": 0,
                "displayed": 9,
            },
        },
    }
    result = module.summarize(issue)
    assert result["papers"]["after_relevance_gate"] == 15
    assert result["papers"]["ready_before_top_n"] == 12
    assert result["papers"]["top_n_excluded"] == 2
    text = module.as_markdown(result)
    assert "Top-N前可展示" in text
    assert "只控制展示篇幅" in text
