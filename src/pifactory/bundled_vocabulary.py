from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .utils import clean_space, sha256_text

BUNDLE_SCHEMA_VERSION = 5
DEFAULT_BUNDLE_VERSION = "2026.07-v17.4"
REQUIRED_FILES = (
    "manifest.json",
    "canonical_vocabulary.json",
    "profile.json",
    "retrieval_vocabulary.json",
    "review_vocabulary.json",
    "exclusion_vocabulary.json",
    "translation_glossary.json",
    "authoritative_sources.json",
    "validation_cases.json",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def bundle_dir(project_root: Path, profile_id: str) -> Path:
    return project_root / "config" / "vocabularies" / profile_id


def _terms(rows: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        term = clean_space(row.get("term") if isinstance(row, dict) else row)
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            out.append(term)
    return out


def _profile_from_canonical(canonical: dict[str, Any], compatibility: dict[str, Any]) -> dict[str, Any]:
    topic = canonical.get("topic_contract") or {}
    retrieval = canonical.get("retrieval_contract") or {}
    review = canonical.get("review_contract") or {}
    related = deepcopy(topic.get("related_entities") or [])
    related_terms = _terms(related)
    hard_excluded = _terms(topic.get("hard_excluded_entities") or topic.get("excluded_entities") or [])
    profile = deepcopy(compatibility)
    profile.update(
        {
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
                "allowed_members": deepcopy(topic.get("allowed_members") or []),
                "excluded_members": hard_excluded,
                "required_identity_concepts": deepcopy(
                    (topic.get("target_entities") or []) + (topic.get("disease_entities") or [])
                ),
                "non_target_near_neighbors": related_terms,
                "related_entity_policy": deepcopy(topic.get("related_entity_policy") or {}),
            },
            "search_strategy": {
                **deepcopy(profile.get("search_strategy") or {}),
                "schema_version": "5.0",
                "max_concepts": int(retrieval.get("max_core_concepts") or 5),
                "concepts": deepcopy(retrieval.get("core_concepts") or []),
                "controlled_supplemental_terms": deepcopy(retrieval.get("controlled_supplemental_terms") or []),
            },
            "translation_glossary": deepcopy(canonical.get("translation_glossary") or []),
            "profile_semantic_fingerprint": clean_space(canonical.get("semantic_fingerprint")),
            "vocabulary_bundle_version": clean_space(canonical.get("bundle_version")) or DEFAULT_BUNDLE_VERSION,
            "vocabulary_source": "canonical_v17.4",
            "authoritative_evidence": deepcopy(canonical.get("authoritative_evidence") or []),
            "consumer_contract": deepcopy(canonical.get("consumer_contract") or {}),
        }
    )
    member_set = set((topic.get("allowed_members") or []) + (topic.get("disease_entities") or []))
    profile["vocabulary"] = {
        "identity_anchor_terms": [
            {"term": x, "safe_to_use_alone": True}
            for x in topic.get("target_entities") or []
            if x not in member_set
        ],
        "member_identity_terms": [
            {"term": x, "safe_to_use_alone": True} for x in topic.get("allowed_members") or []
        ],
        "disease_identity_terms": [
            {"term": x, "safe_to_use_alone": True} for x in topic.get("disease_entities") or []
        ],
        "qualified_identity_terms": deepcopy(topic.get("qualified_entities") or []),
        "related_entity_terms": [
            {
                "term": clean_space(x.get("term") if isinstance(x, dict) else x),
                "safe_to_use_alone": False,
                "relation_type": clean_space(x.get("relation_type") if isinstance(x, dict) else "")
                or "taxonomic_or_biological_neighbour",
                "display_route": clean_space(x.get("display_route") if isinstance(x, dict) else "")
                or "supplementary",
            }
            for x in related
            if clean_space(x.get("term") if isinstance(x, dict) else x)
        ],
        "context_terms": [{"term": x, "safe_to_use_alone": False} for x in review.get("context_terms") or []],
        "display_only_terms": [
            {"term": x, "safe_to_use_alone": False} for x in review.get("display_only_terms") or []
        ],
        "hard_exclusion_terms": [{"term": x, "safe_to_use_alone": False} for x in hard_excluded],
        "exclusion_terms": [{"term": x, "safe_to_use_alone": False} for x in hard_excluded],
        "paper_priority_terms": [
            {"term": x, "safe_to_use_alone": False} for x in review.get("paper_priority_terms") or []
        ],
        "document_type_terms": deepcopy(review.get("document_type_terms") or []),
    }
    return profile


def _validate_core_mapping(canonical: dict[str, Any], errors: list[str]) -> None:
    topic = canonical.get("topic_contract") or {}

    def semantic_key(value: Any) -> str:
        import re

        return re.sub(r"[^a-z0-9]+", " ", clean_space(value).casefold()).strip()

    safe = {semantic_key(x) for x in topic.get("target_entities") or []}
    qualified = {
        semantic_key(x.get("term")): x
        for x in topic.get("qualified_entities") or []
        if isinstance(x, dict) and clean_space(x.get("term"))
    }
    concepts = (canonical.get("retrieval_contract") or {}).get("core_concepts") or []
    if len(concepts) != 5:
        errors.append(f"core concepts must contain exactly 5 entries; got {len(concepts)}")
    for concept in concepts:
        term = clean_space(concept.get("scholarly"))
        mapping = concept.get("review_mapping") or {}
        mode = clean_space(mapping.get("mode"))
        mapped_term = clean_space(mapping.get("term"))
        if not term or not mode or not mapped_term:
            errors.append(f"core concept lacks review_mapping: {term or '<empty>'}")
            continue
        if mode == "safe_identity" and semantic_key(mapped_term) not in safe:
            errors.append(f"core safe mapping not present in target_entities: {term} -> {mapped_term}")
        elif mode == "qualified_identity":
            row = qualified.get(semantic_key(mapped_term))
            if not row or not (row.get("required_context_terms") or mapping.get("required_context_terms")):
                errors.append(f"core qualified mapping lacks contextual rule: {term} -> {mapped_term}")
        elif mode not in {"safe_identity", "qualified_identity", "retrieval_only"}:
            errors.append(f"unsupported core mapping mode: {term} -> {mode}")
        if mode == "retrieval_only" and not mapping.get("review_route"):
            errors.append(f"retrieval_only core concept lacks review_route: {term}")


def _validate_authority(canonical: dict[str, Any], errors: list[str]) -> None:
    evidence = canonical.get("authoritative_evidence") or []
    required = [x for x in evidence if x.get("required")]
    if len(required) < 2:
        errors.append("at least two authoritative evidence records must be required")
    roles = {clean_space(x.get("role")) for x in required}
    if "taxonomy" not in roles:
        errors.append("required authoritative evidence lacks ICTV/taxonomy source")
    if not roles.intersection({"public_health", "clinical", "epidemiology"}):
        errors.append("required authoritative evidence lacks public-health/clinical source")
    ids = {clean_space(x.get("source_id")) for x in evidence}
    evidence_by_id = {clean_space(x.get("source_id")): x for x in evidence}
    for source in evidence:
        payload = {key: source.get(key) for key in ("url", "role", "evidence_statement")}
        expected = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if clean_space(source.get("evidence_sha256")) != expected:
            errors.append(f"authoritative evidence hash mismatch: {source.get('source_id')}")
        if not clean_space(source.get("url")) or not clean_space(source.get("evidence_statement")):
            errors.append(f"authoritative evidence incomplete: {source.get('source_id')}")
    covered: dict[tuple[str, str], int] = {}
    for row in canonical.get("term_evidence") or []:
        if clean_space(row.get("review_status")) != "three_round_reviewed":
            errors.append(f"term not three-round reviewed: {row.get('term')}")
        refs = set(row.get("source_evidence_ids") or [])
        if not refs or not refs <= ids:
            errors.append(f"term evidence references invalid source: {row.get('term')}")
        category = clean_space(row.get("category"))
        term = clean_space(row.get("term")).casefold()
        if category and term:
            covered[(category, term)] = covered.get((category, term), 0) + 1
        # Target and biologically related terms require taxonomy support.
        # Pure lexical/homonym hard exclusions are operational rules and do not.
        if category != "hard_excluded_entities" and refs and not any(
            evidence_by_id.get(ref, {}).get("role") == "taxonomy" for ref in refs
        ):
            errors.append(f"term evidence lacks taxonomy support: {row.get('term')}")
    topic = canonical.get("topic_contract") or {}
    categories = (
        "target_entities",
        "allowed_members",
        "disease_entities",
        "related_entities",
        "hard_excluded_entities",
    )
    for category in categories:
        for value in topic.get(category) or []:
            term = clean_space(value.get("term") if isinstance(value, dict) else value)
            if (category, term.casefold()) not in covered:
                errors.append(f"canonical term lacks term_evidence: {category}:{term}")


def validate_bundled_vocabulary(
    project_root: Path,
    profile_id: str,
    *,
    semantic: bool = False,
) -> tuple[bool, list[str], dict[str, Any]]:
    root = bundle_dir(project_root, profile_id)
    errors: list[str] = []
    if not root.is_dir():
        return False, [f"bundle directory missing: {root}"], {}
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            errors.append(f"missing required file: {name}")
    if errors:
        return False, errors, {}

    manifest = _load(root / "manifest.json")
    canonical = _load(root / "canonical_vocabulary.json")
    if int(manifest.get("schema_version") or 0) != BUNDLE_SCHEMA_VERSION:
        errors.append("manifest schema_version mismatch")
    if int(canonical.get("schema_version") or 0) != BUNDLE_SCHEMA_VERSION:
        errors.append("canonical schema_version mismatch")
    if clean_space(manifest.get("profile_id")) != profile_id or clean_space(canonical.get("profile_id")) != profile_id:
        errors.append("profile_id mismatch")
    if clean_space(canonical.get("bundle_version")) != DEFAULT_BUNDLE_VERSION:
        errors.append("canonical bundle_version mismatch")
    compatibility = _load(root / "profile.json")
    if clean_space(compatibility.get("bundle_version")) != DEFAULT_BUNDLE_VERSION:
        errors.append("compatibility profile bundle_version mismatch")
    if clean_space(compatibility.get("derived_from_semantic_fingerprint")) != clean_space(canonical.get("semantic_fingerprint")):
        errors.append("compatibility profile semantic fingerprint mismatch")
    for name, expected in (manifest.get("files") or {}).items():
        path = root / name
        if not path.is_file():
            errors.append(f"manifest references missing file: {name}")
        elif clean_space(expected) != _hash_file(path):
            errors.append(f"sha256 mismatch: {name}")

    topic = canonical.get("topic_contract") or {}
    identities = _terms(topic.get("target_entities") or [])
    related = _terms(topic.get("related_entities") or [])
    hard = _terms(topic.get("hard_excluded_entities") or topic.get("excluded_entities") or [])
    if len(identities) < 3:
        errors.append("target_entities must contain at least 3 entries")
    identity_keys = {x.casefold() for x in identities}
    related_keys = {x.casefold() for x in related}
    hard_keys = {x.casefold() for x in hard}
    if identity_keys & hard_keys:
        errors.append(f"identity/hard-exclusion contradiction: {sorted(identity_keys & hard_keys)}")
    if identity_keys & related_keys:
        errors.append(f"identity/related contradiction: {sorted(identity_keys & related_keys)}")
    if related_keys & hard_keys:
        errors.append(f"related/hard-exclusion contradiction: {sorted(related_keys & hard_keys)}")
    policy = topic.get("related_entity_policy") or {}
    if related and clean_space(policy.get("supplementary_rule")) == "":
        errors.append("related_entity_policy lacks supplementary_rule")
    _validate_core_mapping(canonical, errors)
    _validate_authority(canonical, errors)

    cases = canonical.get("validation_cases") or _load(root / "validation_cases.json")
    if len(cases.get("positive") or []) < 5 or len(cases.get("negative") or []) < 1:
        errors.append("semantic cases require at least 5 positive and 1 negative case")
    if related and not cases.get("related"):
        errors.append("related entities require executable supplementary cases")

    if semantic and not errors:
        from .query_plan import build_relevance_rules
        from .relevance import relevance_assessment

        profile = _profile_from_canonical(canonical, compatibility)
        profile["post_retrieval_relevance_rules"] = build_relevance_rules(profile)
        for index, case in enumerate(cases.get("positive") or []):
            title = clean_space(case.get("title"))
            text = clean_space(case.get("text"))
            result = relevance_assessment(title, text if case.get("surface") == "abstract_or_brief" else "", profile)
            if result.get("decision") == "reject" or not result.get("primary_eligible"):
                errors.append(f"semantic positive failed #{index+1}: {title or text} :: {result}")
        for index, case in enumerate(cases.get("negative") or []):
            title = clean_space(case.get("title"))
            text = clean_space(case.get("text"))
            result = relevance_assessment(title or text, "", profile)
            if result.get("decision") != "reject" or result.get("supplementary_eligible"):
                errors.append(f"semantic negative failed #{index+1}: {title or text} :: {result}")
        for index, case in enumerate(cases.get("related") or []):
            title = clean_space(case.get("title"))
            text = clean_space(case.get("text"))
            result = relevance_assessment(title or text, "", profile)
            if result.get("route") != "supplementary_related" or not result.get("supplementary_eligible"):
                errors.append(f"semantic related failed #{index+1}: {title or text} :: {result}")
        for index, case in enumerate(cases.get("comparison") or []):
            result = relevance_assessment(clean_space(case.get("title")), clean_space(case.get("text")), profile)
            if result.get("decision") == "reject" or not result.get("primary_eligible"):
                errors.append(f"semantic comparison failed #{index+1}: {case.get('title')} :: {result}")

    return not errors, errors, manifest


def load_bundled_vocabulary(project_root: Path, profile_id: str) -> dict[str, Any]:
    valid, errors, manifest = validate_bundled_vocabulary(project_root, profile_id, semantic=False)
    if not valid:
        raise RuntimeError(f"invalid bundled vocabulary for {profile_id}: {'; '.join(errors)}")
    root = bundle_dir(project_root, profile_id)
    canonical = _load(root / "canonical_vocabulary.json")
    compatibility = _load(root / "profile.json")
    profile = _profile_from_canonical(canonical, compatibility)
    topic = canonical.get("topic_contract") or {}
    return {
        "manifest": manifest,
        "canonical_vocabulary": canonical,
        "profile": profile,
        "review_vocabulary": deepcopy(profile["vocabulary"]),
        "translation_glossary": deepcopy(profile["translation_glossary"]),
        "validation_cases": deepcopy(canonical.get("validation_cases") or {}),
        "authoritative_sources": deepcopy(canonical.get("authoritative_evidence") or []),
        "retrieval_vocabulary": deepcopy(canonical.get("retrieval_contract") or {}),
        "exclusion_vocabulary": {
            "related_entity_terms": deepcopy(topic.get("related_entities") or []),
            "hard_exclusion_terms": deepcopy(topic.get("hard_excluded_entities") or topic.get("excluded_entities") or []),
            "exclusion_terms": deepcopy(topic.get("hard_excluded_entities") or topic.get("excluded_entities") or []),
        },
        "consumer_contract": deepcopy(canonical.get("consumer_contract") or {}),
        "runtime_file_audit": {
            name: {
                "sha256": _hash_file(root / name),
                "consumer": deepcopy((canonical.get("consumer_contract") or {}).get(name) or []),
                "derived_from_semantic_fingerprint": (
                    clean_space((_load(root / name) or {}).get("derived_from_semantic_fingerprint"))
                    if name.endswith(".json") and name != "canonical_vocabulary.json"
                    else clean_space(canonical.get("semantic_fingerprint"))
                ),
            }
            for name in REQUIRED_FILES
            if name != "manifest.json"
        },
    }


def apply_bundled_profile(runtime_profile: dict[str, Any], bundle_profile: dict[str, Any]) -> dict[str, Any]:
    """Keep scheduling/source fields while replacing every semantic contract."""
    merged = deepcopy(runtime_profile)
    for key in (
        "display_name_en",
        "display_name_zh",
        "target_scope",
        "topic_contract",
        "search_strategy",
        "query_policy",
        "source_policy",
        "authoritative_sources",
        "authoritative_evidence",
        "vocabulary",
        "translation_glossary",
        "profile_semantic_fingerprint",
        "vocabulary_bundle_version",
        "vocabulary_source",
        "consumer_contract",
    ):
        if key in bundle_profile:
            merged[key] = deepcopy(bundle_profile[key])
    return merged
