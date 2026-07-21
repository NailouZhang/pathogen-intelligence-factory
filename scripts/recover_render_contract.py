#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pifactory.language_contract import annotate_source_language, sanitize_english_analysis
from pifactory.render import render_site, render_wechat_package
from pifactory.utils import clean_space, dump_json


DEEP_SUPPLEMENTARY_KEYS = {
    "analysis", "analysis_en", "analysis_zh", "elements_en", "elements_zh",
    "summary_en", "summary_zh", "brief_en", "brief_zh", "content_zh",
    "abstract", "abstract_zh", "abstract_original", "full_text",
    "full_text_excerpt", "full_text_sections", "full_text_evidence",
    "wechat_elements_zh", "wechat_summary_zh",
}


def _repair_primary(record: dict[str, Any], *, kind: str) -> bool:
    source_language = annotate_source_language(record, kind=kind)
    existing = record.get("analysis")
    if not isinstance(existing, dict):
        existing = {
            "analysis": dict(record.get("elements_en") or record.get("analysis_en") or {}),
            "brief_en" if kind == "news" else "summary_en": clean_space(
                record.get("brief_en") if kind == "news" else record.get("summary_en")
            ),
        }
    before = json.dumps(existing, ensure_ascii=False, sort_keys=True)
    repaired = sanitize_english_analysis(
        copy.deepcopy(existing),
        kind=kind,
        source_language=source_language,
    )
    record["analysis"] = repaired
    record["elements_en"] = dict(repaired.get("analysis") or {})
    record["analysis_en"] = dict(record["elements_en"])
    if kind == "news":
        record["brief_en"] = clean_space(repaired.get("brief_en"))
    else:
        record["summary_en"] = clean_space(repaired.get("summary_en"))
    return before != json.dumps(repaired, ensure_ascii=False, sort_keys=True)


def _sanitize_supplementary(record: dict[str, Any], *, kind: str) -> list[str]:
    annotate_source_language(record, kind=kind)
    removed: list[str] = []
    for key in sorted(DEEP_SUPPLEMENTARY_KEYS):
        if key in record:
            record.pop(key, None)
            removed.append(key)
    # A supplementary news record may retain the bounded feed excerpt, but a
    # resolved publisher body must never leak into the metadata-only card.
    if kind == "news" and clean_space(record.get("content")):
        record.pop("content", None)
        removed.append("content")
    return removed


def recover_issue(issue: dict[str, Any], audit_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    repaired_primary = 0
    removed_keys: Counter[str] = Counter()
    for record in issue.get("papers") or []:
        repaired_primary += _repair_primary(record, kind="paper")
    for record in issue.get("news") or []:
        repaired_primary += _repair_primary(record, kind="news")
    for record in issue.get("supplementary_papers") or []:
        removed_keys.update(_sanitize_supplementary(record, kind="paper"))
    for record in issue.get("supplementary_news") or []:
        removed_keys.update(_sanitize_supplementary(record, kind="news"))
    audit = {
        "policy_version": "v17.2-deterministic-render-contract-recovery-1",
        "trigger_codes": [
            row.get("code") for row in audit_input.get("findings") or []
            if row.get("severity") == "critical"
        ],
        "primary_language_records_repaired": repaired_primary,
        "supplementary_deep_fields_removed": dict(sorted(removed_keys.items())),
        "supplementary_metadata_preserved": True,
        "rerendered_site": True,
        "rerendered_wechat_package": True,
    }
    issue["render_contract_recovery"] = audit
    return issue, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--audit-json", required=True)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    issue_path = output_dir / "data/latest.json"
    source_bytes = issue_path.read_bytes()
    source_issue = json.loads(source_bytes.decode("utf-8"))
    audit_input = json.loads(Path(args.audit_json).read_text(encoding="utf-8"))
    # Recovery is a publication-view operation. Never overwrite the canonical
    # private issue or remove its full evidence/deep fields.
    render_issue, audit = recover_issue(copy.deepcopy(source_issue), audit_input)
    render_site(render_issue, output_dir)
    cover = render_issue.get("cover") or {}
    if clean_space(cover.get("cover_sha256")):
        render_issue["wechat_content_budget"] = render_wechat_package(render_issue, output_dir, cover)
    audit["source_latest_json_preserved"] = issue_path.read_bytes() == source_bytes
    if not audit["source_latest_json_preserved"]:
        raise SystemExit("Canonical data/latest.json changed during render recovery")
    audit_dir = output_dir / "data/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    dump_json(audit_dir / "render_contract_recovery.json", audit)
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
