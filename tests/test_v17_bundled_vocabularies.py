from __future__ import annotations

from pathlib import Path

from pifactory.bundled_vocabulary import load_bundled_vocabulary, validate_bundled_vocabulary
from pifactory.query_plan import build_relevance_rules
from pifactory.relevance import relevance_assessment

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "arenaviridae", "avian_influenza", "chikungunya_virus", "dengue_virus",
    "ebola_viruses", "hantavirus", "hepatitis_b_virus", "human_adenovirus",
    "human_enterovirus", "human_metapneumovirus", "human_papillomavirus",
    "marburg_virus", "measles_virus", "mpox_virus", "nipah_virus",
    "norovirus", "rabies_virus", "respiratory_syncytial_virus", "sars_cov_2",
    "seasonal_influenza", "sftsv",
}


def test_all_21_chatgpt_curated_bundles_are_complete_and_sars_is_not_exempt() -> None:
    found = {path.name for path in (ROOT / "config/vocabularies").iterdir() if path.is_dir()}
    assert found == EXPECTED
    assert "sars_cov_2" in found
    for profile_id in sorted(EXPECTED):
        valid, errors, manifest = validate_bundled_vocabulary(ROOT, profile_id)
        assert valid, (profile_id, errors)
        assert manifest["generated_by"] == "canonical-compiler-v17.4"
        assert manifest["validation_status"] in {"passed", "semantic_validation_required"}
        assert sum(int(v) for v in manifest["term_counts"].values()) >= 40


def test_review_rules_are_recompiled_from_bundle_and_never_core_only() -> None:
    bundle = load_bundled_vocabulary(ROOT, "sars_cov_2")
    profile = bundle["profile"]
    profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
    rules = profile["post_retrieval_relevance_rules"]
    assert len(rules["identity_anchor_patterns"]) >= 3
    assert len(rules["context_patterns"]) >= 20
    assert "related_entity_patterns" in rules
    assert "hard_excluded_entity_patterns" in rules
    assert len(rules["related_entity_patterns"]) >= 2
    result = relevance_assessment(
        "SARS-CoV-2 neutralizing antibodies after infection",
        "The study reports viral neutralization and immune response in patients with COVID-19.",
        profile,
    )
    assert result["identity_present"] is True
    assert result["decision"] in {"accept", "review"}
