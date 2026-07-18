#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.pifactory.profile_contract import deterministic_profile, validate_profile
from src.pifactory.query_plan import compile_profile_queries


def main() -> int:
    failures=[]
    for seed_path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        seed=yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        docs=[{"url":x["url"],"usable":True,"sha256":"offline-validation"} for x in seed["authoritative_sources"]]
        profile=compile_profile_queries(deterministic_profile(seed,docs))
        valid,issues=validate_profile(profile,seed)
        print(f"{seed['profile_id']}: status={profile['status']} sources={len(seed['authoritative_sources'])} queries={sum(len(v) for v in profile['query_sets'].values())}")
        if not valid or profile["status"] != "ready": failures.append((seed["profile_id"],issues,profile.get("blocking_issues")))
    if failures:
        print(failures,file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
