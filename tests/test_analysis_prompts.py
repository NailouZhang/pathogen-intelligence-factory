from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_research_prompt_has_seven_required_elements():
    text = (ROOT / "prompts/research_analysis.md").read_text(encoding="utf-8")
    for key in (
        "research_question_and_background",
        "study_design_and_population",
        "methods",
        "main_results",
        "interpretation_and_novelty",
        "scientific_and_public_health_significance",
        "limitations_and_evidence_strength",
    ):
        assert key in text
    assert "Every analytical field must cite" in text


def test_review_prompt_has_five_scientific_elements():
    text = (ROOT / "prompts/review_analysis.md").read_text(encoding="utf-8")
    for key in (
        "scope_and_question",
        "evidence_base_and_review_method",
        "consensus_and_key_conclusions",
        "controversies_and_evidence_gaps",
        "research_and_practice_implications",
    ):
        assert key in text
    assert "Never treat a review as a newly performed experiment" in text


def test_news_prompt_has_five_required_elements_and_brief():
    text = (ROOT / "prompts/news_analysis.md").read_text(encoding="utf-8")
    for key in (
        "time",
        "location_and_population",
        "event",
        "scale_impact_and_risk",
        "response_status_and_uncertainty",
        "brief_en",
    ):
        assert key in text


def test_failed_or_weak_llm_still_produces_complete_research_framework(tmp_path):
    from src.pifactory.analysis import RESEARCH_FIELDS, analyze_paper
    from src.pifactory.llm import LLMError

    class WeakLLM:
        available = True

        def json_task(self, **kwargs):
            raise LLMError("model output failed strict schema validation")

    prompts = Path(__file__).resolve().parents[1] / "prompts"
    paper = {
        "paper_id": "p-weak",
        "title": "Hantavirus antibody survey in workers",
        "abstract": (
            "The study evaluated hantavirus antibodies in 120 workers. "
            "Serum samples were tested using an immunoassay. "
            "Twelve participants were seropositive, corresponding to 10%. "
            "The authors stated that occupational exposure warrants further study."
        ),
        "publication_types": ["Journal Article"],
    }
    analyze_paper(paper, WeakLLM(), prompts)
    assert paper["analysis_ready"] is True
    assert paper["analysis"]["status"] == "fallback_source_extract"
    assert all(paper["analysis"]["analysis"].get(field) for field in RESEARCH_FIELDS)
    assert "120" in paper["analysis"]["summary_en"]
