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
    result: dict[str, Any] = {
        "issue_id": issue.get("issue_id"),
        "profile_id": issue.get("profile_id"),
        "window": [issue.get("window_start"), issue.get("window_end")],
        "papers": {},
        "news": {},
    }
    for kind in ("papers", "news"):
        data = funnel.get(kind) or {}
        ready = _i(data.get("ready_before_top_n") or data.get("displayed"))
        shown = _i(data.get("displayed"))
        result[kind] = {
            "raw": _i(data.get("raw")),
            "after_window": _i(data.get("after_window")),
            "after_candidate_gate": _i(data.get("after_candidate_gate")),
            "after_dedup": _i(data.get("after_dedup")),
            "after_relevance_gate": _i(data.get("after_final_gate")),
            "ready_before_top_n": ready,
            "top_n_limit": _i(data.get("top_n_limit") or shown),
            "top_n_excluded": _i(data.get("top_n_excluded") or max(0, ready - shown)),
            "displayed": shown,
            "selection_policy": data.get("selection_policy") or "priority_evidence_recency_source_quality",
        }
    return result


def as_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"### {summary.get('profile_id') or 'unknown'} — {summary.get('issue_id') or 'unknown'}",
        "",
        "| 类型 | 原始 | 时间窗 | 候选门禁 | 去重后 | 相关性通过 | Top-N前可展示 | Top-N限制 | 因Top-N未展示 | 最终展示 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, label in (("papers", "文献"), ("news", "新闻")):
        row = summary[kind]
        lines.append(
            f"| {label} | {row['raw']} | {row['after_window']} | {row['after_candidate_gate']} | "
            f"{row['after_dedup']} | {row['after_relevance_gate']} | {row['ready_before_top_n']} | "
            f"{row['top_n_limit']} | {row['top_n_excluded']} | {row['displayed']} |"
        )
    lines.extend([
        "",
        "Top-N 按优先级、证据强度、时效性和来源质量排序，只控制展示篇幅；未展示的合格记录保留在审计数据中。",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Print a transparent retrieval and Top-N summary")
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
            fh.write(output)
            fh.write("\n\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
