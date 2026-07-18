from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pifactory.analysis import RESEARCH_FIELDS, analyze_paper, compact_analysis_payload
from pifactory.analysis_quality import summarize_analysis_quality
from pifactory.llm import LLMError, LLMRouter
from pifactory.render import render_site


ROOT = Path(__file__).resolve().parents[1]


class NeverHTTP:
    def request(self, *args, **kwargs):
        raise AssertionError("network must not be called")

    def get_json(self, *args, **kwargs):
        raise AssertionError("network must not be called")


class FailedLLM:
    available = True

    def json_task(self, **kwargs):
        raise LLMError(
            "validation_failed: methods lacks evidence ids",
            attempts=[
                {
                    "task": "paper_research_analysis",
                    "provider": "gemini",
                    "model": "test-model",
                    "status": "failed",
                    "failure_category": "validation_failed",
                    "error": "methods lacks evidence ids",
                }
            ],
            category="validation_failed",
        )


def test_router_without_keys_returns_structured_no_provider_failure():
    router = LLMRouter(NeverHTTP(), gemini_key="", groq_key="")
    with pytest.raises(LLMError) as captured:
        router.json_task(system="json", prompt="{}", task_name="test")
    assert captured.value.category == "no_provider_configured"
    assert captured.value.attempts
    assert all(row["status"] == "skipped" for row in captured.value.attempts)



def test_llm_attempt_audit_redacts_api_key_from_http_error(monkeypatch):
    secret = "gemini-secret-value"
    router = LLMRouter(NeverHTTP(), gemini_key=secret, groq_key="")
    monkeypatch.setattr(router, "_discover_gemini_models", lambda: ["test-model"])

    def fail(*args, **kwargs):
        raise RuntimeError(f"400 Client Error for url: https://example.test?key={secret}")

    monkeypatch.setattr(router, "_gemini_call", fail)
    with pytest.raises(LLMError) as captured:
        router.json_task(system="json", prompt="{}", provider_order=("gemini",), max_models_per_provider=1)
    serialized = json.dumps(captured.value.attempts)
    assert secret not in serialized
    assert "REDACTED" in serialized

def test_fallback_recovers_methods_and_results_and_preserves_attempts():
    paper = {
        "paper_id": "p-observable",
        "title": "Hantavirus antibody survey in workers",
        "abstract": (
            "The study evaluated hantavirus antibodies in 120 workers. "
            "Serum samples were tested using an immunoassay. "
            "Twelve participants were seropositive, corresponding to 10%. "
            "The authors stated that occupational exposure warrants further study."
        ),
        "publication_types": ["Journal Article"],
    }
    analyze_paper(paper, FailedLLM(), ROOT / "prompts")
    analysis = paper["analysis"]
    assert analysis["status"] == "fallback_source_extract"
    assert analysis["failure_category"] == "validation_failed"
    assert analysis["attempts"][0]["provider"] == "gemini"
    assert "immunoassay" in analysis["analysis"]["methods"]
    assert "10%" in analysis["analysis"]["main_results"]
    assert all(analysis["analysis"].get(field) for field in RESEARCH_FIELDS)


def test_prompt_compaction_retains_role_diversity():
    evidence = []
    for role in ("background", "methods", "results", "conclusion"):
        for index in range(12):
            evidence.append({"id": f"{role[0]}{index}", "role": role, "text": f"{role} sentence {index} " + "x" * 500})
    payload = {"title": "Long evidence", "evidence": evidence}
    compacted = compact_analysis_payload(payload, max_chars=9000)
    assert compacted["prompt_compaction"]["applied"] is True
    retained_roles = {row["role"] for row in compacted["evidence"]}
    assert retained_roles == {"background", "methods", "results", "conclusion"}
    assert len(compacted["evidence"]) < len(evidence)


def test_analysis_quality_warns_when_fallback_exceeds_threshold():
    papers = [
        {
            "paper_id": f"p{i}",
            "title": f"Paper {i}",
            "analysis": {
                "status": "fallback_source_extract" if i < 3 else "passed",
                "failure_category": "validation_failed" if i < 3 else "",
                "attempts": [
                    {
                        "provider": "gemini",
                        "model": "m",
                        "status": "failed",
                        "failure_category": "validation_failed",
                    }
                ] if i < 3 else [{"provider": "groq", "model": "m", "status": "success"}],
            },
        }
        for i in range(4)
    ]
    quality = summarize_analysis_quality(papers, [], warning_ratio=0.20, critical_ratio=0.50)
    assert quality["severity"] == "critical"
    assert quality["combined"]["fallback_ratio"] == 0.75
    assert "严重降级" in quality["message_zh"]
    assert quality["top_failure_categories"][0]["category"] == "validation_failed"


def test_render_site_places_global_analysis_warning_near_top(tmp_path: Path):
    issue = {
        "title_zh": "汉坦病毒每周情报",
        "title_en": "Hantavirus Weekly Intelligence",
        "issue_date": "2026-07-18",
        "window_start": "2026-07-12",
        "window_end": "2026-07-18",
        "papers": [],
        "news": [],
        "overview": {"literature": {}, "news": {}},
        "metrics": {},
        "retrieval_funnel": {},
        "analysis_quality": {
            "severity": "critical",
            "message_zh": "本期分析质量严重降级。",
            "message_en": "Analysis quality is critically degraded.",
            "combined": {"passed": 0, "fallback": 10},
        },
    }
    render_site(issue, tmp_path)
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "分析质量提示" in html
    assert "本期分析质量严重降级" in html
    assert html.index("分析质量提示") < html.index("本期文献进展")


def test_check_credentials_analysis_only_writes_safe_unavailable_audit(tmp_path: Path):
    output = tmp_path / "preflight.json"
    env = {"PATH": __import__("os").environ.get("PATH", ""), "PYTHONPATH": str(ROOT)}
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_credentials.py"), "--analysis-only", "--json-out", str(output)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 3
    audit = json.loads(output.read_text(encoding="utf-8"))
    assert audit["status"] == "unavailable"
    assert "GEMINI_API_KEY" not in json.dumps(audit) or "configured" in json.dumps(audit)
