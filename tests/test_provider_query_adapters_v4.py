from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def compiled(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_provider_queries_are_not_pubmed_strings_reused_everywhere():
    profile = compiled("sars_cov_2")
    sets = profile["query_sets"]
    assert all("[Title/Abstract]" not in q for q in sets["semantic_scholar"] + sets["crossref"] + sets["openalex_exact"] + sets["openalex_normal"])
    assert all(" AND " not in q and " OR " not in q for q in sets["crossref"])


def test_all_21_profiles_have_natural_chinese_news_aliases_and_retrieval_policy():
    seed_paths = sorted((ROOT / "profiles").glob("*/seed.yaml"))
    assert len(seed_paths) == 21
    for path in seed_paths:
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert seed.get("news_identity_terms_zh"), seed["profile_id"]
        policy = seed.get("retrieval_policy") or {}
        assert policy.get("execute_all_query_chunks") is True
        assert policy.get("candidate_gate") == "accept_or_review"
