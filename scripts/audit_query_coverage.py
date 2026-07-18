#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries


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
        counts = {key: len(value or []) for key, value in profile["query_sets"].items()}
        rows.append({
            "profile_id": seed["profile_id"],
            "status": profile["status"],
            "qualified_terms": len(profile["vocabulary"]["qualified_identity_terms"]),
            "query_counts": counts,
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
