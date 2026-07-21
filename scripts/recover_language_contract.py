#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pifactory.language_contract import annotate_source_language, sanitize_english_analysis
from pifactory.render import render_site, render_wechat_package
from pifactory.storage import write_issue
from pifactory.utils import clean_space, dump_json


def _repair_record(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
    source_language = annotate_source_language(record, kind="paper" if kind != "news" else "news")
    analysis_kind = "news" if kind == "news" else "paper"
    existing = record.get("analysis")
    if isinstance(existing, dict) and isinstance(existing.get("analysis"), dict):
        payload = dict(existing)
    else:
        fields = record.get("elements_en") or record.get("analysis_en") or {}
        payload = {"analysis": dict(fields) if isinstance(fields, dict) else {}}
        if analysis_kind == "news":
            payload["brief_en"] = clean_space(record.get("brief_en") or record.get("summary_en"))
        else:
            payload["summary_en"] = clean_space(record.get("summary_en"))
    record["analysis"] = sanitize_english_analysis(
        payload,
        kind=analysis_kind,
        source_language=source_language,
    )
    record["elements_en"] = dict((record.get("analysis") or {}).get("analysis") or {})
    record["analysis_en"] = dict(record["elements_en"])
    if analysis_kind == "news":
        record["brief_en"] = clean_space((record.get("analysis") or {}).get("brief_en"))
    else:
        record["summary_en"] = clean_space((record.get("analysis") or {}).get("summary_en"))
    return record


def repair_issue(issue: dict[str, Any]) -> dict[str, Any]:
    repaired = 0
    for key in ("papers", "supplementary_papers"):
        for record in issue.get(key) or []:
            before = json.dumps(record.get("analysis") or {}, ensure_ascii=False, sort_keys=True)
            _repair_record(record, kind="paper")
            after = json.dumps(record.get("analysis") or {}, ensure_ascii=False, sort_keys=True)
            repaired += before != after
    for key in ("news", "supplementary_news"):
        for record in issue.get(key) or []:
            before = json.dumps(record.get("analysis") or {}, ensure_ascii=False, sort_keys=True)
            _repair_record(record, kind="news")
            after = json.dumps(record.get("analysis") or {}, ensure_ascii=False, sort_keys=True)
            repaired += before != after
    issue["language_contract_recovery"] = {
        "policy_version": "v17.1-post-render-language-recovery-1",
        "records_repaired": repaired,
        "rerendered": True,
    }
    return issue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    latest = output_dir / "data" / "latest.json"
    issue = json.loads(latest.read_text(encoding="utf-8"))
    issue = repair_issue(issue)
    render_site(issue, output_dir)
    cover = issue.get("cover") or {}
    if not clean_space(cover.get("cover_sha256")):
        raise SystemExit("language recovery cannot rerender WeChat package without cover metadata")
    budget = render_wechat_package(issue, output_dir, cover)
    issue["wechat_content_budget"] = budget
    write_issue(output_dir, issue)
    audit_dir = output_dir / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    dump_json(audit_dir / "language_contract_recovery.json", issue["language_contract_recovery"])
    print(json.dumps(issue["language_contract_recovery"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
