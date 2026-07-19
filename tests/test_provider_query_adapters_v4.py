from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_all_21_profiles_have_natural_chinese_news_aliases_and_lean_policy():
    seed_paths = sorted((ROOT / "profiles").glob("*/seed.yaml"))
    assert len(seed_paths) == 21
    for path in seed_paths:
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert seed.get("news_identity_terms_zh"), seed["profile_id"]
        strategy = seed.get("search_strategy") or {}
        concepts = strategy.get("concepts") or []
        assert len(concepts) == 5, seed["profile_id"]
        assert all(x.get("scholarly") and x.get("news_en") and x.get("news_zh") for x in concepts)
        policy = seed.get("retrieval_policy") or {}
        assert policy.get("execute_all_query_chunks") is False
        assert policy.get("provider_expansion_first") is True
        assert policy.get("candidate_gate") == "python_accept_or_llm_review"
        assert policy.get("content_enrichment_stage") == "after_cross_source_dedup_dynamic_batches"
        assert policy.get("llm_review_mode") == "ambiguous_only"
