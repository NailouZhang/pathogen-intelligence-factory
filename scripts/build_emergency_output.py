#!/usr/bin/env python3
"""Build a dependency-light, metadata-only site and WeChat package.

This is the final continuity guard. It does not publish abstracts, full text,
structured analysis, or model output. It preserves the original latest.json and
uses only profile/title/date metadata plus the already generated cover image.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def visible_count(value: str) -> int:
    parser = VisibleTextParser()
    parser.feed(value)
    return len("".join(parser.parts))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--reason", default="final_output_contract_recovery")
    args = parser.parse_args()
    output = Path(args.output_dir).expanduser().resolve()
    issue = load_json(output / "data/latest.json", {})
    package = output / "wechat-package"
    site = output / "site"
    package.mkdir(parents=True, exist_ok=True)
    (site / "assets").mkdir(parents=True, exist_ok=True)

    profile_id = str(issue.get("profile_id") or "unknown-profile")
    issue_date = str(issue.get("issue_date") or issue.get("report_date") or "unknown-date")
    issue_id = str(issue.get("issue_id") or f"{profile_id}-{issue_date}")
    title_zh = str(issue.get("title_zh") or issue.get("title") or f"{profile_id} 病原情报")
    title_en = str(issue.get("title_en") or f"{profile_id} pathogen intelligence")
    generated_at = str(issue.get("generated_at") or "")

    existing_manifest = load_json(package / "manifest.json", {})
    cover_file = package / str((existing_manifest.get("cover") or {}).get("file") or "cover.jpg")
    if not cover_file.is_file():
        candidates = [site / "assets/cover.jpg", output / "cover.jpg"]
        source = next((path for path in candidates if path.is_file()), None)
        if source is None:
            raise SystemExit("Emergency output cannot continue without the already generated cover image")
        shutil.copy2(source, package / "cover.jpg")
        cover_file = package / "cover.jpg"
    site_cover = site / "assets/cover.jpg"
    if not site_cover.is_file():
        shutil.copy2(cover_file, site_cover)
    cover_hash = sha256(cover_file)

    site_html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title_zh)}</title></head><body><main><img src="assets/cover.jpg" alt="cover" style="max-width:100%;height:auto"><h1>{html.escape(title_zh)}</h1><p>{html.escape(issue_date)}</p><p>本期输出触发确定性安全恢复。完整来源数据与审计记录已保存在私有 Factory 数据分支，本公开页面仅展示安全元数据。</p><h2>{html.escape(title_en)}</h2><p>This issue used deterministic safe-output recovery. Complete source data and audit records remain in the private Factory data branch; this public page contains metadata only.</p></main></body></html>'''
    (site / "index.html").write_text(site_html, encoding="utf-8")

    article = f'''<section style="font-family:Arial,'Noto Sans CJK SC',sans-serif;line-height:1.75;color:#333"><h1>{html.escape(title_zh)}</h1><p>{html.escape(issue_date)}</p><p>本期自动处理已完成，但最终展示契约触发安全恢复。为避免多语种或结构异常导致错误内容进入公众号，本草稿仅保留标题与日期。完整审计信息保存在私有 Factory 仓。</p></section>'''
    (package / "article.html").write_text(article, encoding="utf-8")
    visible = visible_count(article)
    budget = {
        "policy_version": "v17-wechat-visible-text-budget-audit-only-1",
        "max_visible_chars": 48000,
        "minimum_full_papers": 10,
        "minimum_primary_papers": 10,
        "minimum_supplementary_papers": 0,
        "minimum_supplementary_news": 0,
        "minimum_main_news": 10,
        "visible_chars_before": visible,
        "visible_chars_after": visible,
        "within_budget": True,
        "primary_papers_total": 0,
        "primary_papers_displayed": 0,
        "primary_papers_omitted": 0,
        "supplementary_papers_total": 0,
        "supplementary_papers_displayed": 0,
        "supplementary_papers_omitted": 0,
        "supplementary_news_total": 0,
        "supplementary_news_displayed": 0,
        "supplementary_news_omitted": 0,
        "main_news_total": 0,
        "main_news_displayed": 0,
        "main_news_omitted": 0,
        "operational_notice_rendered": False,
        "budget_notice_rendered": False,
        "full_catalog_preserved_in_source_data": True,
        "main_news_elements_preserved": True,
        "main_news_elements_preserved_for_displayed_cards": True,
        "emergency_metadata_only": True,
        "reason": args.reason,
    }
    dump_json(package / "content-budget-audit.json", budget)
    manifest = {
        "schema_version": 2,
        "contract": "pathogen-wechat-package/v2",
        "publish_key": issue_id,
        "profile_id": profile_id,
        "report_date": issue_date,
        "title": f"{title_zh}｜{issue_date}",
        "digest": "本期自动流程已完成，展示内容因安全契约触发元数据级恢复；完整审计保存在私有 Factory 仓。",
        "content_file": "article.html",
        "content_source_url": str(existing_manifest.get("content_source_url") or ""),
        "show_cover_pic": 1,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
        "images": [],
        "cover": {
            "file": cover_file.name,
            "sha256": cover_hash,
            "asset_key": profile_id,
            "generator": "existing-cover-emergency-output",
            "profile_fingerprint": (existing_manifest.get("cover") or {}).get("profile_fingerprint"),
        },
        "source": {
            "profile_id": profile_id,
            "issue_id": issue_id,
            "generated_at": generated_at,
            "emergency_metadata_only": True,
            "full_catalog_preserved_in_source_data": True,
        },
    }
    dump_json(package / "manifest.json", manifest)
    audit = {
        "policy_version": "v17.2-emergency-output-continuity-1",
        "reason": args.reason,
        "site": str(site / "index.html"),
        "wechat_package": str(package),
        "source_latest_json_preserved": True,
        "cover_sha256": cover_hash,
    }
    dump_json(output / "data/audit/render_emergency_output.json", audit)
    print(json.dumps(audit, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
