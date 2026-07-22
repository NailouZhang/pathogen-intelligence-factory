#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT_DEFAULT = Path(__file__).resolve().parents[1]


def _ordered_positions(text: str, tokens: list[str]) -> tuple[bool, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    cursor = -1
    passed = True
    for token in tokens:
        position = text.find(token, cursor + 1)
        rows.append({"token": token, "position": position})
        if position < 0:
            passed = False
        else:
            cursor = position
    return passed, rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=str(ROOT_DEFAULT))
    parser.add_argument("--output", default="PIPELINE_LOGIC_AUDIT.json")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    sys.path.insert(0, str(root / "src"))

    from pifactory.bundled_vocabulary import load_bundled_vocabulary, validate_bundled_vocabulary

    errors: list[str] = []
    pipeline_path = root / "src" / "pifactory" / "pipeline_v15.py"
    pipeline = pipeline_path.read_text(encoding="utf-8")

    literature_tokens = [
        "plan = build_query_plan(profile",
        "paper_candidates = candidate_filter_papers",
        "papers = dedup_papers",
        "llm_review_ambiguous_duplicates",
        "_review_paper_batch(preexisting_evidence",
        "complete_scholarly_work(",
        "_review_paper_batch(completed",
        "_identity_gate_paper_batch(reviewed",
        "_analyze_translate_paper(item)",
        "overview = build_overviews(",
        "render_site(issue",
        "render_wechat_package(issue",
    ]
    news_tokens = [
        "plan = build_query_plan(profile",
        "news_candidates = candidate_filter_news",
        "news = dedup_news",
        "news = final_filter(",
        "resolve_and_extract_news(http",
        "filter_post_enrichment(news, news_profile, \"news\")",
        "analyze_news(article",
        "overview = build_overviews(",
        "render_site(issue",
        "render_wechat_package(issue",
    ]
    literature_ok, literature_rows = _ordered_positions(pipeline, literature_tokens)
    news_ok, news_rows = _ordered_positions(pipeline, news_tokens)
    if not literature_ok:
        errors.append("literature stage order or required call is missing")
    if not news_ok:
        errors.append("news stage order or required call is missing")

    runtime_sources = {
        "pipeline_v15.py": pipeline,
        "vocabulary_lifecycle.py": (root / "src" / "pifactory" / "vocabulary_lifecycle.py").read_text(encoding="utf-8"),
        "translation.py": (root / "src" / "pifactory" / "translation.py").read_text(encoding="utf-8"),
        "render.py": (root / "src" / "pifactory" / "render.py").read_text(encoding="utf-8"),
        "public_display.py": (root / "src" / "pifactory" / "public_display.py").read_text(encoding="utf-8"),
    }
    required_runtime_contracts = {
        "canonical vocabulary application": ("vocabulary_lifecycle.py", "apply_bundled_profile"),
        "review vocabulary lifecycle": ("pipeline_v15.py", "ensure_review_vocabulary"),
        "post-retrieval relevance rules": ("pipeline_v15.py", "build_relevance_rules"),
        "publication date gate": ("pipeline_v15.py", "assess_publication_date"),
        "paper cliff guard": ("pipeline_v15.py", "apply_relevance_cliff_guard"),
        "translation glossary consumption": ("translation.py", 'profile.get("translation_glossary")'),
        "translation structured LLM fallback": ("translation.py", "translation_single_structured_rescue"),
        "translation plain-text LLM rescue": ("translation.py", "_llm_plain_translation_rescue"),
        "translation common response-shape parser": ("translation.py", "_translation_value"),
        "translation display fallback": ("pipeline_v15.py", "apply_translation_display_fallback"),
        "source status audit": ("pipeline_v15.py", "source_status"),
        "wechat package schema": ("pipeline_v15.py", "render_wechat_package"),
        "public backend-wording sanitizer": ("public_display.py", "sanitize_public_text"),
        "public display structure sanitizer": ("render.py", "build_display_issue"),
        "public supplementary notice fields removed": ("public_display.py", '"notice_zh"'),
    }
    contract_rows = []
    for name, (source_name, token) in required_runtime_contracts.items():
        present = token in runtime_sources[source_name]
        contract_rows.append({"contract": name, "source": source_name, "token": token, "present": present})
        if not present:
            errors.append(f"runtime contract missing: {name} ({source_name}:{token})")

    schedule = yaml.safe_load((root / "config" / "weekly_virus_schedule.yaml").read_text(encoding="utf-8"))
    scheduled = [profile_id for day in (schedule.get("week") or {}).values() for profile_id in (day or [])]
    profile_ids = sorted(path.parent.name for path in (root / "profiles").glob("*/seed.yaml"))
    vocabulary_ids = sorted(path.name for path in (root / "config" / "vocabularies").iterdir() if path.is_dir())
    if len(scheduled) != 21 or len(set(scheduled)) != 21:
        errors.append(f"schedule must contain 21 unique profile IDs; got total={len(scheduled)} unique={len(set(scheduled))}")
    if sorted(scheduled) != profile_ids:
        errors.append("schedule profile IDs differ from profiles/*/seed.yaml")
    if profile_ids != vocabulary_ids:
        errors.append("profile IDs differ from config/vocabularies directories")
    if schedule.get("timezone") != "Asia/Shanghai" or schedule.get("run_time") != "02:00":
        errors.append("schedule must remain Asia/Shanghai 02:00")

    profile_rows: list[dict[str, Any]] = []
    for profile_id in profile_ids:
        valid, profile_errors, manifest = validate_bundled_vocabulary(root, profile_id, semantic=True)
        bundle = load_bundled_vocabulary(root, profile_id) if valid else {}
        profile = bundle.get("profile") or {}
        glossary = profile.get("translation_glossary") or []
        review_vocabulary = profile.get("vocabulary") or {}
        row = {
            "profile_id": profile_id,
            "valid": valid,
            "errors": profile_errors,
            "bundle_version": manifest.get("bundle_version") if isinstance(manifest, dict) else "",
            "translation_glossary_entries": len(glossary),
            "review_vocabulary_categories": sorted(review_vocabulary),
            "target_entities": len(((profile.get("topic_contract") or {}).get("target_entities") or [])),
            "related_entities": len(((profile.get("topic_contract") or {}).get("related_entities") or [])),
        }
        profile_rows.append(row)
        if not valid:
            errors.append(f"semantic vocabulary validation failed: {profile_id}: {profile_errors}")
        if not glossary:
            errors.append(f"translation glossary empty: {profile_id}")
        required_review_categories = {"identity_anchor_terms", "member_identity_terms", "disease_identity_terms", "hard_exclusion_terms"}
        if not required_review_categories.issubset(review_vocabulary):
            errors.append(f"review vocabulary categories incomplete: {profile_id}")

    prompt_consumers = {
        "ambiguous_dedup.md": "pipeline_v15.py",
        "relevance_review.md": "relevance.py",
        "research_analysis.md": "analysis.py",
        "review_analysis.md": "analysis.py",
        "news_analysis.md": "analysis.py",
        "field_repair.md": "analysis.py",
        "translate_zh.md": "translation.py",
        "literature_overview.md": "overview.py",
        "news_overview.md": "overview.py",
        "profile_bootstrap_v3.md": "bootstrap.py",
        "review_vocabulary_v1.md": "refresh_canonical_vocabulary.py",
    }
    prompt_rows: list[dict[str, Any]] = []
    for prompt, consumer_name in prompt_consumers.items():
        prompt_path = root / "prompts" / prompt
        candidates = list((root / "src" / "pifactory").glob(consumer_name)) + list((root / "scripts").glob(consumer_name))
        wired = any(prompt in candidate.read_text(encoding="utf-8") for candidate in candidates if candidate.is_file())
        present = prompt_path.is_file()
        prompt_rows.append({"prompt": prompt, "present": present, "consumer": consumer_name, "wired": wired})
        if not present or not wired:
            errors.append(f"prompt not active: {prompt} -> {consumer_name}")

    report = {
        "policy_version": "v17.4-r4-end-to-end-logic-audit-2",
        "passed": not errors,
        "errors": errors,
        "pipeline_file": str(pipeline_path),
        "literature_stage_order": {"passed": literature_ok, "stages": literature_rows},
        "news_stage_order": {"passed": news_ok, "stages": news_rows},
        "runtime_contracts": contract_rows,
        "schedule": {
            "schema_version": schedule.get("schema_version"),
            "timezone": schedule.get("timezone"),
            "run_time": schedule.get("run_time"),
            "profiles": scheduled,
            "unique_profiles": len(set(scheduled)),
        },
        "profile_count": len(profile_rows),
        "profiles": profile_rows,
        "prompts": prompt_rows,
        "scope_note": "Engineering/static and offline semantic audit only; no live network, LLM, GitHub Actions, Runner or WeChat API claim.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "errors": len(errors), "profiles": len(profile_rows)}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
