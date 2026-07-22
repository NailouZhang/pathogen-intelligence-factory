from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .utils import clean_space


SCHEMA_VERSION = 5
COMPILER_VERSION = "canonical-compiler-v17.4"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_space(value).casefold()).strip()


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entry(
    term: str,
    category: str,
    source_urls: list[str],
    *,
    safe: bool = False,
    contexts: list[str] | None = None,
    relation_type: str = "",
    display_route: str = "",
) -> dict[str, Any]:
    row = {
        "term": clean_space(term),
        "normalized_term": _norm(term),
        "category": category,
        "source_urls": source_urls,
        "safe_to_use_alone": bool(safe),
    }
    if contexts:
        row["required_context_terms"] = list(dict.fromkeys(clean_space(x) for x in contexts if clean_space(x)))
    if relation_type:
        row["relation_type"] = relation_type
    if display_route:
        row["display_route"] = display_route
    return row


def _related_entries(topic: dict[str, Any], urls: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in topic.get("related_entities") or []:
        if isinstance(value, dict):
            term = clean_space(value.get("term"))
            relation_type = clean_space(value.get("relation_type") or "taxonomic_or_biological_neighbour")
            route = clean_space(value.get("display_route") or "supplementary")
        else:
            term = clean_space(value)
            relation_type = "taxonomic_or_biological_neighbour"
            route = "supplementary"
        if term:
            rows.append(
                _entry(
                    term,
                    "related_entity_terms",
                    urls,
                    safe=False,
                    relation_type=relation_type,
                    display_route=route,
                )
            )
    return rows


def compile_profile_views(bundle_dir: Path) -> dict[str, Any]:
    canonical_path = bundle_dir / "canonical_vocabulary.json"
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    topic = canonical.get("topic_contract") or {}
    retrieval = canonical.get("retrieval_contract") or {}
    review = canonical.get("review_contract") or {}
    evidence = canonical.get("authoritative_evidence") or []
    urls = list(dict.fromkeys(clean_space(x.get("url")) for x in evidence if clean_space(x.get("url"))))
    members = list(topic.get("allowed_members") or [])
    diseases = list(topic.get("disease_entities") or [])
    member_keys = {_norm(x) for x in members}
    disease_keys = {_norm(x) for x in diseases}
    anchors = [x for x in topic.get("target_entities") or [] if _norm(x) not in member_keys | disease_keys]
    hard_excluded = list(topic.get("hard_excluded_entities") or topic.get("excluded_entities") or [])
    related_entries = _related_entries(topic, urls)
    rv = {
        "identity_anchor_terms": [_entry(x, "identity_anchor_terms", urls, safe=True) for x in anchors],
        "qualified_identity_terms": [
            _entry(
                x.get("term"),
                "qualified_identity_terms",
                urls,
                safe=False,
                contexts=x.get("required_context_terms") or [],
            )
            for x in topic.get("qualified_entities") or []
            if clean_space(x.get("term"))
        ],
        "member_identity_terms": [_entry(x, "member_identity_terms", urls, safe=True) for x in members],
        "disease_identity_terms": [_entry(x, "disease_identity_terms", urls, safe=True) for x in diseases],
        "related_entity_terms": related_entries,
        "context_terms": [_entry(x, "context_terms", urls) for x in review.get("context_terms") or []],
        "display_only_terms": [_entry(x, "display_only_terms", urls) for x in review.get("display_only_terms") or []],
        "hard_exclusion_terms": [_entry(x, "hard_exclusion_terms", urls) for x in hard_excluded],
        # Compatibility key consumed by older helpers. It now contains only
        # terminal hard exclusions, never biological near neighbours.
        "exclusion_terms": [_entry(x, "exclusion_terms", urls) for x in hard_excluded],
        "paper_priority_terms": [_entry(x, "paper_priority_terms", urls) for x in review.get("paper_priority_terms") or []],
        "document_type_terms": deepcopy(review.get("document_type_terms") or {}),
    }
    profile_path = bundle_dir / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.exists() else {}
    fingerprint = clean_space(canonical.get("semantic_fingerprint"))
    related_terms = [row["term"] for row in related_entries]
    profile.update(
        {
            "schema_version": "5.0-canonical-derived",
            "derived_from_semantic_fingerprint": fingerprint,
            "profile_id": canonical.get("profile_id"),
            "display_name_en": canonical.get("display_name_en"),
            "display_name_zh": canonical.get("display_name_zh"),
            "topic_contract": deepcopy(topic),
            "target_scope": {
                "topic_en": topic.get("topic_en"),
                "topic_zh": topic.get("topic_zh"),
                "scope_included": [topic.get("scope_statement")],
                "scope_related": related_terms,
                "scope_excluded": hard_excluded,
                "allowed_members": members,
                "excluded_members": hard_excluded,
                "required_identity_concepts": list(topic.get("target_entities") or []) + diseases,
                "non_target_near_neighbors": related_terms,
                "related_entity_policy": deepcopy(topic.get("related_entity_policy") or {}),
            },
            "search_strategy": {
                **dict(profile.get("search_strategy") or {}),
                "schema_version": "5.0",
                "concepts": deepcopy(retrieval.get("core_concepts") or []),
                "controlled_supplemental_terms": deepcopy(retrieval.get("controlled_supplemental_terms") or []),
            },
            "vocabulary_source": "canonical_v17.4",
        }
    )
    _write(profile_path, profile)
    _write(
        bundle_dir / "retrieval_vocabulary.json",
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": canonical.get("profile_id"),
            "derived_from_semantic_fingerprint": fingerprint,
            "frozen_core_concepts": retrieval.get("core_concepts") or [],
            "controlled_supplemental_terms": retrieval.get("controlled_supplemental_terms") or [],
            "policy": "derived from canonical_vocabulary.json; every core concept has review_mapping",
        },
    )
    _write(
        bundle_dir / "review_vocabulary.json",
        {
            "schema_version": SCHEMA_VERSION,
            "bundle_version": canonical.get("bundle_version"),
            "profile_id": canonical.get("profile_id"),
            "generated_by": COMPILER_VERSION,
            "profile_semantic_fingerprint": fingerprint,
            "derived_from_semantic_fingerprint": fingerprint,
            "review_vocabulary": rv,
        },
    )
    _write(
        bundle_dir / "exclusion_vocabulary.json",
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": canonical.get("profile_id"),
            "derived_from_semantic_fingerprint": fingerprint,
            "related_entity_terms": related_entries,
            "hard_exclusion_terms": rv["hard_exclusion_terms"],
            "exclusion_terms": rv["exclusion_terms"],
            "policy": "related entities route to supplementary; only hard exclusions are terminal",
        },
    )
    _write(
        bundle_dir / "translation_glossary.json",
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": canonical.get("profile_id"),
            "derived_from_semantic_fingerprint": fingerprint,
            "translation_glossary": canonical.get("translation_glossary") or [],
        },
    )
    _write(
        bundle_dir / "authoritative_sources.json",
        {
            "schema_version": SCHEMA_VERSION,
            "profile_id": canonical.get("profile_id"),
            "derived_from_semantic_fingerprint": fingerprint,
            "sources": evidence,
        },
    )
    validation_view = deepcopy(canonical.get("validation_cases") or {})
    validation_view["derived_from_semantic_fingerprint"] = fingerprint
    _write(bundle_dir / "validation_cases.json", validation_view)
    files = [
        "canonical_vocabulary.json",
        "profile.json",
        "retrieval_vocabulary.json",
        "review_vocabulary.json",
        "exclusion_vocabulary.json",
        "translation_glossary.json",
        "authoritative_sources.json",
        "validation_cases.json",
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_version": canonical.get("bundle_version"),
        "profile_id": canonical.get("profile_id"),
        "generated_at": canonical.get("reviewed_at")
        or canonical.get("generated_at")
        or datetime.now(timezone.utc).isoformat(),
        "generated_by": COMPILER_VERSION,
        "profile_semantic_fingerprint": canonical.get("semantic_fingerprint"),
        "validation_status": canonical.get("validation_status") or "semantic_validation_required",
        "files": {name: _sha(bundle_dir / name) for name in files},
        "term_counts": {key: len(value) if isinstance(value, list) else len(value or {}) for key, value in rv.items()},
    }
    _write(bundle_dir / "manifest.json", manifest)
    return manifest
