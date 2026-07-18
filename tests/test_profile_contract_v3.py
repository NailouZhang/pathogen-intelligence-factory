from pathlib import Path

import yaml

from pifactory.profile_contract import deterministic_profile, validate_profile
from pifactory.query_plan import compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def test_every_profile_compiles_ready_without_llm():
    for seed_path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        docs = [{"url": x["url"], "usable": True, "sha256": "offline-test"} for x in seed["authoritative_sources"]]
        profile = compile_profile_queries(deterministic_profile(seed, docs))
        valid, issues = validate_profile(profile, seed)
        assert valid, (seed["profile_id"], issues)
        assert profile["status"] == "ready", (seed["profile_id"], profile.get("blocking_issues"))
        assert profile["validation"]["branch_anchor_check"]["passed"]
        assert profile["post_retrieval_relevance_rules"]["reject_if_only_context_terms"] is True


def test_forbidden_term_overrides_candidate_anchor():
    seed_path = ROOT / "profiles/avian_influenza/seed.yaml"
    seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True} for x in seed["authoritative_sources"]]
    profile = deterministic_profile(seed, docs)
    bird_flu = next(x for x in profile["vocabulary"]["identity_anchor_terms"] if x["term"] == "bird flu")
    assert bird_flu["safe_to_use_alone"] is False
