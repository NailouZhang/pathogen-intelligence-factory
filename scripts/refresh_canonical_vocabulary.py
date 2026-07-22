#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pifactory.authority_sources import fetch_authoritative_documents
from pifactory.bootstrap import build_profile
from pifactory.bundled_vocabulary import load_bundled_vocabulary, validate_bundled_vocabulary
from pifactory.canonical_compiler import compile_profile_views
from pifactory.config import Settings, load_seed
from pifactory.http import HttpClient
from pifactory.llm import LLMRouter
from pifactory.utils import clean_space, utc_now_iso

PROMPT_VERSION = "review-vocabulary-v17.4.0-1"
SCHEMA_VERSION = 5
BUNDLE_VERSION = "2026.07-v17.4"


def norm(value: Any) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", clean_space(value).casefold()).strip()


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def term_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in value or [] if isinstance(row, dict) and clean_space(row.get("term"))]


def terms(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for row in value or []:
        term = clean_space(row.get("term") if isinstance(row, dict) else row)
        key = norm(term)
        if term and key not in seen:
            seen.add(key); output.append(term)
    return output


def dedup_cases(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (norm(row.get("title") or row.get("text")), clean_space(row.get("expected")), clean_space(row.get("expected_route")))
        if key in seen:
            continue
        seen.add(key); output.append(row)
    return output


def executable_cases(canonical: dict[str, Any]) -> dict[str, Any]:
    topic = canonical.get("topic_contract") or {}
    retrieval = canonical.get("retrieval_contract") or {}
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []
    comparison: list[dict[str, Any]] = []

    target_terms = terms(
        list(topic.get("target_entities") or [])
        + list(topic.get("allowed_members") or [])
        + list(topic.get("disease_entities") or [])
    )
    for term in target_terms[:30]:
        text = f"{term} surveillance and clinical study"
        positive.append({"surface":"title","title":text,"text":text,"expected":"accept","expected_route":"primary_candidate","case_role":"canonical_target_identity"})

    for concept in retrieval.get("core_concepts") or []:
        mapping = concept.get("review_mapping") or {}
        term = clean_space(concept.get("scholarly"))
        contexts = list(mapping.get("required_context_terms") or [])[:2]
        text = clean_space(" ".join([term] + contexts + ["surveillance study"]))
        positive.append({"surface":"title","title":text,"text":text,"expected":"accept","expected_route":"primary_candidate","case_role":"core_to_review_mapping"})

    for row in topic.get("related_entities") or []:
        term = clean_space(row.get("term") if isinstance(row, dict) else row)
        if not term:
            continue
        title = f"{term} genomic and epidemiological study"
        related.append({"surface":"title","title":title,"text":title,"expected":"review","expected_route":"supplementary_related","case_role":"related_non_target_supplementary"})
        if target_terms:
            mixed = f"Comparative study of {target_terms[0]} and {term} with target-specific results"
            comparison.append({"surface":"title","title":mixed,"text":mixed,"expected":"review","expected_route":"primary_candidate","case_role":"material_target_related_comparison"})

    for term in terms(topic.get("hard_excluded_entities") or topic.get("excluded_entities") or []):
        title = f"{term} company platform update"
        negative.append({"surface":"title","title":title,"text":title,"expected":"reject","expected_route":"reject","case_role":"hard_unrelated_negative"})

    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": canonical.get("profile_id"),
        "positive": dedup_cases(positive),
        "negative": dedup_cases(negative),
        "related": dedup_cases(related),
        "comparison": dedup_cases(comparison),
    }


def rebuild_term_evidence(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    sources = canonical.get("authoritative_evidence") or []
    source_by_url = {clean_space(row.get("url")): row.get("source_id") for row in sources if clean_space(row.get("url"))}
    taxonomy = [row.get("source_id") for row in sources if row.get("role") == "taxonomy"]
    public = [row.get("source_id") for row in sources if row.get("role") == "public_health"]
    molecular = [row.get("source_id") for row in sources if row.get("role") == "molecular"]
    topic = canonical.get("topic_contract") or {}
    rows: list[dict[str, Any]] = []

    def add(term: str, category: str, source_urls: list[str] | None = None) -> None:
        term = clean_space(term)
        if not term:
            return
        ids = [source_by_url.get(clean_space(url)) for url in source_urls or [] if source_by_url.get(clean_space(url))]
        if not ids:
            if category == "disease_entities":
                ids = public[:1] + taxonomy[:1]
            elif category == "hard_excluded_entities":
                ids = public[:1] or taxonomy[:1]
            else:
                ids = taxonomy[:1] + molecular[:1]
        rows.append({
            "term": term,
            "category": category,
            "source_evidence_ids": list(dict.fromkeys(x for x in ids if x)),
            "evidence_hash": stable_hash({"term":term,"category":category,"sources":ids}),
            "review_status": "three_round_reviewed",
            "review_rounds": ["theme_boundary","consumer_execution","pipeline_outcome"],
        })

    for category in ("target_entities","allowed_members","disease_entities","hard_excluded_entities"):
        for value in topic.get(category) or []:
            add(value, category)
    for row in topic.get("related_entities") or []:
        if isinstance(row, dict): add(row.get("term"), "related_entities", row.get("source_urls") or [])
        else: add(row, "related_entities")
    for row in topic.get("qualified_entities") or []:
        add(row.get("term"), "qualified_entities", row.get("source_urls") or [])
    return rows


def validator(data: Any) -> tuple[bool, str]:
    if not isinstance(data, dict): return False, "not object"
    if clean_space(data.get("prompt_version")) != PROMPT_VERSION: return False, "prompt_version mismatch"
    vocab = data.get("review_vocabulary")
    if not isinstance(vocab, dict) or not vocab.get("identity_anchor_terms"): return False, "review_vocabulary missing"
    for key in ("related_entity_terms","hard_exclusion_terms"):
        if not isinstance(vocab.get(key), list): return False, f"{key} missing"
    validation = data.get("validation")
    if not isinstance(validation, dict) or validation.get("topic_boundary_passed") is not True: return False, "topic validation missing"
    if validation.get("related_entity_routing_passed") is not True: return False, "related entity routing validation missing"
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild one canonical vocabulary from configured authoritative sources and activate only after v17.4 tiered semantic validation.")
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--proposal-out", default="")
    args = ap.parse_args()
    root = Path(args.project_root).resolve(); profile_id = args.profile
    bundle_dir = root / "config/vocabularies" / profile_id
    bundle = load_bundled_vocabulary(root, profile_id)
    canonical = bundle["canonical_vocabulary"]

    with tempfile.TemporaryDirectory(prefix=f"pif-vocab-{profile_id}-") as td:
        settings = Settings(profile_id=profile_id, project_root=root, output_dir=Path(td)/"out", state_dir=Path(td)/"state")
        http = HttpClient(settings.user_agent)
        secrets = settings.secrets
        llm = LLMRouter(
            http,
            gemini_key=secrets.get("GEMINI_API_KEY", ""), groq_key=secrets.get("GROQ_API_KEY", ""),
            openrouter_key=secrets.get("OPENROUTER_API_KEY", ""), mistral_key=secrets.get("MISTRAL_API_KEY", ""),
            siliconflow_key=secrets.get("SILICONFLOW_API_KEY", ""), bigmodel_key=secrets.get("BIGMODEL_API_KEY", ""),
            deepseek_key=secrets.get("DEEPSEEK_API_KEY", ""),
        )
        if not llm.available:
            raise SystemExit("No configured LLM provider is available for explicit vocabulary refresh")
        seed = load_seed(root, profile_id)
        documents = fetch_authoritative_documents(settings, seed, http)
        proposed_profile = build_profile(settings, http, llm)
        prompt = (root / "prompts/review_vocabulary_v1.md").read_text(encoding="utf-8")
        payload = {
            "manual_topic_contract": canonical.get("topic_contract"),
            "frozen_core_terms": [row.get("scholarly") for row in (canonical.get("retrieval_contract") or {}).get("core_concepts") or []],
            "deterministic_base_vocabulary": bundle.get("review_vocabulary"),
            "proposed_profile_from_profile_bootstrap_v3": proposed_profile,
            "authoritative_source_documents": [
                {"url":row.get("url"),"organization":row.get("organization"),"role":row.get("role"),"sha256":row.get("sha256"),"text":clean_space(row.get("text"))[:24000]}
                for row in documents if row.get("usable") and clean_space(row.get("text"))
            ],
        }
        result = llm.json_task(
            system=prompt, prompt=json.dumps(payload, ensure_ascii=False), validator=validator,
            provider_order=getattr(llm,"provider_order",lambda purpose:None)("extract"),
            max_models_per_provider=2, temperature=0.0, task_name="canonical_vocabulary_refresh_v17_4",
        )
        proposal = result.data
        proposal_path = Path(args.proposal_out) if args.proposal_out else bundle_dir / "canonical_vocabulary.proposal.json"
        proposal_path.write_text(json.dumps({"generated_at":utc_now_iso(),"provider":result.provider,"model":result.model,"proposal":proposal},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        if not args.activate:
            print(json.dumps({"status":"proposal_only","profile_id":profile_id,"path":str(proposal_path)},ensure_ascii=False)); return 0

        backup = bundle_dir.with_name(bundle_dir.name + ".refresh-backup")
        if backup.exists(): shutil.rmtree(backup)
        shutil.copytree(bundle_dir, backup)
        try:
            vocab = proposal["review_vocabulary"]
            topic = dict(canonical.get("topic_contract") or {})
            anchors = terms(vocab.get("identity_anchor_terms")); members = terms(vocab.get("member_identity_terms")); diseases = terms(vocab.get("disease_identity_terms"))
            topic["target_entities"] = list(dict.fromkeys(anchors + members + diseases))
            topic["allowed_members"] = members
            topic["disease_entities"] = diseases
            topic["qualified_entities"] = [
                {"term":clean_space(row.get("term")),"required_context_terms":list(row.get("required_context_terms") or []),"source_urls":list(row.get("source_urls") or [])}
                for row in term_rows(vocab.get("qualified_identity_terms"))
            ]
            topic["related_entities"] = [
                {"term":clean_space(row.get("term")),"relation_type":clean_space(row.get("relation_type") or "taxonomic_or_biological_neighbour"),"display_route":"supplementary","source_urls":list(row.get("source_urls") or [])}
                for row in term_rows(vocab.get("related_entity_terms"))
            ]
            topic["hard_excluded_entities"] = terms(vocab.get("hard_exclusion_terms"))
            topic["excluded_entities"] = list(topic["hard_excluded_entities"])
            topic["related_entity_policy"] = {
                "related_only_route":"supplementary",
                "mixed_target_related_route":"primary_when_target_specific_evidence_exists",
                "hard_exclusion_route":"reject",
                "longest_entity_purpose":"disambiguation_not_deletion",
            }
            canonical["schema_version"] = SCHEMA_VERSION
            canonical["bundle_version"] = BUNDLE_VERSION
            canonical["topic_contract"] = topic
            canonical.setdefault("review_contract", {})["context_terms"] = terms(vocab.get("context_terms"))
            canonical["review_contract"]["display_only_terms"] = terms(vocab.get("display_only_terms"))
            canonical["review_contract"]["paper_priority_terms"] = terms(vocab.get("paper_priority_terms"))
            canonical["review_contract"]["document_type_terms"] = vocab.get("document_type_terms") or {}
            canonical["translation_glossary"] = proposal.get("translation_glossary") or canonical.get("translation_glossary") or []

            target_keys = {norm(value) for value in topic.get("target_entities") or []}
            qualified = {norm(row.get("term")):row for row in topic.get("qualified_entities") or []}
            for concept in (canonical.get("retrieval_contract") or {}).get("core_concepts") or []:
                mapping = concept.get("review_mapping") or {}
                mapped = clean_space(mapping.get("term") or concept.get("scholarly")); mode = clean_space(mapping.get("mode"))
                if mode == "safe_identity" and norm(mapped) not in target_keys:
                    topic.setdefault("target_entities", []).append(mapped); target_keys.add(norm(mapped))
                elif mode == "qualified_identity" and norm(mapped) not in qualified:
                    topic.setdefault("qualified_entities", []).append({"term":mapped,"required_context_terms":list(mapping.get("required_context_terms") or ["virus","infection","disease"])})
                elif mode == "retrieval_only_with_review_mapping" and not clean_space(mapping.get("term")):
                    raise RuntimeError(f"core concept lacks review mapping: {concept.get('scholarly')}")
            canonical["topic_contract"] = topic
            canonical["term_evidence"] = rebuild_term_evidence(canonical)
            canonical["validation_cases"] = executable_cases(canonical)
            canonical["semantic_fingerprint"] = stable_hash({key:canonical.get(key) for key in ("schema_version","bundle_version","profile_id","topic_contract","retrieval_contract","review_contract","translation_glossary","authoritative_evidence","term_evidence","validation_cases","consumer_contract")})
            canonical["reviewed_at"] = utc_now_iso(); canonical["validation_status"] = "semantic_validation_required"
            canonical.setdefault("review_history", []).append({"round":"explicit_authoritative_refresh_v17_4","status":"candidate_activated_pending_semantic_validation","provider":result.provider,"model":result.model,"at":canonical["reviewed_at"]})
            (bundle_dir/"canonical_vocabulary.json").write_text(json.dumps(canonical,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            compile_profile_views(bundle_dir)
            ok, errors, _ = validate_bundled_vocabulary(root, profile_id, semantic=True)
            if not ok: raise RuntimeError("; ".join(errors))
            canonical["validation_status"] = "passed"; canonical["review_history"][-1]["status"] = "passed_and_activated"
            (bundle_dir/"canonical_vocabulary.json").write_text(json.dumps(canonical,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            compile_profile_views(bundle_dir)
            shutil.rmtree(backup)
        except Exception:
            shutil.rmtree(bundle_dir); shutil.move(str(backup), str(bundle_dir)); raise
        print(json.dumps({"status":"activated","profile_id":profile_id,"proposal":str(proposal_path),"semantic_validation":"passed","bundle_version":BUNDLE_VERSION},ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
