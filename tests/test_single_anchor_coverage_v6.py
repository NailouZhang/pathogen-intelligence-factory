from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries
from src.pifactory.utils import clean_space

ROOT = Path(__file__).resolve().parents[1]


def _compiled(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def _safe_terms(profile):
    terms = []
    for key in ("identity_anchor_terms", "member_identity_terms", "disease_identity_terms"):
        for item in profile["vocabulary"].get(key, []):
            if item.get("safe_to_use_alone", key == "member_identity_terms"):
                terms.append(clean_space(item.get("term")))
    return [x for x in terms if x]


def test_all_21_profiles_have_per_anchor_pubmed_and_epmc_queries():
    for seed_path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        profile = _compiled(seed_path.parent.name)
        safe = _safe_terms(profile)
        pubmed = "\n".join(profile["query_sets"]["pubmed_single_anchor_exact"]).casefold()
        epmc = "\n".join(profile["query_sets"]["europe_pmc_single_anchor_exact"]).casefold()
        assert safe
        for term in safe:
            assert term.casefold() in pubmed, (profile["profile_id"], term, "pubmed")
            assert term.casefold() in epmc, (profile["profile_id"], term, "epmc")
        assert profile["validation"]["single_anchor_coverage_check"]["passed"]


def test_rare_arenavirus_members_are_not_only_grouped():
    profile = _compiled("arenaviridae")
    singles = profile["query_sets"]["pubmed_single_anchor_exact"]
    assert any('"Chapare virus"[Title/Abstract]' == q for q in singles)
    assert any('"Lujo virus"[Title/Abstract]' == q for q in singles)


def test_ambiguous_sftsv_is_not_a_standalone_news_query():
    profile = _compiled("sftsv")
    queries = profile["query_sets"]["general_news_single_zh"]
    assert '"SFTSV"' not in queries
