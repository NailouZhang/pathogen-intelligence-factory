from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def _compiled(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_all_profiles_cover_each_curated_core_concept_in_every_scholarly_provider():
    for seed_path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        profile = _compiled(seed_path.parent.name)
        sets = profile["query_sets"]
        concepts = sets["core_concepts"]
        assert len(concepts) == 5
        for provider in ("pubmed_core", "europe_pmc_core", "crossref_core", "semantic_scholar_core", "openalex_core"):
            assert len(sets[provider]) == len(concepts), (profile["profile_id"], provider)


def test_arenavirus_prioritizes_five_distinct_public_health_concepts():
    profile = _compiled("arenaviridae")
    terms = profile["query_sets"]["crossref_core"]
    assert terms == [
        "Lassa virus",
        "lymphocytic choriomeningitis virus",
        "Junin virus",
        "Machupo virus",
        "mammarenavirus",
    ]
