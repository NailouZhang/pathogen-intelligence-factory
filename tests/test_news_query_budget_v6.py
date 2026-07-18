from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def test_large_family_uses_five_distinct_news_concepts_without_request_explosion():
    seed = yaml.safe_load((ROOT / "profiles/arenaviridae/seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    profile = compile_profile_queries(deterministic_profile(seed, docs))
    sets = profile["query_sets"]
    assert len(sets["general_news_single_en"]) == 5
    assert len(sets["gdelt_core"]) == 5
    assert len(sets["reliefweb_core"]) == 5
    assert len({q.casefold() for q in sets["general_news_single_en"]}) == 5


def test_context_query_multiplication_is_disabled():
    for seed_path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        seed = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
        policy = seed.get("retrieval_policy") or {}
        assert policy.get("execute_all_query_chunks") is False
        assert policy.get("max_core_concepts") == 5
        assert policy.get("content_enrichment_stage") == "after_top_n_selection"
