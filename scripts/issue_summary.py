#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def summarize(issue: dict[str, Any]) -> dict[str, Any]:
    funnel = issue.get("retrieval_funnel") or {}
    papers = funnel.get("papers") or {}
    news = funnel.get("news") or {}
    return {
        "schema_version": "v15-summary-1",
        "issue_id": issue.get("issue_id"),
        "profile_id": issue.get("profile_id"),
        "window": [issue.get("window_start"), issue.get("window_end")],
        "papers": {
            "raw": _i(papers.get("raw")),
            "after_window": _i(papers.get("after_window")),
            "after_type_gate": _i(papers.get("after_type_gate")),
            "after_dedup": _i(papers.get("after_dedup")),
            "after_final_relevance": _i(papers.get("after_final_relevance") or papers.get("after_final_gate")),
            "after_relevance_gate": _i(papers.get("after_final_relevance") or papers.get("after_final_gate")),
            "ready_before_top_n": _i(papers.get("primary_ready_before_top_n") or papers.get("ready_before_top_n")),
            "top_n_excluded": _i(papers.get("primary_top_n_excluded_to_supplementary") or papers.get("top_n_excluded")),
            "relevant_catalog": _i(papers.get("relevant_catalog_after_completion_and_identity_gate") or papers.get("after_final_gate")),
            "evidence_ready_catalog": _i(papers.get("evidence_ready_catalog")),
            "metadata_only_catalog": _i(papers.get("metadata_only_catalog")),
            "primary_displayed": _i(papers.get("primary_displayed")),
            "supplementary_displayed": _i(papers.get("supplementary_displayed")),
            "primary_limit": _i(papers.get("primary_top_n_limit")),
            "supplementary_limit": _i(papers.get("supplementary_limit")),
            "selection_policy": papers.get("selection_policy") or "verified_primary_top_n_plus_verified_supplementary_catalog",
        },
        "news": {
            "raw": _i(news.get("raw")),
            "after_window": _i(news.get("after_window")),
            "after_dedup": _i(news.get("after_dedup")),
            "after_final_relevance": _i(news.get("after_final_gate")),
            "ready_before_top_n": _i(news.get("ready_before_top_n")),
            "displayed": _i(news.get("displayed")),
            "top_n_limit": _i(news.get("top_n_limit")),
            "selection_policy": news.get("selection_policy") or "qualification_independent_from_wechat_length",
        },
    }


def as_markdown(summary: dict[str, Any]) -> str:
    p = summary["papers"]
    n = summary["news"]
    return "\n".join([
        f"### {summary.get('profile_id') or 'unknown'} — {summary.get('issue_id') or 'unknown'}",
        "",
        "#### 文献生命周期",
        "",
        "| 原始 | 日期窗 | 类型门禁 | 去重后 | 相关性终审 | 可核验目录 | 有摘要/全文 | 仅元数据 | 主报告 | 补充文献 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {p['raw']} | {p['after_window']} | {p['after_type_gate']} | {p['after_dedup']} | {p['after_final_relevance']} | {p['relevant_catalog']} | {p['evidence_ready_catalog']} | {p['metadata_only_catalog']} | {p['primary_displayed']} | {p['supplementary_displayed']} |",
        "",
        f"主报告Top{p['primary_limit']}只决定深度分析范围，不是删除阈值；其余通过终审且元数据可核验的记录进入补充文献区，最多展示{p['supplementary_limit']}条。Top-N只控制展示篇幅；Top-N前可展示记录不因篇幅限制被删除。",
        "",
        "#### 新闻生命周期",
        "",
        "| 原始 | 日期窗 | 去重后 | 相关性终审 | 合格池 | 展示 |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {n['raw']} | {n['after_window']} | {n['after_dedup']} | {n['after_final_relevance']} | {n['ready_before_top_n']} | {n['displayed']} |",
        "",
        "新闻资格独立于微信公众号字符限制；标准数据保存完整内容，微信仅在渲染阶段压缩。",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the v15 literature/news lifecycle summary")
    parser.add_argument("issue", type=Path, help="Path to data/latest.json")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--github-summary", type=Path, default=None)
    args = parser.parse_args()
    issue = json.loads(args.issue.read_text(encoding="utf-8"))
    summary = summarize(issue)
    output = json.dumps(summary, ensure_ascii=False, indent=2) if args.format == "json" else as_markdown(summary)
    print(output)
    if args.github_summary:
        args.github_summary.parent.mkdir(parents=True, exist_ok=True)
        with args.github_summary.open("a", encoding="utf-8") as fh:
            fh.write(output + "\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
