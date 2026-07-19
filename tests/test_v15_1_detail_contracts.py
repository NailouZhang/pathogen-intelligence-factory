from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from pifactory.analysis import classify_paper
from pifactory.dates import assess_publication_date
from pifactory.dedup import dedup_papers
from pifactory.literature.identity import assess_completion_identity, merge_verified_candidate
from pifactory.literature.normalization import metadata_verification, normalize_literature_record
from pifactory.literature.profile import prohibited_direction_tokens, validate_frozen_core_terms
from pifactory.query_plan import build_query_plan, compile_query_sets
from pifactory.ranking import rank_papers
from pifactory.relevance import _deterministic_medium_accept

ROOT = Path(__file__).resolve().parents[1]


def _seed(profile_id: str = "hantavirus") -> dict:
    return yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))


def test_all_21_profiles_use_exactly_five_identity_terms_without_research_direction_combinations() -> None:
    seeds = sorted((ROOT / "profiles").glob("*/seed.yaml"))
    assert len(seeds) == 21
    for path in seeds:
        seed = yaml.safe_load(path.read_text(encoding="utf-8"))
        result = validate_frozen_core_terms(seed, strict=False)
        assert result["passed"], (path.parent.name, result["issues"])
        assert len(result["terms"]) == 5
        assert all(not prohibited_direction_tokens(term) for term in result["terms"])


def test_direction_combination_is_rejected_but_disease_identity_is_allowed() -> None:
    seed = _seed("chikungunya_virus")
    seed["search_strategy"]["concepts"][1]["scholarly"] = "chikungunya vaccine"
    result = validate_frozen_core_terms(seed, strict=False)
    assert not result["passed"]
    assert any("research-direction" in issue for issue in result["issues"])
    assert prohibited_direction_tokens("chronic hepatitis B") == []


def test_controlled_supplemental_terms_are_executed_and_audited() -> None:
    seed = _seed("hantavirus")
    sets = compile_query_sets(seed)
    assert "Puumala virus" in sets["controlled_supplemental_terms"]
    assert "Puumala virus" in sets["pubmed_supplemental"]
    assert "Puumala virus" in sets["pubmed_all"]
    plan = build_query_plan(seed)
    rows = [row for row in plan if row["provider"] == "pubmed_supplemental"]
    assert rows
    assert any(row["query"] == "Puumala virus" for row in rows)
    assert all(row["purpose"] == "controlled supplemental member-identity discovery" for row in rows)


def test_profile_priority_terms_change_global_ranking() -> None:
    profile = {
        "candidate_vocabulary": {
            "paper_priority_terms": [
                {"term": "novel reservoir", "category": "novelty", "weight": 12}
            ]
        }
    }
    base = {
        "source": "PubMed",
        "sources": ["PubMed"],
        "relevance_score": 0.7,
        "abstract": "Verified abstract text.",
        "availability_date": date.today().isoformat(),
        "publication_types": ["Journal Article"],
    }
    ordinary = {**base, "title": "Routine virological characterization", "paper_id": "ordinary"}
    priority = {**base, "title": "First report of a novel reservoir", "paper_id": "priority"}
    ranked = rank_papers([ordinary, priority], profile)
    assert ranked[0]["paper_id"] == "priority"
    assert ranked[0]["profile_priority_score"] == 12
    assert ranked[0]["profile_priority_term_hits"][0]["term"] == "novel reservoir"


def test_profile_document_type_terms_control_classification() -> None:
    work = {
        "title": "Evidence atlas of hantavirus studies",
        "abstract": "This evidence atlas synthesizes studies without using the word review.",
        "publication_types": [],
        "document_type_terms": {
            "systematic_review": ["evidence atlas"],
            "research": ["prospective cohort"],
        },
    }
    assert classify_paper(work) == "review"
    assert work["document_type_category"] == "systematic_review"


def test_identity_verification_has_verified_uncertain_and_conflict_states() -> None:
    expected = {
        "title": "Genomic epidemiology of Hantaan virus",
        "authors": ["Alice Smith", "Bob Chen"],
        "journal": "Virology Journal",
        "year": 2026,
        "doi": "10.1000/example",
    }
    verified = assess_completion_identity(expected, {**expected, "abstract": "x"})
    assert verified["status"] == "identity_verified"

    uncertain = assess_completion_identity(
        {k: v for k, v in expected.items() if k != "doi"},
        {
            "title": "Genomic epidemiology of Hantaan viruses",
            "authors": ["Alice Smith"],
            "journal": "Another Virology Journal",
            "year": 2026,
        },
    )
    assert uncertain["status"] in {"identity_verified", "identity_uncertain"}

    conflict = assess_completion_identity(expected, {**expected, "doi": "10.1000/other"})
    assert conflict["status"] == "identity_conflict"
    record = dict(expected)
    merge_verified_candidate(record, {**expected, "doi": "10.1000/other", "abstract": "wrong"}, method="test")
    assert record["identifier_conflict"]
    merge_verified_candidate(record, {**expected, "abstract": "later weak match"}, method="later")
    assert record["identifier_conflict"]
    assert not record.get("abstract")


def test_dedup_merges_earliest_priority_dates_then_recalculates_canonical_date() -> None:
    records = [
        {
            "source": "Crossref",
            "doi": "10.1000/date",
            "title": "A hantavirus date study",
            "authors": ["A Smith"],
            "first_publication_date": "2026-07-18",
            "online_date": "2026-07-18",
        },
        {
            "source": "PubMed",
            "doi": "10.1000/date",
            "title": "A hantavirus date study",
            "authors": ["Alice Smith"],
            "first_publication_date": "2026-07-11",
            "published_date": "2026-07-20",
        },
    ]
    merged = normalize_literature_record(dedup_papers(records)[0])
    assert merged["first_publication_date"] == "2026-07-11"
    assert merged["canonical_publication_date"] == "2026-07-11"
    assert merged["canonical_publication_date_basis"] == "first_publication_date"
    decision = assess_publication_date(merged, date(2026, 7, 12), date(2026, 7, 18), future_days=90)
    assert not decision.accepted
    assert decision.reason == "canonical_publication_date_before_window"


def test_completion_can_move_date_earlier_and_final_gate_must_reject() -> None:
    record = {
        "title": "Hantavirus article",
        "authors": ["A Smith"],
        "journal": "Journal X",
        "year": 2026,
        "doi": "10.1000/date2",
        "first_publication_date": "2026-07-18",
    }
    candidate = {
        **record,
        "first_publication_date": "2026-07-01",
        "abstract": "Short but verified hantavirus abstract.",
    }
    result = merge_verified_candidate(record, candidate, method="PubMed completion")
    assert result["status"] == "identity_verified"
    normalized = normalize_literature_record(record)
    assert normalized["canonical_publication_date"] == "2026-07-01"
    decision = assess_publication_date(normalized, date(2026, 7, 12), date(2026, 7, 18), future_days=90)
    assert not decision.accepted


def test_short_identity_clear_abstract_survives_deterministic_fallback_without_length_gate() -> None:
    record = {
        "title": "Hantaan virus in rodents",
        "abstract": "Hantaan virus detected in rodents.",
        "doi": "10.1000/short",
        "metadata_verification": {"verified": True},
    }
    assessment = {
        "identity_present": True,
        "title_identity_hits": ["Hantaan virus"],
        "body_identity_hits": ["Hantaan virus"],
        "context_hits": [],
        "exclusion_hits": [],
    }
    assert len(record["abstract"]) < 80
    assert _deterministic_medium_accept(record, assessment, "paper")


def test_metadata_verification_rejects_monotonic_identifier_conflict() -> None:
    record = {
        "title": "Hantavirus study",
        "doi": "10.1000/x",
        "first_publication_date": "2026-07-18",
        "journal": "Journal",
        "authors": ["A Smith"],
        "identifier_conflict": {"reason": "explicit_identifier_mismatch"},
    }
    result = metadata_verification(normalize_literature_record(record))
    assert result["status"] == "identity_conflict"
    assert not result["verified"]


def test_pipeline_uses_comparison_pool_and_second_date_gate() -> None:
    text = (ROOT / "src/pifactory/pipeline_v15.py").read_text(encoding="utf-8")
    assert "comparison_target =" in text
    assert "len(primary_ready) < comparison_target" in text
    assert "ranked_primary_ready = rank_papers(primary_ready, profile)" in text
    assert 'stage="post_dedup_recalculation"' in text
    assert 'stage=f"post_completion_{batch_number}"' in text
    assert 'dump_json(audit_dir / "publication_date_gate.json", paper_final_date_gate_summary)' in text


def test_full_text_only_excerpt_is_traceable_and_translatable(monkeypatch, tmp_path):
    from pifactory.content import _full_text_excerpt
    from pifactory.translation import translate_record

    paper = {
        "paper_id": "full-only",
        "title": "Hantavirus genomic surveillance",
        "abstract": "",
        "full_text": (
            "Background: Hantavirus infection remains an important zoonosis. "
            "Methods: We sequenced 42 complete viral genomes from clinical samples. "
            "Results: We identified two geographically structured lineages and no recurrent mutation associated with severity. "
            "Conclusion: Integrated genomic surveillance is required in endemic regions."
        ),
        "full_text_sections": {
            "methods": "Methods: We sequenced 42 complete viral genomes from clinical samples.",
            "results": "Results: We identified two geographically structured lineages and no recurrent mutation associated with severity.",
            "conclusion": "Conclusion: Integrated genomic surveillance is required in endemic regions.",
        },
        "analysis": {"analysis": {key: "Evidence statement." for key in __import__('pifactory.analysis', fromlist=['RESEARCH_FIELDS']).RESEARCH_FIELDS}},
        "paper_type": "research",
    }
    paper["full_text_excerpt"] = _full_text_excerpt(paper)
    assert "42 complete viral genomes" in paper["full_text_excerpt"]
    assert "geographically structured lineages" in paper["full_text_excerpt"]

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "translate_zh.md").write_text("Translate to Chinese.", encoding="utf-8")

    import pifactory.translation as translation

    def fake_batch(fields, field_kinds, **kwargs):
        return ({key: f"中文：{value}" for key, value in fields.items()}, {key: {"status": "passed_test"} for key in fields})

    monkeypatch.setattr(translation, "_translate_field_map", fake_batch)
    translate_record(
        paper,
        profile={},
        llm=object(),
        prompts_dir=prompts,
        cache={},
        kind="research",
    )
    assert paper["abstract_zh"].startswith("中文")
    assert paper["translation_audit"]["body_source_kind"] == "full_text_excerpt"


def test_supplementary_title_fallback_occurs_only_after_translation_stack(monkeypatch, tmp_path):
    from pifactory.translation import translate_title_only
    import pifactory.translation as translation

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "translate_zh.md").write_text("Translate.", encoding="utf-8")
    calls = []

    def fake_translate_text(text, **kwargs):
        calls.append(text)
        return "", {"status": "all_providers_failed", "attempts": ["google", "mymemory", "llm"]}

    monkeypatch.setattr(translation, "translate_text", fake_translate_text)
    record = {"title": "Verified metadata-only hantavirus record"}
    translate_title_only(record, profile={}, llm=object(), prompts_dir=prompts, cache={})
    assert calls == [record["title"]]
    assert record["title_zh"] == record["title"]
    assert record["supplementary_translation_audit"]["fallback_used"] is True
    assert record["supplementary_translation_audit"]["title"]["status"] == "english_title_fallback_after_all_translation_attempts"


def test_reference_list_doi_does_not_create_false_identity_conflict():
    from pifactory.content import _identity_score

    work = {
        "doi": "10.1000/expected",
        "title": "Hantavirus epidemiology in Chile",
        "authors": ["Ana Ramos"],
        "journal": "Virology Journal",
        "year": 2026,
    }
    text = (
        "Hantavirus epidemiology in Chile. Ana Ramos. Virology Journal 2026. "
        "Methods and results are reported here. References include 10.2000/other and 10.3000/reference."
    )
    accepted, assessment = _identity_score(work, text, "https://publisher.example/article/123")
    assert assessment["status"] != "identity_conflict"
    assert assessment["doi_conflict"] is False


def test_identifier_conflict_is_rejected_even_when_abstract_exists():
    from pifactory.relevance import filter_post_enrichment

    profile = {
        "post_retrieval_relevance_rules": {
            "identity_anchor_patterns": ["hantavirus"],
            "member_patterns": [],
            "disease_patterns": [],
            "context_patterns": [],
            "excluded_entity_patterns": [],
            "qualified_abbreviation_rules": [],
            "minimum_relevance_score": 3,
            "review_score_min": 1,
        }
    }
    paper = {
        "paper_id": "conflict-paper",
        "title": "Hantavirus study",
        "abstract": "Hantavirus was detected in clinical samples.",
        "content_identity_status": "identity_conflict",
        "identifier_conflict": {"reason": "explicit_identifier_mismatch"},
        "metadata_verification": {"conflict": True},
    }
    kept, audit = filter_post_enrichment([paper], profile, "paper")
    assert kept == []
    assert audit["rejected_records"][0]["reason"] == "identifier_conflict"


def test_weekly_profile_resolution_never_calls_llm_builder(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import pifactory.pipeline_v15 as pipeline

    seed = {"profile_id": "hantavirus", "search_strategy": {"concepts": []}}
    fallback = {"status": "ready", "generated_by": "deterministic_seed_contract"}
    calls = {"build": 0, "dump": []}

    monkeypatch.setattr(pipeline, "load_profile", lambda settings: None)
    monkeypatch.setattr(pipeline, "load_seed", lambda project_root, profile_id: seed)
    monkeypatch.setattr(pipeline, "_fallback_profile", lambda value, sources: dict(fallback))

    def forbidden_build(*args, **kwargs):
        calls["build"] += 1
        raise AssertionError("scheduled run must not invoke build_profile")

    monkeypatch.setattr(pipeline, "build_profile", forbidden_build)
    monkeypatch.setattr(pipeline, "dump_json", lambda path, data: calls["dump"].append(path))
    settings = SimpleNamespace(
        project_root=tmp_path,
        profile_id="hantavirus",
        state_dir=tmp_path / "data" / "state",
        refresh_profile=False,
    )
    profile = pipeline._load_or_build_runtime_profile(settings, object(), object(), demo=False)
    assert calls["build"] == 0
    assert profile["generated_by"] == "deterministic_frozen_seed_refresh"
    assert profile["seed_hash"]
    assert calls["dump"] == [tmp_path / "data" / "profiles" / "hantavirus" / "profile.json"]


def test_explicit_profile_refresh_is_the_only_llm_builder_path(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import pifactory.pipeline_v15 as pipeline

    expected = {"status": "ready", "generated_by": "bigmodel:glm-4.7-flash"}
    monkeypatch.setattr(pipeline, "load_profile", lambda settings: {"seed_hash": "old"})
    monkeypatch.setattr(pipeline, "load_seed", lambda project_root, profile_id: {"profile_id": profile_id})
    monkeypatch.setattr(pipeline, "build_profile", lambda settings, http, llm: dict(expected))
    settings = SimpleNamespace(
        project_root=tmp_path,
        profile_id="hantavirus",
        state_dir=tmp_path / "data" / "state",
        refresh_profile=True,
    )
    assert pipeline._load_or_build_runtime_profile(settings, object(), object(), demo=False) == expected


def test_deterministic_profile_uses_core_term_version_2():
    from pifactory.profile_contract import deterministic_profile

    seed = {
        "profile_id": "example",
        "search_strategy": {
            "concepts": [
                {"id": f"c{i}", "scholarly": term, "role": "identity", "priority": i}
                for i, term in enumerate(
                    ["Virus alpha", "Virus beta", "Disease alpha", "Subtype alpha", "Syndrome alpha"], 1
                )
            ],
            "frozen": True,
            "allow_weekly_mutation": False,
        },
        "candidate_vocabulary": {},
    }
    profile = deterministic_profile(seed, [])
    assert profile["search_strategy"]["core_terms_version"] == "2.0"


def test_run_all_defaults_to_no_profile_refresh():
    from pathlib import Path

    # Public-repository tests must also pass when the repository is cloned or
    # distributed independently from the two-repository bundle.  The bundle
    # wrapper ``public_manager.sh`` is validated by ``validate_bundle.sh``;
    # here we verify the same production invariant in the public workflow.
    repo_root = Path(__file__).resolve().parents[1]
    workflow = repo_root / ".github" / "workflows" / "daily-intelligence.yml"
    text = workflow.read_text(encoding="utf-8")
    assert "refresh_profile:" in text
    assert "default: false" in text
    assert "inputs.refresh_profile || 'false'" in text
