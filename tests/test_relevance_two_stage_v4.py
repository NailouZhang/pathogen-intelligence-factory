from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import compile_profile_queries
from src.pifactory.relevance import candidate_filter_news, candidate_filter_papers, final_filter, relevance_assessment

ROOT = Path(__file__).resolve().parents[1]


class NoLLM:
    available = False


def profile(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_abstract_identity_plus_context_survives_candidate_and_final_gate():
    p = profile("respiratory_syncytial_virus")
    record = {
        "title": "A phase 3 prevention trial in healthy infants",
        "abstract": (
            "This randomized trial assessed prevention of respiratory syncytial virus infection "
            "and infant hospitalization. Vaccine efficacy and clinical outcomes were evaluated "
            "in a sufficiently detailed multicenter study population."
        ),
    }
    candidates = candidate_filter_papers([record], p)
    assert len(candidates) == 1
    assert candidates[0]["relevance_candidate"]["decision"] in {"review", "accept"}
    final = final_filter(candidates, p, NoLLM(), kind="paper")
    assert len(final) == 1
    assert final[0]["relevance_decision"] in {"accept", "accept_after_review", "accept_after_deterministic_full_review"}


def test_news_body_is_allowed_to_rescue_a_generic_headline():
    p = profile("nipah_virus")
    record = {
        "title": "Health ministry confirms two new cases",
        "excerpt": "",
        "content": (
            "The health ministry confirmed Nipah virus infection after laboratory diagnosis. "
            "Officials initiated contact tracing, surveillance and person-to-person transmission "
            "investigation. The report contains substantive outbreak details and response measures."
        ),
        "retrieval_queries": ["Nipah virus"],
    }
    # The shallow candidate gate may reject a generic title; the pipeline keeps
    # query-anchored candidates for body extraction. The final gate must accept
    # once the landing-page body provides identity and context.
    final = final_filter([record], p, NoLLM(), kind="news")
    assert len(final) == 1


def test_specific_disease_title_is_high_confidence_accept():
    p = profile("hantavirus")
    assessment = relevance_assessment(
        "Hemorrhagic fever with renal syndrome surveillance in 2026",
        "A regional public health report.",
        p,
    )
    assert assessment["decision"] == "accept"


def test_context_only_still_rejected_after_recall_expansion():
    p = profile("sars_cov_2")
    record = {"title": "ACE2 receptor biology", "abstract": "A molecular protein study without coronavirus infection."}
    assert candidate_filter_papers([record], p) == []
