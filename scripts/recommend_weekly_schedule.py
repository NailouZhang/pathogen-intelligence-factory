#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _score(row: dict[str, Any]) -> float:
    # Runtime dominates; candidate/LLM/browser pressure refine ties.
    return (
        _num(row.get("elapsed_seconds")) / 60.0
        + min(60.0, _num(row.get("unique_papers")) / 15.0)
        + min(40.0, _num(row.get("llm_attempts")) / 5.0)
        + min(20.0, _num(row.get("news_candidates")) / 5.0)
        + (35.0 if row.get("schedule_relevant_failure") else 0.0)
    )


def collect(data_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for profile_dir in sorted((data_root / "profiles").glob("*")):
        if not profile_dir.is_dir():
            continue
        audit = profile_dir / "data" / "audit"
        runtime = _load(audit / "runtime_budget.json")
        funnel = _load(audit / "retrieval_funnel.json")
        llm = _load(audit / "llm_provider_usage.json")
        failure = _load(audit / "profile_failure_classification.json")
        issue = _load(profile_dir / "data" / "latest.json")
        metrics = issue.get("metrics") or {}
        unique = (
            (funnel.get("papers") or {}).get("deduplicated")
            or funnel.get("papers_deduplicated")
            or funnel.get("paper_unique")
            or 0
        )
        row = {
            "profile_id": profile_dir.name,
            "elapsed_seconds": runtime.get("elapsed_seconds") or 0,
            "unique_papers": unique,
            "llm_attempts": llm.get("attempts_total") or llm.get("total_attempts") or 0,
            "news_candidates": metrics.get("news_candidates") or metrics.get("news_total") or len(issue.get("news") or []),
            "schedule_relevant_failure": bool(failure.get("schedule_relevant")),
            "failure_classification": failure.get("classification") or "",
        }
        row["load_score"] = round(_score(row), 3)
        rows.append(row)
    return rows


def recommend(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (-row["load_score"], row["profile_id"]))
    n = len(ordered)
    tiers: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for idx, row in enumerate(ordered):
        tier = "high" if idx < (n + 2) // 3 else "medium" if idx < (2 * n + 2) // 3 else "low"
        row["tier"] = tier
        tiers[tier].append(row["profile_id"])

    # Pair one high, one medium, one low. Rotate medium/low to avoid repeatedly coupling adjacent ranks.
    schedule: list[list[str]] = []
    width = max(len(tiers["high"]), len(tiers["medium"]), len(tiers["low"]))
    for i in range(width):
        day: list[str] = []
        for tier, offset in (("high", 0), ("medium", 2), ("low", 4)):
            values = tiers[tier]
            if values:
                candidate = values[(i + offset) % len(values)]
                if candidate not in day:
                    day.append(candidate)
        schedule.append(day)
    return {
        "schema_version": 1,
        "policy": "one-high-one-medium-one-low; workload failures only; never auto-mutates source schedule",
        "profiles": ordered,
        "tiers": tiers,
        "recommended_days": schedule,
        "requires_human_review": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recommend, but never automatically apply, the next weekly 21-profile schedule.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    result = recommend(collect(args.data_root))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
