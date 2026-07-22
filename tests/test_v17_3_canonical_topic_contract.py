from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pifactory.analysis import _analysis_topic_contract
from pifactory.bundled_vocabulary import load_bundled_vocabulary, validate_bundled_vocabulary
from pifactory.query_plan import build_relevance_rules
from pifactory.relevance import _compact_scope, relevance_assessment

ROOT = Path(__file__).resolve().parents[1]
PROFILES = sorted(path.name for path in (ROOT / "config/vocabularies").iterdir() if path.is_dir())


def _profile(profile_id: str) -> dict:
    profile = load_bundled_vocabulary(ROOT, profile_id)["profile"]
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    return profile


def test_all_21_canonical_contracts_pass_real_semantic_validation() -> None:
    assert len(PROFILES) == 21
    for profile_id in PROFILES:
        valid, errors, manifest = validate_bundled_vocabulary(ROOT, profile_id, semantic=True)
        assert valid, (profile_id, errors)
        assert manifest["schema_version"] == 5
        assert manifest["bundle_version"] == "2026.07-v17.4"
        assert manifest["validation_status"] == "passed"


def test_every_core_query_has_an_executable_review_mapping_and_three_round_history() -> None:
    for profile_id in PROFILES:
        canonical = json.loads((ROOT / "config/vocabularies" / profile_id / "canonical_vocabulary.json").read_text(encoding="utf-8"))
        concepts = canonical["retrieval_contract"]["core_concepts"]
        assert len(concepts) == 5
        assert all((row.get("review_mapping") or {}).get("mode") in {"safe_identity", "qualified_identity", "retrieval_only", "retrieval_only_with_review_mapping"} for row in concepts)
        history = canonical["review_history"]
        assert len(history) >= 3
        assert all(row["status"] == "passed" for row in history[:3])
        roles = {row["role"] for row in canonical["authoritative_evidence"] if row.get("required")}
        assert "taxonomy" in roles
        assert "public_health" in roles


def test_compatibility_views_are_fingerprinted_derivatives_not_parallel_truths() -> None:
    for profile_id in PROFILES:
        bundle = load_bundled_vocabulary(ROOT, profile_id)
        fingerprint = bundle["canonical_vocabulary"]["semantic_fingerprint"]
        assert bundle["consumer_contract"]["canonical_vocabulary.json"]
        for name, row in bundle["runtime_file_audit"].items():
            assert row["sha256"]
            assert row["consumer"]
            assert row["derived_from_semantic_fingerprint"] == fingerprint


def test_longest_entity_precedence_routes_biological_neighbours_to_supplementary() -> None:
    cases = [
        ("respiratory_syncytial_virus", "Bovine respiratory syncytial virus vaccine trial"),
        ("norovirus", "Murine norovirus persistence in laboratory mice"),
        ("hepatitis_b_virus", "Duck hepatitis B virus replication"),
        ("hantavirus", "Novel fish hantavirus genome"),
        ("human_enterovirus", "Bovine enterovirus infection in cattle"),
    ]
    for profile_id, title in cases:
        result = relevance_assessment(title, "", _profile(profile_id))
        assert result["decision"] == "review", (profile_id, title, result)
        assert result["route"] == "supplementary_related", (profile_id, title, result)
        assert result["supplementary_eligible"] is True
        assert result["primary_eligible"] is False
        assert result["related_hits"]
        assert not result["hard_excluded_hits"]


def test_hard_lexical_noise_remains_terminal() -> None:
    result = relevance_assessment("Rabies as a metaphor in political discourse", "", _profile("rabies_virus"))
    assert result["decision"] == "reject"
    assert result["route"] == "reject"
    assert result["hard_excluded_hits"]
    assert result["supplementary_eligible"] is False


def test_target_related_comparison_is_primary_reviewable() -> None:
    result = relevance_assessment(
        "Comparative study of human respiratory syncytial virus and bovine respiratory syncytial virus",
        "Target-specific neutralization results were reported for human respiratory syncytial virus.",
        _profile("respiratory_syncytial_virus"),
    )
    assert result["decision"] in {"accept", "review"}
    assert result["route"] == "primary_candidate"
    assert result["mixed_entity_comparison"] is True
    assert result["primary_eligible"] is True


def test_real_disease_identity_is_not_rejected_by_abbreviation_penalty() -> None:
    cases = [
        ("human_papillomavirus", "HPV-associated anal cancer prevention"),
        ("human_papillomavirus", "HPV-associated oropharyngeal cancer epidemiology"),
        ("sftsv", "SFTS disease surveillance"),
        ("human_metapneumovirus", "hMPV respiratory infection surveillance"),
    ]
    for profile_id, title in cases:
        result = relevance_assessment(title, "", _profile(profile_id))
        assert result["decision"] in {"accept", "review"}, (profile_id, title, result)
        assert result["identity_present"] is True


def test_llm_review_and_analysis_payloads_receive_complete_topic_contract() -> None:
    profile = _profile("marburg_virus")
    scope = _compact_scope(profile)
    assert scope["profile_id"] == "marburg_virus"
    assert scope["topic"][0]
    assert "Marburg virus" in scope["target_entities"]
    assert any((row.get("term") if isinstance(row, dict) else row) == "Ebola virus" for row in scope["related_entities"])
    assert "hard_excluded_entities" in scope
    assert scope["authoritative_evidence"]
    payload = _analysis_topic_contract(profile, {"relevance_final": {"identity_hits": ["Marburg virus"], "related_hits": ["Ebola virus"], "hard_excluded_hits": [], "route": "primary_candidate"}})
    assert payload["target_entities"]
    assert payload["related_entities"]
    assert "hard_excluded_entities" in payload
    assert payload["record_identity_hits"] == ["Marburg virus"]
    assert payload["record_related_hits"] == ["Ebola virus"]


def test_consumer_and_prompt_wiring_audit_is_executable() -> None:
    output = ROOT / ".pytest-consumer-audit.json"
    try:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/audit_vocabulary_consumers.py"), "--project-root", str(ROOT), "--output", str(output)],
            check=False, capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        report = json.loads(output.read_text(encoding="utf-8"))
        assert report["passed"] is True
        assert report["profile_count"] == 21
        assert len(report["prompts"]) == 11
    finally:
        output.unlink(missing_ok=True)
