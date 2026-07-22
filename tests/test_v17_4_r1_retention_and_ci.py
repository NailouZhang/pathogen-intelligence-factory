from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from pifactory.dedup import dedup_news, llm_review_ambiguous_duplicates
from pifactory.profile_contract import deterministic_profile
from pifactory.query_plan import compile_profile_queries
from pifactory.relevance import final_filter

ROOT = Path(__file__).resolve().parents[1]


class ClusterLLM:
    available = True

    def __init__(self, cluster: dict):
        self.cluster = cluster

    def provider_order(self, purpose: str):
        return ("test",)

    def json_task(self, **kwargs):
        return SimpleNamespace(data={"duplicate_clusters": [self.cluster]})


class DecisionLLM:
    available = True

    def __init__(self, code: str):
        self.code = code

    def json_task(self, **kwargs):
        payload = json.loads(kwargs["prompt"])
        return SimpleNamespace(
            data={
                "d": [
                    {"id": row["id"], "c": self.code, "p": 96, "r": "test decision"}
                    for row in payload["records"]
                ]
            }
        )


def _profile(profile_id: str = "hantavirus") -> dict:
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def _ambiguous_papers() -> list[dict]:
    return [
        {
            "source": "Europe PMC",
            "title": "Hantavirus seroprevalence among forestry workers in Germany",
            "authors": ["Alice Smith", "Bob Jones"],
            "journal": "Journal of Occupational Virology",
            "published_date": "2026-07-01",
            "abstract": "Hantavirus antibodies were measured in forestry workers.",
            "retrieval_queries": ["hantavirus forestry workers"],
        },
        {
            "source": "Crossref",
            "title": "Hantavirus antibody seroprevalence among forestry workers in Germany",
            "authors": ["Smith A", "Jones B"],
            "journal": "Journal of Occupational Virology",
            "published_date": "2026-07-01",
            "abstract": "Hantavirus antibodies were measured in forestry workers using serological testing and exposure questionnaires.",
            "retrieval_queries": ["hantavirus seroprevalence"],
        },
        {
            "source": "OpenAlex",
            "title": "Ecological surveillance of rodents in Germany",
            "authors": ["Other Author"],
            "published_date": "2026-07-01",
            "abstract": "Rodent ecology was assessed.",
        },
    ]


def test_llm_ambiguous_dedup_requires_group_confidence_and_same_work() -> None:
    rows = _ambiguous_papers()
    for cluster in (
        {"indexes": [0, 2], "keep_index": 0, "same_work": True, "confidence": 0.99},
        {"indexes": [0, 1], "keep_index": 0, "same_work": True, "confidence": 0.70},
        {"indexes": [0, 1], "keep_index": 0, "same_work": False, "confidence": 0.99},
    ):
        audit: dict = {}
        output = llm_review_ambiguous_duplicates(rows, ClusterLLM(cluster), "prompt", audit)
        assert len(output) == 3
        assert audit["removed"] == 0
        assert audit["rejected_model_clusters"]


def test_llm_ambiguous_dedup_merges_provenance_when_strongly_supported() -> None:
    rows = _ambiguous_papers()[:2]
    audit: dict = {}
    output = llm_review_ambiguous_duplicates(
        rows,
        ClusterLLM(
            {
                "indexes": [0, 1],
                "keep_index": 0,
                "same_work": True,
                "confidence": 0.98,
                "reason": "same authors, venue, date and near-identical title",
            }
        ),
        "prompt",
        audit,
    )
    assert len(output) == 1
    assert audit["removed"] == 1
    assert set(output[0]["sources"]) == {"Europe PMC", "Crossref"}
    assert set(output[0]["retrieval_queries"]) == {
        "hantavirus forestry workers",
        "hantavirus seroprevalence",
    }
    assert "using serological testing" in output[0]["abstract"]
    assert output[0]["llm_dedup_relations"]


def test_news_similar_event_headlines_are_not_collapsed_without_document_evidence() -> None:
    rows = [
        {
            "source": "Outlet A",
            "publisher": "Outlet A",
            "title": "WHO declares hantavirus outbreak over",
            "url": "https://a.example/report",
            "published_date": "2026-07-01",
            "excerpt": "WHO ended the emergency response after no new cases.",
        },
        {
            "source": "Outlet B",
            "publisher": "Outlet B",
            "title": "WHO says hantavirus outbreak is over",
            "url": "https://b.example/story",
            "published_date": "2026-07-01",
            "excerpt": "Officials discussed surveillance and recovery plans.",
        },
    ]
    assert len(dedup_news(rows)) == 2


def test_news_exact_document_copy_is_merged_with_provenance() -> None:
    rows = [
        {
            "source": "RSS A",
            "publisher": "Health Agency",
            "title": "Health agency reports a confirmed hantavirus case",
            "url": "https://example.org/report?utm_source=rss",
            "published_date": "2026-07-01",
            "excerpt": "The health agency confirmed a hantavirus case after laboratory testing.",
            "retrieval_queries": ["hantavirus case"],
        },
        {
            "source": "RSS B",
            "publisher": "Health Agency",
            "title": "Health agency reports a confirmed hantavirus case",
            "url": "https://example.org/report",
            "published_date": "2026-07-01",
            "excerpt": "The health agency confirmed a hantavirus case after laboratory testing.",
            "retrieval_queries": ["confirmed hantavirus"],
        },
    ]
    output = dedup_news(rows)
    assert len(output) == 1
    assert len(output[0]["duplicate_sources"]) == 1
    assert set(output[0]["retrieval_queries"]) == {"hantavirus case", "confirmed hantavirus"}


def _strong_paper() -> dict:
    return {
        "paper_id": "p-strong",
        "title": "Hantavirus infection and occupational surveillance in forestry workers",
        "abstract": (
            "Hantavirus infection was confirmed by serological testing in forestry workers. "
            "The study estimated hantavirus seroprevalence and evaluated rodent exposure."
        ),
        "doi": "10.1000/hanta.2026.1",
        "sources": ["PubMed", "Europe PMC"],
        "evidence_status": {"has_verified_evidence": True},
        "metadata_verification": {"verified": True},
    }


def test_background_llm_code_cannot_delete_strong_authenticated_target_paper() -> None:
    record = _strong_paper()
    output = final_filter([record], _profile(), DecisionLLM("B"), kind="paper", review_mode="all_compact")
    assert len(output) == 1
    assert output[0]["relevance_decision"] == "accept_after_identity_evidence_disagreement_guard"
    assert output[0]["relevance_llm_disagreement_guard"]["accepted"] is True


def test_background_guard_does_not_rescue_weak_or_unverified_record() -> None:
    record = _strong_paper()
    record.pop("doi")
    record["sources"] = ["Unknown"]
    record["metadata_verification"] = {"verified": False}
    record["evidence_status"] = {"has_verified_evidence": False}
    output = final_filter([record], _profile(), DecisionLLM("B"), kind="paper", review_mode="all_compact")
    assert output == []
    assert record["relevance_llm_disagreement_guard"]["accepted"] is False


def test_hard_noise_llm_code_is_never_overridden() -> None:
    record = _strong_paper()
    output = final_filter([record], _profile(), DecisionLLM("N"), kind="paper", review_mode="all_compact")
    assert output == []
    assert record["relevance_decision"] == "reject_after_compact_llm_review"
