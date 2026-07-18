from __future__ import annotations

import json
from pathlib import Path

import pytest

from pifactory.analysis import ANALYSIS_POLICY_VERSION, RESEARCH_FIELDS, analyze_paper, build_paper_evidence
from pifactory.llm import LLMError, LLMResult, LLMRouter


class NeverHTTP:
    def request(self, *args, **kwargs):
        raise AssertionError("network must not be called")

    def get_json(self, *args, **kwargs):
        raise AssertionError("network must not be called")


def test_policy_version_invalidates_pre_v12_analysis_cache():
    assert ANALYSIS_POLICY_VERSION.startswith("v14-")


def test_default_provider_pools_include_all_five_providers(monkeypatch):
    for name in (
        "PIF_LLM_EXTRACT_PROVIDER_ORDER",
        "PIF_LLM_RESCUE_PROVIDER_ORDER",
        "PIF_LLM_OVERVIEW_PROVIDER_ORDER",
        "PIF_LLM_RELEVANCE_PROVIDER_ORDER",
    ):
        monkeypatch.delenv(name, raising=False)
    router = LLMRouter(NeverHTTP())
    assert set(router.provider_order("extract")) == {"gemini", "groq", "openrouter", "mistral", "siliconflow"}
    assert set(router.provider_order("rescue")) == {"gemini", "groq", "openrouter", "mistral", "siliconflow"}


def test_router_switches_provider_after_quota_exhaustion(monkeypatch):
    router = LLMRouter(NeverHTTP(), siliconflow_key="sf-key", groq_key="groq-key")
    monkeypatch.setattr(router, "_discover_models", lambda provider: [f"{provider}-model"])

    def fake_call(provider, model, system, prompt, temperature):
        if provider == "siliconflow":
            raise RuntimeError("HTTP status 402: insufficient balance")
        return {"status": "ok"}, {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}, model

    monkeypatch.setattr(router, "_openai_compatible_call", fake_call)
    result = router.json_task(
        system="json",
        prompt="{}",
        provider_order=("siliconflow", "groq"),
        validator=lambda data: (data.get("status") == "ok", "status"),
        max_models_per_provider=1,
    )
    assert result.provider == "groq"
    assert router.states["siliconflow"].status == "quota_exhausted"
    assert router.states["groq"].total_tokens == 15


def test_l1_abstract_analysis_never_sends_fulltext():
    full = "FULLTEXT SECRET TOKEN " * 5000
    paper = {
        "paper_id": "p1",
        "title": "Hantavirus cohort",
        "abstract": "Methods: Serum samples were tested by ELISA. Results: 12 of 120 samples were positive.",
        "full_text": full,
        "analysis_level": "L1_abstract_only",
    }
    payload = build_paper_evidence(paper)
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "FULLTEXT SECRET TOKEN" not in serialized
    assert payload["evidence_scope"] == "abstract_only"


def test_l2_locally_selects_bounded_role_balanced_evidence(monkeypatch):
    monkeypatch.setenv("PIF_ANALYSIS_EVIDENCE_MAX_CHARS", "4200")
    methods = " ".join(f"Methods: Sample {i} was tested using RT-PCR and ELISA." for i in range(40))
    results = " ".join(f"Results: {i + 1} of 120 participants were positive ({i + 1}%)." for i in range(40))
    paper = {
        "paper_id": "p2",
        "title": "Hantavirus surveillance",
        "abstract": "This study assessed hantavirus infection in workers.",
        "full_text_sections": {"methods": methods, "results": results},
        "analysis_level": "L2_retrieved_fulltext_evidence",
    }
    payload = build_paper_evidence(paper)
    audit = payload["evidence_selector"]
    roles = {row["role"] for row in payload["evidence"]}
    assert audit["selected_chars"] <= 4200
    assert audit["selected_rows"] < audit["original_rows"]
    assert "methods" in roles
    assert "results" in roles
    assert payload["evidence_scope"] == "retrieved_fulltext_evidence"


def test_workflow_exposes_new_secrets_and_low_token_controls():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/daily-intelligence.yml").read_text()
    for name in (
        "OPENROUTER_API_KEY",
        "MISTRAL_API_KEY",
        "SILICONFLOW_API_KEY",
        "PIF_ANALYSIS_FULLTEXT_TOP_N",
        "PIF_ANALYSIS_CROSSCHECK_TOP_N",
        "PIF_ANALYSIS_EVIDENCE_MAX_CHARS",
        "PIF_LLM_EXTRACT_PROVIDER_ORDER",
        "PIF_LLM_RESCUE_PROVIDER_ORDER",
    ):
        assert name in workflow


def test_l3_uses_independent_rescue_provider_for_crosscheck():
    class FakeRouter:
        def __init__(self):
            self.calls = []

        def provider_order(self, purpose):
            return ("siliconflow", "groq") if purpose == "extract" else ("gemini", "mistral", "siliconflow")

        def json_task(self, **kwargs):
            self.calls.append(kwargs.get("provider_order"))
            values = {
                field: f"Distinct evidence-grounded statement for {field.replace('_', ' ')}."
                for field in RESEARCH_FIELDS
            }
            data = {
                "analysis": values,
                "summary_en": " ".join(values.values()),
                "evidence_ids": {field: ["A1"] for field in RESEARCH_FIELDS},
                "confidence": "moderate",
            }
            provider = "siliconflow" if len(self.calls) == 1 else "gemini"
            return LLMResult(data=data, provider=provider, model=f"{provider}-model", attempts=[])

    paper = {
        "paper_id": "p3",
        "title": "Hantavirus study",
        "abstract": "This study assessed hantavirus infection. Serum samples were tested by PCR. Results identified infection in participants.",
        "analysis_level": "L3_cross_provider_verified",
        "publication_types": ["Journal Article"],
    }
    router = FakeRouter()
    analyze_paper(paper, router, Path(__file__).resolve().parents[1] / "prompts")
    assert len(router.calls) == 2
    assert router.calls[0] == ("siliconflow", "groq")
    assert "siliconflow" not in router.calls[1]
    assert paper["analysis"]["crosscheck"]["provider"] == "gemini"
