from pathlib import Path

import yaml

from src.pifactory.profile_contract import deterministic_profile
from src.pifactory.query_plan import build_query_plan, compile_profile_queries

ROOT = Path(__file__).resolve().parents[1]


def compiled(profile_id: str):
    seed = yaml.safe_load((ROOT / "profiles" / profile_id / "seed.yaml").read_text(encoding="utf-8"))
    docs = [{"url": x["url"], "usable": True, "sha256": "test"} for x in seed["authoritative_sources"]]
    return compile_profile_queries(deterministic_profile(seed, docs))


def test_pubmed_and_europe_pmc_have_provider_fields_and_fallbacks():
    profile = compiled("respiratory_syncytial_virus")
    sets = profile["query_sets"]
    assert all("[Title/Abstract]" in q for q in sets["pubmed_core_high_precision"])
    assert sets["pubmed_identity_fallback"]
    assert all("[Title/Abstract]" in q for q in sets["pubmed_identity_fallback"])
    assert all("TITLE_ABS:" in q for q in sets["europe_pmc"])
    assert all("TITLE_ABS:" in q for q in sets["europe_pmc_identity_fallback"])
    joined = "\n".join(sets["pubmed_core_high_recall"])
    assert '"RSV"[Title/Abstract] AND' in joined


def test_semantic_scholar_uses_no_pubmed_syntax_and_qualifies_abbreviations():
    profile = compiled("respiratory_syncytial_virus")
    queries = profile["query_sets"]["semantic_scholar"]
    assert queries
    assert all("[Title/Abstract]" not in q for q in queries)
    assert all("-" not in q for q in queries), "hyphens must be normalized"
    assert any(q.startswith("RSV +(") and " | " in q for q in queries)
    assert "RSV" not in queries, "ambiguous abbreviations must never be standalone"
    assert all(" AND " not in q and " OR " not in q for q in queries), "bulk search uses + and | operators"


def test_crossref_receives_simple_identity_terms_only():
    profile = compiled("sars_cov_2")
    queries = profile["query_sets"]["crossref"]
    assert queries
    assert all("[Title/Abstract]" not in q for q in queries)
    assert all(" AND " not in q and " OR " not in q and " NOT " not in q for q in queries)


def test_openalex_has_exact_and_normal_channels():
    profile = compiled("sars_cov_2")
    exact = profile["query_sets"]["openalex_exact"]
    normal = profile["query_sets"]["openalex_normal"]
    assert exact and normal
    assert any("SARS-CoV-2" in q for q in exact)
    assert any(" OR " in q or " AND " in q for q in normal)


def test_all_query_chunks_are_present_in_audit_plan():
    profile = compiled("arenaviridae")
    plan = build_query_plan(profile, max_groups=1000)
    keys = (
        "pubmed_single_anchor_exact", "pubmed_single_qualified",
        "pubmed_core_high_precision", "pubmed_core_high_recall", "pubmed_identity_fallback",
        "pubmed_molecular", "pubmed_epidemiology", "pubmed_clinical",
        "europe_pmc_single_anchor_exact", "europe_pmc_single_qualified",
        "europe_pmc", "europe_pmc_identity_fallback", "crossref", "semantic_scholar",
        "openalex_exact", "openalex_normal", "general_news_single_en", "general_news_single_zh",
        "general_news_en", "general_news_zh",
        "gdelt", "reliefweb", "authoritative_web_queries",
    )
    assert len(plan) == sum(len(profile["query_sets"].get(k) or []) for k in keys)
    assert len(profile["query_sets"]["pubmed_core_high_recall"]) >= 2
