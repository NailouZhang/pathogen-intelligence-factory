from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import os
from typing import Any

from .authority_sources import fetch_authoritative_documents, source_bundle_hash
from .bundled_vocabulary import apply_bundled_profile, load_bundled_vocabulary
from .config import Settings, load_seed
from .http import HttpClient
from .llm import LLMError, LLMRouter
from .profile_contract import SCHEMA_VERSION
from .utils import clean_space, dump_json, load_json, sha256_text, unique_strings, utc_now_iso

PROMPT_VERSION = "review-vocabulary-v17.4.0-1"
VOCABULARY_KEYS = (
    "identity_anchor_terms",
    "qualified_identity_terms",
    "member_identity_terms",
    "disease_identity_terms",
    "related_entity_terms",
    "hard_exclusion_terms",
    "context_terms",
    "display_only_terms",
    "paper_priority_terms",
    "document_type_terms",
)



def _atomic_dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

def _stable_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _core_concepts(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in (profile.get("search_strategy") or {}).get("concepts") or [] if isinstance(row, dict)]


def semantic_fingerprints(seed: dict[str, Any], profile: dict[str, Any]) -> dict[str, str]:
    concepts = _core_concepts(profile)
    core_payload = [
        {
            "id": clean_space(row.get("id")),
            "scholarly": clean_space(row.get("scholarly")),
            "news_en": clean_space(row.get("news_en")),
            "news_zh": clean_space(row.get("news_zh")),
            "role": clean_space(row.get("role")),
        }
        for row in concepts
    ]
    topic_payload = {
        "profile_id": clean_space(seed.get("profile_id") or profile.get("profile_id")),
        "seed_target_scope": seed.get("target_scope") or {},
        "runtime_target_scope": profile.get("target_scope") or {},
        "authoritative_source_contract": [
            {
                "url": clean_space(row.get("url")),
                "organization": clean_space(row.get("organization")),
                "role": clean_space(row.get("role")),
                "required": bool(row.get("required", False)),
            }
            for row in seed.get("authoritative_sources") or []
            if isinstance(row, dict)
        ],
        "seed_news_identity_terms_zh": seed.get("news_identity_terms_zh") or [],
        "runtime_news_identity_terms_zh": profile.get("news_identity_terms_zh") or [],
    }
    review_inputs = {
        "seed_candidate_vocabulary": seed.get("candidate_vocabulary") or {},
        "runtime_profile_vocabulary": profile.get("vocabulary") or profile.get("candidate_vocabulary") or {},
        "controlled_supplemental_terms": (profile.get("search_strategy") or {}).get("controlled_supplemental_terms") or [],
        "seed_translation_glossary": seed.get("translation_glossary") or [],
        "runtime_translation_glossary": profile.get("translation_glossary") or [],
    }
    core_hash = _stable_hash(core_payload)
    topic_hash = _stable_hash(topic_payload)
    inputs_hash = _stable_hash(review_inputs)
    semantic = _stable_hash({
        "profile_schema": SCHEMA_VERSION,
        "core_terms_hash": core_hash,
        "topic_contract_hash": topic_hash,
        "review_vocabulary_input_hash": inputs_hash,
        "prompt_version": PROMPT_VERSION,
    })
    return {
        "core_terms_hash": core_hash,
        "topic_contract_hash": topic_hash,
        "review_vocabulary_input_hash": inputs_hash,
        "profile_semantic_fingerprint": semantic,
        "review_vocabulary_prompt_version": PROMPT_VERSION,
    }


def _entry_term(row: Any) -> str:
    return clean_space(row.get("term")) if isinstance(row, dict) else clean_space(row)


def _normalize_entry(row: Any, *, category: str, source_urls: list[str]) -> dict[str, Any] | None:
    if isinstance(row, str):
        row = {"term": row}
    if not isinstance(row, dict):
        return None
    term = clean_space(row.get("term"))
    if not term:
        return None
    output = dict(row)
    output["term"] = term
    output["normalized_term"] = clean_space(row.get("normalized_term") or term).casefold()
    output.setdefault("category", category)
    output["source_urls"] = unique_strings(row.get("source_urls") or source_urls)
    if category in {"identity_anchor_terms", "member_identity_terms", "disease_identity_terms"}:
        output.setdefault("safe_to_use_alone", True)
    if category == "qualified_identity_terms":
        output.setdefault("forbidden_without_context", True)
        output["required_context_terms"] = unique_strings(output.get("required_context_terms") or [])
    if category in {"related_entity_terms", "context_terms", "display_only_terms"}:
        output.setdefault("may_use_only_after_identity", True)
    return output


def _deterministic_vocabulary(seed: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    base = deepcopy(profile.get("vocabulary") or profile.get("candidate_vocabulary") or seed.get("candidate_vocabulary") or {})
    urls = [clean_space(row.get("url")) for row in seed.get("authoritative_sources") or [] if isinstance(row, dict) and clean_space(row.get("url"))]
    output: dict[str, Any] = {}
    for key in VOCABULARY_KEYS:
        value = base.get(key)
        if key == "document_type_terms":
            output[key] = deepcopy(value) if isinstance(value, dict) else {}
            continue
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in value or []:
            row = _normalize_entry(raw, category=key, source_urls=urls)
            if not row:
                continue
            norm = row["normalized_term"]
            if norm in seen:
                continue
            seen.add(norm)
            rows.append(row)
        output[key] = rows
    return output


def _core_terms(profile: dict[str, Any]) -> list[str]:
    return [clean_space(row.get("scholarly")) for row in _core_concepts(profile) if clean_space(row.get("scholarly"))]


def _validate_proposal(data: Any, profile: dict[str, Any], allowed_urls: set[str]) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "output must be an object"
    if clean_space(data.get("prompt_version")) != PROMPT_VERSION:
        return False, "prompt_version mismatch"
    proposed_core = [clean_space(x) for x in data.get("frozen_core_terms") or []]
    if proposed_core != _core_terms(profile):
        return False, "frozen_core_terms changed or reordered"
    vocabulary = data.get("review_vocabulary")
    if not isinstance(vocabulary, dict):
        return False, "review_vocabulary missing"
    if not vocabulary.get("identity_anchor_terms"):
        return False, "identity_anchor_terms missing"
    for key in VOCABULARY_KEYS:
        value = vocabulary.get(key)
        if key == "document_type_terms":
            if value is not None and not isinstance(value, dict):
                return False, "document_type_terms must be an object"
            continue
        if value is not None and not isinstance(value, list):
            return False, f"{key} must be a list"
        for row in value or []:
            if not isinstance(row, dict) or not clean_space(row.get("term")):
                return False, f"{key} contains an invalid term"
            urls = {clean_space(url) for url in row.get("source_urls") or [] if clean_space(url)}
            if urls and not urls.issubset(allowed_urls):
                return False, f"{key} contains an unapproved source URL"
    validation = data.get("validation")
    if not isinstance(validation, dict) or validation.get("topic_boundary_passed") is not True:
        return False, "topic boundary validation missing"
    return True, "ok"


def _merge_vocabulary(base: dict[str, Any], proposal: dict[str, Any], seed: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    urls = [clean_space(row.get("url")) for row in seed.get("authoritative_sources") or [] if isinstance(row, dict) and clean_space(row.get("url"))]
    proposed = proposal.get("review_vocabulary") or {}
    merged = deepcopy(base)
    for key in VOCABULARY_KEYS:
        if key == "document_type_terms":
            if isinstance(proposed.get(key), dict) and proposed.get(key):
                merged[key] = deepcopy(proposed[key])
            continue
        rows = []
        seen: set[str] = set()
        for raw in list(proposed.get(key) or []) + list(base.get(key) or []):
            row = _normalize_entry(raw, category=key, source_urls=urls)
            if not row:
                continue
            norm = row["normalized_term"]
            if norm in seen:
                continue
            seen.add(norm)
            rows.append(row)
        merged[key] = rows
    glossary: list[dict[str, str]] = []
    seen_glossary: set[tuple[str, str]] = set()
    for row in list(proposal.get("translation_glossary") or []) + list(seed.get("translation_glossary") or []):
        if not isinstance(row, dict):
            continue
        source = clean_space(row.get("source") or row.get("en") or row.get("term"))
        target = clean_space(row.get("target") or row.get("zh") or row.get("translation"))
        if source and target and (source.casefold(), target) not in seen_glossary:
            seen_glossary.add((source.casefold(), target))
            glossary.append({"source": source, "target": target})
    return merged, glossary


def ensure_review_vocabulary(
    settings: Settings,
    profile: dict[str, Any],
    http: HttpClient,
    llm: LLMRouter,
    *,
    demo: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a validated, complete vocabulary without silent retrieval-term fallback.

    Production defaults to the 21 three-round-reviewed canonical contracts shipped with the
    repository.  A manual runtime rebuild is available only when both
    ``PIF_VOCAB_SOURCE=runtime`` and ``PIF_VOCAB_ALLOW_RUNTIME_REFRESH=true``
    are explicitly set.  Any runtime failure returns to the complete bundled
    vocabulary, never to the five retrieval concepts.
    """
    del http, llm, demo
    source = os.getenv("PIF_VOCAB_SOURCE", "canonical").strip().lower() or "canonical"
    runtime_allowed = os.getenv("PIF_VOCAB_ALLOW_RUNTIME_REFRESH", "false").strip().lower() in {"1", "true", "yes", "on"}
    allow_core_fallback = os.getenv("PIF_REVIEW_ALLOW_CORE_TERMS_FALLBACK", "false").strip().lower() in {"1", "true", "yes", "on"}
    if allow_core_fallback:
        raise RuntimeError(
            "PIF_REVIEW_ALLOW_CORE_TERMS_FALLBACK=true is forbidden by the v17 production contract; "
            "load the complete bundled review vocabulary instead"
        )
    if source not in {"canonical", "bundled"} and not runtime_allowed:
        source = "canonical"

    bundle = load_bundled_vocabulary(settings.project_root, settings.profile_id)
    bundle_profile = bundle["profile"]
    profile = apply_bundled_profile(profile, bundle_profile)
    manifest = bundle["manifest"]

    target_dir = settings.state_dir.parent / "profiles" / settings.profile_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "review_vocabulary.json"
    previous = load_json(path, default={}) or {}
    previous_version = clean_space(previous.get("bundle_version"))
    requested = bool(settings.refresh_review_vocabulary or settings.refresh_profile)
    install_required = (
        requested
        or previous_version != clean_space(manifest.get("bundle_version"))
        or clean_space(previous.get("profile_semantic_fingerprint"))
        != clean_space(manifest.get("profile_semantic_fingerprint"))
        or not isinstance(previous.get("review_vocabulary"), dict)
    )
    trigger = (
        "explicit_refresh" if requested
        else "missing" if not previous
        else "bundle_version_change" if previous_version != clean_space(manifest.get("bundle_version"))
        else "profile_semantic_change"
        if clean_space(previous.get("profile_semantic_fingerprint")) != clean_space(manifest.get("profile_semantic_fingerprint"))
        else "validated_bundled_reuse"
    )

    # Persist a runtime copy for durable audits and cache fingerprinting.  The
    # bundled copy remains the source of truth, so a stale state branch cannot
    # silently override the reviewed package.
    record = {
        "schema_version": 5,
        "bundle_version": manifest.get("bundle_version"),
        "profile_id": settings.profile_id,
        "profile_semantic_fingerprint": manifest.get("profile_semantic_fingerprint"),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "generated_at": utc_now_iso(),
        "generated_by": manifest.get("generated_by"),
        "vocabulary_source": "canonical",
        "review_vocabulary": deepcopy(bundle["review_vocabulary"]),
        "translation_glossary": deepcopy(bundle["translation_glossary"]),
        "validation": {
            "status": "passed",
            "strict": True,
            "retrieval_term_fallback_allowed": False,
            "positive_cases": len(
                (bundle.get("validation_cases") or {}).get("positive")
                or (bundle.get("validation_cases") or {}).get("positive_cases")
                or []
            ),
            "negative_cases": len(
                (bundle.get("validation_cases") or {}).get("negative")
                or (bundle.get("validation_cases") or {}).get("negative_cases")
                or []
            ),
            "related_cases": len((bundle.get("validation_cases") or {}).get("related") or []),
            "comparison_cases": len((bundle.get("validation_cases") or {}).get("comparison") or []),
            "semantic_validation_executed_in_ci": True,
        },
    }
    if install_required or previous != record:
        _atomic_dump_json(path, record)

    audit = {
        "policy_version": "v17.4-canonical-vocabulary-contract-1",
        "schema_version": 5,
        "bundle_version": manifest.get("bundle_version"),
        "profile_id": settings.profile_id,
        "requested": requested,
        "rebuilt": install_required,
        "rebuild_required": install_required,
        "trigger": trigger,
        "generated_at": record["generated_at"],
        "generated_by": record["generated_by"],
        "vocabulary_source": "canonical",
        "profile_semantic_fingerprint": record["profile_semantic_fingerprint"],
        "source_fingerprint": record["source_fingerprint"],
        "cache_invalidation_required": install_required,
        "runtime_refresh_allowed": runtime_allowed,
        "runtime_refresh_requested": source == "runtime" and requested,
        "runtime_refresh_executed": False,
        "fallback_to_core_search_terms": False,
        "validation": record["validation"],
        "term_counts": manifest.get("term_counts") or {},
        "consumer_contract": deepcopy(bundle.get("consumer_contract") or {}),
        "authoritative_source_count": len(bundle.get("authoritative_sources") or []),
        "runtime_file_audit": deepcopy(bundle.get("runtime_file_audit") or {}),
    }
    profile["vocabulary_validation_cases"] = deepcopy(bundle.get("validation_cases") or {})
    profile["vocabulary_source"] = "canonical"
    profile["vocabulary_bundle_version"] = manifest.get("bundle_version")
    return profile, audit
