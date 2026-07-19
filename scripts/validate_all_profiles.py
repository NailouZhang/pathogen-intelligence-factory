#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pifactory.profile_contract import deterministic_profile, validate_profile
from pifactory.query_plan import compile_profile_queries


def main() -> int:
    failures=[]
    for seed_path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        seed=yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        docs=[{"url":x["url"],"usable":True,"sha256":"offline-validation"} for x in seed["authoritative_sources"]]
        profile=compile_profile_queries(deterministic_profile(seed,docs))
        valid,issues=validate_profile(profile,seed)
        provider_keys=("pubmed_core","europe_pmc_core","crossref_core","semantic_scholar_core","openalex_core","general_news_en","general_news_zh","gdelt_core","reliefweb_core","pubmed_supplemental","europe_pmc_supplemental","crossref_supplemental","semantic_scholar_supplemental","openalex_supplemental")
        query_count=sum(len(profile["query_sets"].get(k) or []) for k in provider_keys)
        print(f"{seed['profile_id']}: status={profile['status']} sources={len(seed['authoritative_sources'])} core_concepts={len(profile['query_sets'].get('core_concepts') or [])} supplemental_terms={len(profile['query_sets'].get('controlled_supplemental_terms') or [])} provider_queries={query_count}")
        if not valid or profile["status"] != "ready": failures.append((seed["profile_id"],issues,profile.get("blocking_issues")))
    if failures:
        print(failures,file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
