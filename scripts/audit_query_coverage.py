#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import yaml

from pifactory.profile_contract import deterministic_profile
from pifactory.query_plan import compile_profile_queries


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile all provider-specific queries and report coverage without network calls.")
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    rows = []
    for seed_path in sorted((root / "profiles").glob("*/seed.yaml")):
        seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        docs = [{"url": x["url"], "usable": True, "sha256": "offline-audit"} for x in seed["authoritative_sources"]]
        profile = compile_profile_queries(deterministic_profile(seed, docs))
        provider_keys = (
            "pubmed_core", "europe_pmc_core", "crossref_core",
            "semantic_scholar_core", "openalex_core", "general_news_en",
            "general_news_zh", "gdelt_core", "reliefweb_core",
            "pubmed_supplemental", "europe_pmc_supplemental", "crossref_supplemental",
            "semantic_scholar_supplemental", "openalex_supplemental",
        )
        counts = {key: len(profile["query_sets"].get(key) or []) for key in provider_keys}
        concepts = profile["query_sets"].get("core_concepts") or []
        rows.append({
            "profile_id": seed["profile_id"],
            "status": profile["status"],
            "qualified_terms": len(profile["vocabulary"]["qualified_identity_terms"]),
            "core_concepts": concepts,
            "core_concept_count": len(concepts),
            "provider_query_count": sum(counts.values()),
            "query_counts": counts,
            "controlled_supplemental_terms": profile["query_sets"].get("controlled_supplemental_terms") or [],
            "controlled_supplemental_query_count": sum(
                counts.get(key, 0) for key in (
                    "pubmed_supplemental", "europe_pmc_supplemental", "crossref_supplemental",
                    "semantic_scholar_supplemental", "openalex_supplemental",
                )
            ),
            "validation": profile["validation"],
        })
    payload = {
        "profiles": rows,
        "profile_count": len(rows),
        "ready_count": sum(x["status"] == "ready" for x in rows),
        "all_ready": all(x["status"] == "ready" for x in rows),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    print(text)
    if not payload["all_ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
