#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED: dict[str, list[str]] = {
    "manifest.json": ["src/pifactory/bundled_vocabulary.py"],
    "canonical_vocabulary.json": ["src/pifactory/bundled_vocabulary.py"],
    "profile.json": ["src/pifactory/bundled_vocabulary.py"],
    "retrieval_vocabulary.json": ["src/pifactory/bundled_vocabulary.py"],
    "review_vocabulary.json": ["src/pifactory/bundled_vocabulary.py"],
    "exclusion_vocabulary.json": ["src/pifactory/bundled_vocabulary.py"],
    "translation_glossary.json": ["src/pifactory/bundled_vocabulary.py"],
    "authoritative_sources.json": ["src/pifactory/bundled_vocabulary.py"],
    "validation_cases.json": ["src/pifactory/bundled_vocabulary.py", "scripts/validate_canonical_vocabularies.py"],
}

PROMPTS: dict[str, list[str]] = {
    "relevance_review.md": ["src/pifactory/relevance.py"],
    "research_analysis.md": ["src/pifactory/analysis.py"],
    "review_analysis.md": ["src/pifactory/analysis.py"],
    "news_analysis.md": ["src/pifactory/analysis.py"],
    "field_repair.md": ["src/pifactory/analysis.py"],
    "translate_zh.md": ["src/pifactory/translation.py"],
    "ambiguous_dedup.md": ["src/pifactory/pipeline_v15.py"],
    "profile_bootstrap_v3.md": ["src/pifactory/bootstrap.py"],
    "review_vocabulary_v1.md": ["scripts/refresh_canonical_vocabulary.py"],
    "literature_overview.md": ["src/pifactory/overview.py"],
    "news_overview.md": ["src/pifactory/overview.py"],
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--output", default="VOCABULARY_CONSUMER_AUDIT.json")
    args = parser.parse_args()

    root = Path(args.project_root).resolve()
    errors: list[str] = []
    file_rows: list[dict[str, Any]] = []
    vocabulary_root = root / "config" / "vocabularies"
    profiles = sorted(path for path in vocabulary_root.iterdir() if path.is_dir())
    if len(profiles) != 21:
        errors.append(f"expected 21 vocabulary profiles, found {len(profiles)}")

    for name, consumers in EXPECTED.items():
        missing = [directory.name for directory in profiles if not (directory / name).is_file()]
        unreferenced = [
            consumer for consumer in consumers
            if not (root / consumer).is_file() or name not in (root / consumer).read_text(encoding="utf-8")
        ]
        if missing:
            errors.append(f"{name} missing in {missing}")
        if unreferenced:
            errors.append(f"{name} not referenced by {unreferenced}")

        derivation_failures: list[str] = []
        bundle_version_failures: list[str] = []
        counts: list[int] = []
        hashes: dict[str, str] = {}
        for directory in profiles:
            path = directory / name
            if not path.is_file():
                continue
            value = load_json(path)
            canonical = load_json(directory / "canonical_vocabulary.json")
            fingerprint = canonical.get("semantic_fingerprint")
            bundle_version = canonical.get("bundle_version")
            if name not in {"canonical_vocabulary.json", "manifest.json"}:
                if value.get("derived_from_semantic_fingerprint") != fingerprint:
                    derivation_failures.append(directory.name)
            if name not in {"canonical_vocabulary.json", "manifest.json"}:
                if value.get("bundle_version") != bundle_version:
                    bundle_version_failures.append(directory.name)
            hashes[directory.name] = sha(path)
            if name == "canonical_vocabulary.json":
                topic = value.get("topic_contract") or {}
                counts.append(sum(len(topic.get(key) or []) for key in (
                    "target_entities", "allowed_members", "disease_entities",
                    "qualified_entities", "related_entities", "hard_excluded_entities",
                )))
            elif name == "translation_glossary.json":
                counts.append(len(value.get("translation_glossary") or []))
            elif isinstance(value, dict):
                counts.append(len(value))

        if derivation_failures:
            errors.append(f"{name} derivation fingerprint mismatch: {derivation_failures}")
        if bundle_version_failures:
            errors.append(f"{name} bundle_version mismatch: {bundle_version_failures}")
        if name == "translation_glossary.json" and counts and min(counts) < 1:
            errors.append("translation_glossary.json contains an empty profile glossary")
        file_rows.append({
            "file": name,
            "consumers": consumers,
            "profile_count": len(profiles) - len(missing),
            "minimum_entry_count": min(counts) if counts else 0,
            "profile_sha256": hashes,
        })

    prompt_rows: list[dict[str, Any]] = []
    for name, consumers in PROMPTS.items():
        path = root / "prompts" / name
        refs = [
            consumer for consumer in consumers
            if path.is_file() and (root / consumer).is_file() and name in (root / consumer).read_text(encoding="utf-8")
        ]
        if not path.is_file():
            errors.append(f"prompt missing: {name}")
        if len(refs) != len(consumers):
            errors.append(f"prompt not wired: {name} expected={consumers} actual={refs}")
        prompt_rows.append({
            "prompt": name,
            "purpose": name.rsplit(".", 1)[0],
            "consumers": consumers,
            "wired_consumers": refs,
            "sha256": sha(path) if path.is_file() else "",
        })

    actual_prompts = {path.name for path in (root / "prompts").glob("*.md")}
    expected_prompts = set(PROMPTS)
    orphan_prompts = sorted(actual_prompts - expected_prompts)
    undeployed_prompts = sorted(expected_prompts - actual_prompts)
    if orphan_prompts:
        errors.append(f"orphan prompt files without declared runtime/maintenance consumer: {orphan_prompts}")
    if undeployed_prompts:
        errors.append(f"declared prompt files missing: {undeployed_prompts}")

    report = {
        "policy_version": "v17.4-r2-consumer-prompt-and-derived-view-audit-3",
        "passed": not errors,
        "errors": errors,
        "profile_count": len(profiles),
        "vocabulary_file_count_per_profile": len(EXPECTED),
        "vocabulary_files": file_rows,
        "prompts": prompt_rows,
        "prompt_inventory": {
            "actual": sorted(actual_prompts),
            "declared": sorted(expected_prompts),
            "orphan": orphan_prompts,
        },
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"], "errors": len(errors), "files": len(file_rows),
        "prompts": len(prompt_rows), "profiles": len(profiles),
    }, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
