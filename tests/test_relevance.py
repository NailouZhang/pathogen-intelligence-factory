from pathlib import Path

import yaml

from pifactory.profile_contract import deterministic_profile
from pifactory.query_plan import compile_profile_queries
from pifactory.relevance import filter_relevant_papers, relevance_assessment

ROOT = Path(__file__).resolve().parents[1]


def profile(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "x"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_context_only_paper_is_rejected():
    p = profile("sars_cov_2")
    assessment = relevance_assessment("ACE2 signaling in cardiovascular disease", "A receptor study without coronavirus infection.", p)
    assert assessment["decision"] == "reject"


def test_full_identity_in_title_is_accepted():
    p = profile("sars_cov_2")
    assessment = relevance_assessment("SARS-CoV-2 wastewater surveillance in 2026", "Genomic surveillance report.", p)
    assert assessment["decision"] == "accept"


def test_negative_only_unrelated_paper_is_filtered():
    p = profile("hantavirus")
    records = [{"title": "Detection of porcine circovirus", "abstract": "All samples tested negative for hantavirus."}]
    assert filter_relevant_papers(records, p) == []
