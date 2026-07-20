#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

WORKLOAD_REASONS = {
    "runtime_timeout",
    "candidate_volume_excessive",
    "llm_attempts_excessive",
    "fulltext_volume_excessive",
}

PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("runtime_timeout", ("timed out", "timeout", "exit status 124", "terminated", "signal term", "profile timeout")),
    ("provider_authentication_failure", ("401", "403", "unauthorized", "invalid api key", "authentication", "permission denied")),
    ("provider_quota_failure", ("quota", "insufficient balance", "credit", "billing", "exceeded your current quota")),
    ("provider_rate_limited", ("429", "rate limit", "rate_limited", "retry-after")),
    ("network_failure", ("ssl connection", "connection reset", "connection refused", "name or service not known", "read timed out", "connect timeout")),
    ("schema_or_validation_failure", ("schema", "validationerror", "manifest", "invalid json", "jsondecodeerror")),
    ("dispatch_failure", ("repository_dispatch", "wechat dispatch failed", "dispatches")),
    ("runner_failure", ("runner listener", "listening for jobs", "session for this runner", "self-hosted")),
    ("code_failure", ("traceback", "assertionerror", "typeerror", "keyerror", "valueerror", "runtimeerror")),
]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify(log_text: str, output_dir: Path | None, exit_status: int) -> dict[str, Any]:
    text = log_text.casefold()
    evidence: list[str] = []
    reason = "unknown_failure"

    if exit_status in {124, 137, 143}:
        reason = "runtime_timeout"
        evidence.append(f"exit_status={exit_status}")
    else:
        for candidate, needles in PATTERNS:
            if any(needle in text for needle in needles):
                reason = candidate
                evidence.append(f"log_pattern={candidate}")
                break

    metrics: dict[str, Any] = {}
    if output_dir:
        audit_dir = output_dir / "data" / "audit"
        funnel = _read_json(audit_dir / "retrieval_funnel.json")
        selection = _read_json(audit_dir / "literature_selection.json")
        completion = _read_json(audit_dir / "literature_content_completion.json")
        provider = _read_json(audit_dir / "llm_provider_usage.json")
        runtime = _read_json(audit_dir / "runtime_budget.json")
        issue = _read_json(output_dir / "data" / "latest.json")

        raw_papers = _as_int((funnel.get("papers") or {}).get("raw") or funnel.get("papers_raw") or funnel.get("scholarly_raw"))
        unique_papers = _as_int((funnel.get("papers") or {}).get("deduplicated") or funnel.get("papers_deduplicated") or funnel.get("paper_unique"))
        attempted = _as_int(selection.get("analysis_attempted") or selection.get("analysis_attempts"))
        completed = _as_int(completion.get("processed") or completion.get("completion_processed"))
        llm_calls = _as_int(provider.get("attempts_total") or provider.get("total_attempts"))
        elapsed = float(runtime.get("elapsed_seconds") or 0.0)
        metrics = {
            "raw_papers": raw_papers,
            "unique_papers": unique_papers,
            "analysis_attempts": attempted,
            "fulltext_completion_processed": completed,
            "llm_attempts": llm_calls,
            "elapsed_seconds": elapsed,
            "issue_available": bool(issue),
        }

        if reason == "unknown_failure":
            if runtime.get("global_stop_reason") == "finalization_reserve_entered" or elapsed >= 0.95 * 150 * 60:
                reason = "runtime_timeout"
                evidence.append("runtime_budget_near_hard_limit")
            elif attempted >= 100:
                reason = "llm_attempts_excessive"
                evidence.append(f"analysis_attempts={attempted}")
            elif completed >= 150:
                reason = "fulltext_volume_excessive"
                evidence.append(f"fulltext_completion_processed={completed}")
            elif unique_papers >= 750 or raw_papers >= 1500:
                reason = "candidate_volume_excessive"
                evidence.append(f"raw={raw_papers},unique={unique_papers}")

    schedule_relevant = reason in WORKLOAD_REASONS
    return {
        "schema_version": 1,
        "classification": reason,
        "schedule_relevant": schedule_relevant,
        "schedule_action": "increase_load_tier_and_avoid_other_heavy_profiles" if schedule_relevant else "do_not_reschedule_fix_root_cause",
        "exit_status": exit_status,
        "evidence": evidence,
        "metrics": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify profile failure without hiding infrastructure faults as schedule problems.")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--exit-status", type=int, required=True)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()

    log_text = args.log.read_text(encoding="utf-8", errors="replace") if args.log.is_file() else ""
    result = classify(log_text, args.output_dir, args.exit_status)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
