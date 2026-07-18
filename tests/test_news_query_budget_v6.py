from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries


ROOT = Path(__file__).resolve().parents[1]


def test_large_family_keeps_all_single_anchors_without_context_request_explosion():
    seed = yaml.safe_load((ROOT / "profiles/arenaviridae/seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    profile = compile_profile_queries(deterministic_profile(seed, docs))
    sets = profile["query_sets"]
    singles = sets["general_news_single_en"]
    assert len(singles) >= 10
    assert all(any(term.casefold() in q.casefold() for q in sets["general_news_en"]) for term in ["Chapare virus", "Lujo virus"])
    # Independent coverage is retained, while contextual variants are grouped
    # so a provider is not hit by hundreds of near-duplicate requests.
    assert len(sets["general_news_en"]) < len(singles) * 3
    assert len(sets["gdelt"]) < len(singles) * 3
    assert len(sets["reliefweb"]) < len(singles) * 3
