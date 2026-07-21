from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from pifactory.analysis import (
    RESEARCH_FIELDS,
    _attempt_field_repair,
    _evidence_role_map,
    _paper_validator,
)
from pifactory.bundled_vocabulary import load_bundled_vocabulary
from pifactory.llm import LLMError, LLMResult, _extract_json
from pifactory.query_plan import build_relevance_rules
from pifactory.relevance_guard import apply_relevance_cliff_guard
from pifactory.render import render_site
from pifactory.structured_contract import normalize_structured_candidate, validate_structured_candidate
from scripts.audit_rendered_html import audit_html
from scripts.recover_render_contract import recover_issue

ROOT = Path(__file__).resolve().parents[1]


def _profile() -> dict[str, Any]:
    profile = load_bundled_vocabulary(ROOT, "avian_influenza")["profile"]
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    return profile


def _payload() -> dict[str, Any]:
    return {
        "evidence": [
            {"id": "A1", "role": "background", "text": "The study evaluated avian influenza surveillance."},
            {"id": "A2", "role": "design_population", "text": "A cross-sectional study included 120 poultry samples."},
            {"id": "A3", "role": "methods", "text": "Samples were tested by RT-PCR and sequencing."},
            {"id": "A4", "role": "results", "text": "Twelve samples were positive for H5N1 virus."},
            {"id": "A5", "role": "interpretation", "text": "The findings indicate ongoing viral circulation."},
            {"id": "A6", "role": "implications", "text": "The authors support continued surveillance."},
            {"id": "A7", "role": "limitations", "text": "The single-region sample limits generalisability."},
        ]
    }


def _valid_candidate() -> dict[str, Any]:
    fields = {
        "research_question_and_background": "The study evaluated the current circulation of H5N1 avian influenza virus.",
        "study_design_and_population": "A cross-sectional design included 120 poultry samples from one region.",
        "methods": "The investigators used RT-PCR and sequencing to test the collected samples.",
        "main_results": "Twelve of the 120 poultry samples were positive for H5N1 virus.",
        "interpretation_and_novelty": "The findings indicate continuing H5N1 circulation in the sampled poultry population.",
        "scientific_and_public_health_significance": "The results support continued surveillance of avian influenza in poultry.",
        "limitations_and_evidence_strength": "The single-region sampling limits generalisability, so confidence is moderate.",
    }
    return {
        "analysis": fields,
        "summary_en": " ".join(fields.values()),
        "evidence_ids": {field: [f"A{index}"] for index, field in enumerate(RESEARCH_FIELDS, 1)},
        "confidence": "moderate",
    }


def test_news_ratio_trigger_is_independent_and_does_not_force_thirty_percent(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "100")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_RATIO_MIN_CANDIDATES", "10")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_TRIGGER_RATIO", "0.30")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.15")
    candidates = [
        {
            "news_id": f"n{i}",
            "title": "H5N1 avian influenza outbreak update",
            "excerpt": "Health authorities reported H5N1 avian influenza infections in poultry.",
            "url": f"https://example.org/{i}",
            "relevance_final": {"decision": "reject", "identity_present": True, "score": 4},
        }
        for i in range(87)
    ]
    output, audit = apply_relevance_cliff_guard(candidates, candidates[:1], _profile(), kind="news")
    assert audit["triggered"] is True
    assert audit["guard_settings"]["trigger_acceptance_ratio"] == 0.30
    assert audit["target_accepted"] == 14
    assert audit["target_accepted"] < 27
    assert len(output) == 14
    assert audit["target_cap_enforced"] is True
    assert audit["initial_rejection_reason_counts"]["score_below_final_threshold"] == 86


def test_ten_record_ratio_alarm_targets_fifteen_percent_not_ten_records(monkeypatch) -> None:
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "100")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_RATIO_MIN_CANDIDATES", "10")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_TRIGGER_RATIO", "0.30")
    monkeypatch.setenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.15")
    candidates = [
        {
            "news_id": f"small-{i}",
            "title": "H5N1 avian influenza outbreak update",
            "excerpt": "Health authorities reported H5N1 avian influenza infections in poultry.",
            "url": f"https://example.org/small/{i}",
            "relevance_final": {"decision": "reject", "identity_present": True, "score": 4},
        }
        for i in range(10)
    ]
    output, audit = apply_relevance_cliff_guard(candidates, candidates[:1], _profile(), kind="news")
    assert audit["triggered"] is True
    assert audit["trigger_reasons"] == ["candidate_acceptance_ratio"]
    assert audit["target_accepted"] == 2
    assert len(output) == 2


def test_json_parser_repairs_fence_trailing_comma_and_python_literal() -> None:
    fenced = _extract_json('```json\n{"analysis":{"methods":"A complete method."},}\n```')
    assert fenced["analysis"]["methods"] == "A complete method."
    assert fenced["_pif_parser_audit"]["repaired"] is True
    literal = _extract_json("{'confidence': 'medium', 'evidence_ids': {'methods': ['A1']}}")
    assert literal["confidence"] == "medium"
    assert "python_literal_repair" in literal["_pif_parser_audit"]["method"]


def test_prevalidation_normalization_repairs_aliases_types_language_and_refs() -> None:
    raw = {
        "analysis": {
            "background": ["The study evaluated avian influenza surveillance."],
            "study_design": "A cross-sectional design included poultry samples.",
            "methodology": "Samples were tested by RT-PCR.",
            "results": "Twelve samples were positive.",
            "interpretation": "The findings indicate viral circulation.",
            "significance": "Continued surveillance is supported.",
            "limitations": "Single-region sampling limits generalisability.",
        },
        "evidence_ids": {
            "background": "a1",
            "study_design": "A2",
            "methodology": ["A3"],
            "results": "[A4]",
            "interpretation": "A5",
            "significance": "A6",
            "limitations": "A7",
        },
        "summary": "A concise evidence-grounded summary of the study and its surveillance implications.",
        "confidence": "medium",
    }
    normalized, audit = normalize_structured_candidate(
        raw,
        payload=_payload(),
        kind="research",
        required_fields=RESEARCH_FIELDS,
        source_language="en",
    )
    assert normalized["analysis"]["methods"] == "Samples were tested by RT-PCR."
    assert normalized["evidence_ids"]["research_question_and_background"] == ["A1"]
    assert normalized["confidence"] == "moderate"
    assert audit["repair_count"] >= 5
    valid, detail = validate_structured_candidate(
        normalized,
        required_fields=RESEARCH_FIELDS,
        valid_ids={f"A{i}" for i in range(1, 8)},
        kind="research",
    )
    assert valid is True, detail


class _RepairRouter:
    available = True

    @staticmethod
    def provider_order(_: str) -> tuple[str, ...]:
        return ("repair-provider",)

    def json_task(self, **kwargs: Any) -> LLMResult:
        repair = {
            "analysis": {"methods": "The investigators used RT-PCR and sequencing to test all samples."},
            "evidence_ids": {"methods": ["A3"]},
        }
        normalizer = kwargs.get("normalizer")
        data, normalization_audit = normalizer(repair) if normalizer else (repair, {})
        valid, detail = kwargs["validator"](data)
        assert valid, detail
        return LLMResult(
            data=data,
            provider="repair-provider",
            model="repair-model",
            attempts=[{"status": "success", "provider": "repair-provider", "model": "repair-model", "normalization_audit": normalization_audit}],
        )


def test_only_failed_field_is_rewritten_and_preserved_fields_remain() -> None:
    payload = _payload()
    candidate = _valid_candidate()
    original_background = candidate["analysis"]["research_question_and_background"]
    candidate["analysis"]["methods"] = "The investigators used an unspecified laboratory workflow to test the samples."
    candidate["evidence_ids"]["methods"] = ["UNKNOWN"]
    valid_ids = {f"A{i}" for i in range(1, 8)}
    validator = _paper_validator("research", valid_ids, _evidence_role_map(payload))
    _, detail = validator(candidate)
    exc = LLMError(
        "validation_failed",
        category="validation_failed",
        candidates=[{
            "provider": "initial-provider",
            "model": "initial-model",
            "data": candidate,
            "validation": detail,
        }],
    )
    repaired, audit = _attempt_field_repair(
        exc=exc,
        llm=_RepairRouter(),
        prompts_dir=ROOT / "prompts",
        prompt_payload=payload,
        payload=payload,
        kind="research",
        source_language="en",
        valid_ids=valid_ids,
        role_map=_evidence_role_map(payload),
    )
    assert repaired is not None
    assert repaired["analysis"]["methods"].startswith("The investigators used")
    assert repaired["evidence_ids"]["methods"] == ["A3"]
    assert repaired["analysis"]["research_question_and_background"] == original_background
    assert audit["status"] == "passed_after_field_repair"
    assert audit["rounds"][0]["targets"] == ["methods"]


def test_render_recovery_preserves_multilingual_title_but_removes_supplementary_deep_content(tmp_path: Path) -> None:
    issue = {
        "title_zh": "测试周报",
        "title_en": "Test Weekly Intelligence",
        "issue_date": "2026-07-21",
        "window_start": "2026-07-14",
        "window_end": "2026-07-21",
        "papers": [],
        "news": [],
        "supplementary_papers": [{
            "paper_id": "uk-1",
            "title": "КЛІНІКО-ПАТОГЕНЕТИЧНІ ТЕНДЕНЦІЇ ПНЕВМОНІЙ У ДІТЕЙ",
            "title_en": "Clinical trends of pneumonia in children",
            "title_zh": "儿童肺炎临床趋势",
            "authors": ["A"],
            "journal": "Journal",
            "availability_date": "2026-07-21",
            "analysis": {"analysis": {"methods": "Deep content must not remain."}},
            "abstract": "Deep abstract must not remain.",
        }],
        "supplementary_news": [],
        "metrics": {"supplementary_papers": 1},
        "overview": {},
        "retrieval_funnel": {"papers": {}, "news": {}},
    }
    recovered, audit = recover_issue(issue, {"findings": [{"severity": "critical", "code": "supplementary_card_contains_deep_content"}]})
    assert "analysis" not in recovered["supplementary_papers"][0]
    assert "abstract" not in recovered["supplementary_papers"][0]
    assert audit["supplementary_deep_fields_removed"]["analysis"] == 1
    render_site(recovered, tmp_path)
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'data-metadata-role="title"' in html
    result = audit_html(tmp_path / "site/index.html")
    assert result["status"] == "passed", json.dumps(result, ensure_ascii=False)



def test_emergency_output_builds_valid_site_and_wechat_package(tmp_path: Path) -> None:
    output = tmp_path / "out"
    (output / "data").mkdir(parents=True)
    (output / "wechat-package").mkdir(parents=True)
    issue = {
        "profile_id": "respiratory_syncytial_virus",
        "issue_id": "respiratory_syncytial_virus-2026-07-21",
        "issue_date": "2026-07-21",
        "title_zh": "呼吸道合胞病毒周报",
        "title_en": "Respiratory syncytial virus weekly intelligence",
        "papers": [{"title": "Source data must remain private and intact."}],
    }
    source_path = output / "data/latest.json"
    source_path.write_text(json.dumps(issue, ensure_ascii=False), encoding="utf-8")
    source_before = source_path.read_bytes()
    # The emergency builder only reuses and hashes the previously generated
    # cover; image decoding is intentionally not required at this last layer.
    (output / "wechat-package/cover.jpg").write_bytes(b"existing-cover-bytes")
    (output / "wechat-package/manifest.json").write_text(
        json.dumps({"cover": {"file": "cover.jpg"}}),
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/build_emergency_output.py"),
            "--output-dir",
            str(output),
            "--reason",
            "test_contract_failure",
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/validate_wechat_package.py"),
            str(output / "wechat-package"),
        ],
        check=True,
    )
    result = audit_html(output / "site/index.html")
    assert result["status"] == "passed"
    assert source_path.read_bytes() == source_before
    manifest = json.loads((output / "wechat-package/manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["emergency_metadata_only"] is True
    audit = json.loads((output / "data/audit/render_emergency_output.json").read_text(encoding="utf-8"))
    assert audit["source_latest_json_preserved"] is True



def test_recover_render_contract_cli_preserves_canonical_latest_json(tmp_path: Path) -> None:
    output = tmp_path / "out"
    demo = {
        "profile_id": "respiratory_syncytial_virus",
        "issue_id": "respiratory_syncytial_virus-2026-07-21",
        "issue_date": "2026-07-21",
        "generated_at": "2026-07-21T00:00:00Z",
        "schema_version": "6.2",
        "title_zh": "呼吸道合胞病毒周报",
        "title_en": "Respiratory syncytial virus weekly intelligence",
        "window_start": "2026-07-14",
        "window_end": "2026-07-21",
        "papers": [],
        "news": [],
        "supplementary_papers": [{
            "paper_id": "source-1",
            "title": "КЛІНІКО-ПАТОГЕНЕТИЧНІ ТЕНДЕНЦІЇ",
            "title_en": "Clinical trends",
            "title_zh": "临床趋势",
            "abstract": "Private source abstract must remain in canonical data.",
        }],
        "supplementary_news": [],
        "metrics": {},
        "overview": {},
        "retrieval_funnel": {"papers": {}, "news": {}},
    }
    (output / "data/audit").mkdir(parents=True)
    (output / "wechat-package").mkdir(parents=True)
    (output / "site/assets").mkdir(parents=True)
    cover_bytes = b"existing-cover-for-recovery-test"
    import hashlib
    cover_hash = hashlib.sha256(cover_bytes).hexdigest()
    (output / "wechat-package/cover.jpg").write_bytes(cover_bytes)
    (output / "site/assets/cover.jpg").write_bytes(cover_bytes)
    demo["cover"] = {
        "cover_sha256": cover_hash,
        "generator": "test",
        "profile_fingerprint": "test-fingerprint",
    }
    latest = output / "data/latest.json"
    latest.write_text(json.dumps(demo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = latest.read_bytes()
    audit = output / "data/audit/rendered_html_quality.initial.json"
    audit.write_text(json.dumps({
        "status": "failed",
        "findings": [{"severity": "critical", "code": "supplementary_card_contains_deep_content"}],
    }), encoding="utf-8")
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/recover_render_contract.py"),
        "--output-dir", str(output),
        "--audit-json", str(audit),
    ], check=True)
    assert latest.read_bytes() == before
    recovery = json.loads((output / "data/audit/render_contract_recovery.json").read_text(encoding="utf-8"))
    assert recovery["source_latest_json_preserved"] is True
    assert audit_html(output / "site/index.html")["status"] == "passed"
