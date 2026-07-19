from pathlib import Path

import yaml

from pifactory.profile_contract import deterministic_profile
from pifactory.query_plan import build_query_plan, compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def compiled(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_provider_native_queries_are_lean_and_not_cross_contaminated():
    sets = compiled("respiratory_syncytial_virus")["query_sets"]
    assert len(sets["pubmed_core"]) == 5
    assert all("[Title/Abstract]" not in q for q in sets["pubmed_core"])
    assert all("FIRST_PDATE" not in q for q in sets["europe_pmc_core"])
    assert sets["europe_pmc_core"] == [
        concept["scholarly"] for concept in sets["core_concepts"]
    ]
    assert all("TITLE_ABS:" not in q for q in sets["europe_pmc_core"])
    assert all("[Title/Abstract]" not in q for q in sets["semantic_scholar_core"])
    assert all(" AND " not in q and " OR " not in q for q in sets["semantic_scholar_core"])
    assert all("[" not in q and "]" not in q for q in sets["crossref_core"])
    assert sets["openalex_exact"] == []
    assert sets["openalex_normal"] == sets["openalex_core"]


def test_each_provider_has_at_most_five_direct_concepts():
    for profile_id in [p.name for p in (ROOT / "profiles").iterdir() if p.is_dir()]:
        sets = compiled(profile_id)["query_sets"]
        for key in (
            "pubmed_core", "europe_pmc_core", "crossref_core",
            "semantic_scholar_core", "openalex_core", "general_news_en",
            "general_news_zh", "gdelt_core", "reliefweb_core",
        ):
            assert 1 <= len(sets[key]) <= 5, (profile_id, key, len(sets[key]))


def test_audit_plan_contains_one_row_per_provider_query():
    profile = compiled("arenaviridae")
    plan = build_query_plan(profile, max_groups=1000)
    provider_keys = {
        "pubmed": "pubmed_core",
        "pubmed_supplemental": "pubmed_supplemental",
        "europe_pmc": "europe_pmc_core",
        "europe_pmc_supplemental": "europe_pmc_supplemental",
        "crossref": "crossref_core",
        "crossref_supplemental": "crossref_supplemental",
        "semantic_scholar": "semantic_scholar_core",
        "semantic_scholar_supplemental": "semantic_scholar_supplemental",
        "openalex": "openalex_core",
        "openalex_supplemental": "openalex_supplemental",
        "news_en": "general_news_en",
        "news_zh": "general_news_zh",
        "gdelt": "gdelt_core",
        "reliefweb": "reliefweb_core",
    }
    expected = sum(len(profile["query_sets"][key]) for key in provider_keys.values())
    assert len(plan) == expected
    assert all(row.get("concept_id") for row in plan)
