from pathlib import Path

import yaml

from pifactory.profile_contract import deterministic_profile
from pifactory.query_plan import build_query_plan, compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def compiled(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "x"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_query_plan_uses_database_specific_anchored_queries():
    profile = compiled("sftsv")
    plan = build_query_plan(profile)
    assert plan
    assert all("pubmed_query" in x for x in plan)
    assert any("severe fever with thrombocytopenia syndrome virus" in x["pubmed_query"] for x in plan)
    assert all("{{DATE_FILTER}}" not in x["pubmed_query"] for x in plan)
    assert all(len(x["news_query"]) <= 350 for x in plan if x["news_query"])


def test_context_and_short_symbols_never_become_standalone_identity_branches():
    profile = compiled("sftsv")
    queries = "\n".join(q for qs in profile["query_sets"].values() if isinstance(qs, list) for q in qs if isinstance(q, str))
    assert '("NSs")' not in queries
    assert '("Gn")' not in queries
    assert '("thrombocytopenia")' not in queries
    assert '\nSFTSV\n' in '\n' + queries + '\n'
    assert 'SFTSV outbreak' not in queries
    assert 'SFTSV vaccine' not in queries
