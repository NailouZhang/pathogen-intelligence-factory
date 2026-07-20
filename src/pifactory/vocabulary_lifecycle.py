from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import os
from typing import Any

from .authority_sources import fetch_authoritative_documents, source_bundle_hash
from .config import Settings, load_seed
from .http import HttpClient
from .llm import LLMError, LLMRouter
from .profile_contract import SCHEMA_VERSION
from .utils import clean_space, dump_json, load_json, sha256_text, unique_strings, utc_now_iso

PROMPT_VERSION = "review-vocabulary-v16.0.0-1"
VOCABULARY_KEYS = (
    "identity_anchor_terms",
    "qualified_identity_terms",
    "member_identity_terms",
    "disease_identity_terms",
    "context_terms",
    "display_only_terms",
    "exclusion_terms",
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
    if category in {"context_terms", "display_only_terms"}:
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
    """Return a profile with a frozen, fingerprinted post-retrieval vocabulary.

    The LLM is called only when the vocabulary is absent, explicitly refreshed,
    the profile/five core terms changed, or the prompt version changed. A failed
    rebuild never reuses a vocabulary associated with a different semantic
    fingerprint.
    """

    seed = load_seed(settings.project_root, settings.profile_id)
    fingerprints = semantic_fingerprints(seed, profile)
    target_dir = settings.state_dir.parent / "profiles" / settings.profile_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "review_vocabulary.json"
    previous = load_json(path, default={}) or {}
    previous_fingerprint = clean_space(previous.get("profile_semantic_fingerprint"))
    requested = bool(settings.refresh_review_vocabulary)
    needs_rebuild = requested or previous_fingerprint != fingerprints["profile_semantic_fingerprint"]
    trigger = (
        "explicit_refresh" if requested
        else "missing" if not previous
        else "profile_semantic_change" if previous_fingerprint != fingerprints["profile_semantic_fingerprint"]
        else "unchanged"
    )
    base = _deterministic_vocabulary(seed, profile)
    audit: dict[str, Any] = {
        "policy_version": "v16-fingerprinted-review-vocabulary-1",
        **fingerprints,
        "previous_profile_semantic_fingerprint": previous_fingerprint,
        "rebuild_required": needs_rebuild,
        "trigger": trigger,
        "generated_at": utc_now_iso(),
        "generated_by": "persisted_frozen_vocabulary",
        "llm_attempts": [],
        "validation": {},
        "cache_invalidation_required": needs_rebuild,
    }

    if not needs_rebuild and isinstance(previous.get("review_vocabulary"), dict):
        profile["vocabulary"] = deepcopy(previous["review_vocabulary"])
        profile["translation_glossary"] = deepcopy(previous.get("translation_glossary") or profile.get("translation_glossary") or [])
        profile.update(fingerprints)
        audit["generated_by"] = clean_space(previous.get("generated_by")) or "persisted_frozen_vocabulary"
        return profile, audit

    documents: list[dict[str, Any]] = []
    proposal: dict[str, Any] | None = None
    error = ""
    if not demo and llm.available:
        try:
            documents = fetch_authoritative_documents(settings, seed, http)
            usable = [row for row in documents if row.get("usable") and clean_space(row.get("text"))]
            allowed_urls = {clean_space(row.get("url")) for row in documents if clean_space(row.get("url"))}
            minimum = int((seed.get("source_policy") or {}).get("minimum_usable_sources", 1))
            if len(usable) >= minimum:
                system = (settings.project_root / "prompts" / "review_vocabulary_v1.md").read_text(encoding="utf-8")
                payload = {
                    "profile_id": settings.profile_id,
                    "frozen_core_terms": _core_terms(profile),
                    "manual_topic_contract": seed,
                    "deterministic_base_vocabulary": base,
                    "authoritative_source_documents": [
                        {
                            "url": row.get("url"),
                            "organization": row.get("organization"),
                            "role": row.get("role"),
                            "sha256": row.get("sha256"),
                            "text": clean_space(row.get("text"))[:24000],
                        }
                        for row in usable
                    ],
                    "prompt_version": PROMPT_VERSION,
                }
                result = llm.json_task(
                    system=system,
                    prompt=json.dumps(payload, ensure_ascii=False),
                    provider_order=llm.provider_order("extract"),
                    validator=lambda data: _validate_proposal(data, profile, allowed_urls),
                    max_models_per_provider=2,
                    temperature=0.0,
                    task_name="review_vocabulary_build",
                )
                proposal = dict(result.data) if isinstance(result.data, dict) else None
                audit["llm_attempts"] = result.attempts
                audit["generated_by"] = f"{result.provider}:{result.model}"
                audit["validation"] = proposal.get("validation") if proposal else {}
            else:
                error = f"usable authoritative sources {len(usable)} < required {minimum}"
        except LLMError as exc:
            error = clean_space(exc)[:1800]
            audit["llm_attempts"] = list(exc.attempts or [])
        except Exception as exc:  # source/network failures are deterministic fallbacks
            error = clean_space(exc)[:1800]

    if proposal:
        vocabulary, glossary = _merge_vocabulary(base, proposal, seed)
    else:
        vocabulary = base
        glossary = deepcopy(profile.get("translation_glossary") or seed.get("translation_glossary") or [])
        audit["generated_by"] = "deterministic_seed_vocabulary_after_llm_unavailable" if error else "deterministic_seed_vocabulary"
        audit["rebuild_error"] = error
        audit["validation"] = {
            "topic_boundary_passed": True,
            "frozen_core_terms_unchanged": True,
            "deterministic_fallback": True,
        }

    record = {
        "schema_version": 1,
        **fingerprints,
        "profile_id": settings.profile_id,
        "generated_at": utc_now_iso(),
        "generated_by": audit["generated_by"],
        "source_bundle_hash": source_bundle_hash(documents) if documents else "",
        "frozen_core_terms": _core_terms(profile),
        "review_vocabulary": vocabulary,
        "translation_glossary": glossary,
        "validation": audit["validation"],
        "rebuild_error": audit.get("rebuild_error", ""),
    }
    _atomic_dump_json(path, record)
    profile["vocabulary"] = vocabulary
    profile["translation_glossary"] = glossary
    profile.update(fingerprints)
    return profile, audit
