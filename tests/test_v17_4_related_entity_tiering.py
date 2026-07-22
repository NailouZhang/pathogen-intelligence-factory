from __future__ import annotations

from pathlib import Path

from pifactory.bundled_vocabulary import load_bundled_vocabulary
from pifactory.literature.selection import select_primary_and_supplementary
from pifactory.query_plan import build_relevance_rules
from pifactory.relevance import filter_post_enrichment, final_filter
from pifactory.relevance_guard import apply_relevance_cliff_guard

ROOT = Path(__file__).resolve().parents[1]


class UnavailableLLM:
    available = False


def profile(profile_id: str) -> dict:
    value = load_bundled_vocabulary(ROOT, profile_id)["profile"]
    value["post_retrieval_relevance_rules"] = build_relevance_rules(value)
    return value


def test_related_only_paper_survives_final_filter_as_supplementary() -> None:
    record = {
        "paper_id": "murine-noro",
        "title": "Murine norovirus persistence in laboratory mice",
        "abstract": "Murine norovirus was detected and sequenced in laboratory mice.",
        "doi": "10.1000/mnv",
        "metadata_verification": {"verified": True},
        "sources": ["PubMed"],
    }
    output = final_filter([record], profile("norovirus"), UnavailableLLM(), kind="paper")
    assert len(output) == 1
    assert output[0]["relevance_route"] == "supplementary_related"
    assert output[0]["display_eligibility"] == "supplementary_only"
    assert output[0]["primary_eligible"] is False


def test_related_only_news_survives_post_enrichment_without_primary_promotion() -> None:
    record = {
        "news_id": "brsv-news",
        "title": "Bovine RSV update",
        "content": "Veterinary authorities reported bovine respiratory syncytial virus infections in cattle.",
        "url": "https://example.org/brsv",
        "content_status": "full",
    }
    retained, audit = filter_post_enrichment([record], profile("respiratory_syncytial_virus"), "news")
    assert len(retained) == 1
    assert retained[0]["relevance_route"] == "supplementary_related"
    assert retained[0]["display_eligibility"] == "supplementary_only"
    assert audit["route_counts"]["supplementary_related"] == 1


def test_cliff_guard_counts_primary_and_related_independently(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_RATIO_MIN_CANDIDATES", "10")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_TRIGGER_RATIO", "0.30")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.15")
    primary = [
        {
            "paper_id": f"p{i}", "title": "Human respiratory syncytial virus surveillance",
            "abstract": "Human respiratory syncytial virus infection surveillance.",
            "doi": f"10.1000/{i}", "metadata_verification": {"verified": True},
            "relevance_route": "primary_candidate",
        }
        for i in range(20)
    ]
    related = {
        "paper_id": "related", "title": "Bovine respiratory syncytial virus study",
        "abstract": "Bovine respiratory syncytial virus in cattle.",
        "doi": "10.1000/related", "metadata_verification": {"verified": True},
        "relevance_route": "supplementary_related", "display_eligibility": "supplementary_only",
    }
    output, audit = apply_relevance_cliff_guard(primary + [related], primary[:1] + [related], profile("respiratory_syncytial_virus"), kind="paper")
    assert related in output
    assert audit["primary_candidate_count"] == 20
    assert audit["related_supplementary_candidate_count"] == 1
    assert audit["initial_primary_accepted"] == 1
    assert audit["initial_related_supplementary_accepted"] == 1
    assert audit["related_entities_never_promoted_by_recovery"] is True


def test_related_paper_enters_supplementary_view_without_analysis_fields() -> None:
    related = {
        "paper_id": "duck-hbv", "title": "Duck hepatitis B virus replication",
        "doi": "10.1000/duck", "metadata_verification": {"verified": True},
        "evidence_status": {"has_verified_evidence": True},
        "relevance_route": "supplementary_related", "display_eligibility": "supplementary_only",
        "related_hits": ["duck hepatitis B virus"],
        "analysis": {"status": "should_not_be_exposed"},
    }
    primary, supplementary, audit = select_primary_and_supplementary([related], primary_ready=[], primary_limit=50, supplementary_limit=100)
    assert primary == []
    assert len(supplementary) == 1
    assert supplementary[0]["supplementary_reason"] == "biologically_related_non_target_entity"
    assert supplementary[0]["display_mode"] == "supplementary_related"
    assert "analysis" not in supplementary[0]
    assert audit["related_supplementary_displayed"] == 1


def test_all_bundles_keep_related_and_hard_entities_disjoint() -> None:
    for directory in sorted((ROOT / "config" / "vocabularies").iterdir()):
        if not directory.is_dir():
            continue
        canonical = load_bundled_vocabulary(ROOT, directory.name)["canonical_vocabulary"]
        topic = canonical["topic_contract"]
        related = {
            (row.get("term") if isinstance(row, dict) else row).casefold()
            for row in topic.get("related_entities") or []
        }
        hard = {str(row).casefold() for row in topic.get("hard_excluded_entities") or []}
        assert related.isdisjoint(hard), directory.name
        policy = topic.get("related_entity_policy") or {}
        assert policy.get("supplementary_rule")


def test_clinical_differential_neighbour_is_supplementary_not_terminal() -> None:
    record = {
        "paper_id": "heartland-differential",
        "title": "Heartland virus differential diagnosis and surveillance update",
        "abstract": "Heartland virus is considered in the differential diagnosis of febrile thrombocytopenia.",
        "doi": "10.1000/heartland",
        "metadata_verification": {"verified": True},
        "sources": ["PubMed"],
    }
    output = final_filter([record], profile("hantavirus"), UnavailableLLM(), kind="paper")
    assert len(output) == 1
    assert output[0]["relevance_route"] == "supplementary_related"
    assert output[0]["primary_eligible"] is False
