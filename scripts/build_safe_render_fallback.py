#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pifactory.language_contract import annotate_source_language
from pifactory.render import render_site, render_wechat_package
from pifactory.utils import clean_space, dump_json


DROP_KEYS = {
    "analysis", "analysis_en", "analysis_zh", "elements_en", "elements_zh",
    "summary_en", "summary_zh", "brief_en", "brief_zh", "content", "content_zh",
    "abstract", "abstract_zh", "abstract_original", "full_text", "full_text_excerpt",
    "full_text_sections", "full_text_evidence", "wechat_elements_zh", "wechat_summary_zh",
}


def metadata_only(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
    output = copy.deepcopy(record)
    annotate_source_language(output, kind=kind)
    for key in DROP_KEYS:
        output.pop(key, None)
    return output


def build_safe_issue(issue: dict[str, Any], source_audit: str) -> dict[str, Any]:
    safe = copy.deepcopy(issue)
    safe["supplementary_papers"] = [
        metadata_only(row, kind="paper")
        for row in (issue.get("papers") or []) + (issue.get("supplementary_papers") or [])
    ]
    safe["supplementary_news"] = [
        metadata_only(row, kind="news")
        for row in (issue.get("news") or []) + (issue.get("supplementary_news") or [])
    ]
    safe["papers"] = []
    safe["news"] = []
    safe["overview"] = {}
    safe["render_safe_fallback"] = {
        "policy_version": "v17.2-metadata-only-last-resort-2",
        "reason": "A rendered output contract remained unsafe after deterministic recovery.",
        "source_audit": source_audit,
        "primary_records_quarantined_to_metadata": True,
        "source_latest_json_preserved": True,
        "site_regenerated": True,
        "wechat_package_regenerated": True,
    }
    return safe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-audit", required=True)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    issue = json.loads((output_dir / "data/latest.json").read_text(encoding="utf-8"))
    safe = build_safe_issue(issue, str(Path(args.source_audit).name))
    render_site(safe, output_dir)
    cover = issue.get("cover") or {}
    if not clean_space(cover.get("cover_sha256")):
        existing_manifest = output_dir / "wechat-package/manifest.json"
        if existing_manifest.is_file():
            cover = (json.loads(existing_manifest.read_text(encoding="utf-8")).get("cover") or {})
            cover["cover_sha256"] = cover.get("sha256")
    safe["wechat_content_budget"] = render_wechat_package(safe, output_dir, cover)
    audit_dir = output_dir / "data/audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    dump_json(audit_dir / "render_safe_fallback.json", safe["render_safe_fallback"])
    print(json.dumps(safe["render_safe_fallback"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
