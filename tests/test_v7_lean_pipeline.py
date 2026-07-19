from pathlib import Path

import yaml

from pifactory.content import LEGAL_FULLTEXT_POLICY
from pifactory.profile_contract import deterministic_profile
from pifactory.query_plan import compile_profile_queries
from pifactory.relevance import final_filter

ROOT = Path(__file__).resolve().parents[1]


class NoLLM:
    available = False


def _profile(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_every_profile_has_five_semantically_named_core_concepts():
    for path in sorted((ROOT / "profiles").glob("*/seed.yaml")):
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        concepts = seed["search_strategy"]["concepts"]
        assert len(concepts) == 5
        assert len({x["id"] for x in concepts}) == 5
        assert len({x["scholarly"].casefold() for x in concepts}) == 5
        assert all(x["role"] and x["priority"] for x in concepts)


def test_rich_vocabulary_is_used_for_review_not_query_multiplication():
    profile = _profile("hantavirus")
    query_text = "\n".join(profile["query_sets"]["pubmed_core"])
    review_text = "\n".join(profile["post_retrieval_relevance_rules"]["title_or_abstract_identity_patterns"])
    assert len(profile["query_sets"]["pubmed_core"]) == 5
    assert "Puumala virus" not in query_text
    assert "Puumala virus" in review_text
    assert "Andes virus" in review_text


def test_python_accepts_clear_identity_without_llm_and_rejects_context_only():
    profile = _profile("hantavirus")
    good = {"title": "Hantavirus surveillance in rodents", "abstract": "Hantavirus RNA was detected in rodent samples.", "retrieval_queries": ["hantavirus"]}
    bad = {"title": "Platelet biology and renal injury", "abstract": "Thrombocytopenia and capillary leak were studied.", "retrieval_queries": ["hantavirus"]}
    out = final_filter([good, bad], profile, NoLLM(), kind="paper", review_mode="balanced")
    assert [x["title"] for x in out] == [good["title"]]


def test_fulltext_policy_is_legal_open_access_only():
    assert LEGAL_FULLTEXT_POLICY == "legal_open_access_only"
    production = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "src").rglob("*.py")
    ).casefold()
    prohibited = "sci" + "-hub"
    assert prohibited not in production


def test_workflow_and_pipeline_use_v15_completion_and_supplementary_budgets():
    workflow = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "vars.PIF_MAX_FULLTEXTS || '150'" in workflow
    assert 'PIF_MAX_NEWS_FETCHES: "80"' in workflow
    assert "vars.PIF_DISPLAY_CANDIDATE_BUFFER || '100'" in workflow
    assert "vars.PIF_MAX_SUPPLEMENTARY_PAPERS || '100'" in workflow
    pipeline = (ROOT / "src/pifactory/pipeline_v15.py").read_text(encoding="utf-8")
    dedup = pipeline.index("dedup_papers")
    lifecycle = pipeline.index('progress(\n        "literature_lifecycle", "start"', dedup)
    completion = pipeline.index("complete_literature_catalog(", lifecycle)
    final_review = pipeline.index("_review_paper_batch(completed", completion)
    analysis = pipeline.index("_analyze_translate_paper(item)", final_review)
    selection = pipeline.index("select_primary_and_supplementary(", analysis)
    assert dedup < lifecycle < completion < final_review < analysis < selection
    assert "len(primary_ready) < comparison_target" in pipeline
    assert "completion_processed < settings.max_fulltexts" in pipeline
