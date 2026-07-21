from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .utils import clean_space, sha256_text

BUNDLE_SCHEMA_VERSION = 3
DEFAULT_BUNDLE_VERSION = "2026.07-v17.1"
REQUIRED_FILES = (
    "manifest.json",
    "profile.json",
    "retrieval_vocabulary.json",
    "review_vocabulary.json",
    "exclusion_vocabulary.json",
    "translation_glossary.json",
    "authoritative_sources.json",
    "validation_cases.json",
)
REQUIRED_REVIEW_KEYS = (
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


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash_file(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8"))


def bundle_dir(project_root: Path, profile_id: str) -> Path:
    return project_root / "config" / "vocabularies" / profile_id


def validate_bundled_vocabulary(project_root: Path, profile_id: str) -> tuple[bool, list[str], dict[str, Any]]:
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
    if int(manifest.get("schema_version") or 0) != BUNDLE_SCHEMA_VERSION:
        errors.append("manifest schema_version mismatch")
    if clean_space(manifest.get("profile_id")) != profile_id:
        errors.append("manifest profile_id mismatch")
    if clean_space(manifest.get("validation_status")) != "passed":
        errors.append("manifest validation_status is not passed")

    for name, expected in (manifest.get("files") or {}).items():
        path = root / name
        if not path.is_file():
            errors.append(f"manifest references missing file: {name}")
        elif clean_space(expected) != _hash_file(path):
            errors.append(f"sha256 mismatch: {name}")

    review_record = _load(root / "review_vocabulary.json")
    review = review_record.get("review_vocabulary") or {}
    for key in REQUIRED_REVIEW_KEYS:
        if key not in review:
            errors.append(f"review vocabulary missing key: {key}")
    anchors = review.get("identity_anchor_terms") or []
    contexts = review.get("context_terms") or []
    exclusions = review.get("exclusion_terms") or []
    if len(anchors) < 3:
        errors.append("identity_anchor_terms must contain at least 3 entries")
    if len(contexts) < 20:
        errors.append("context_terms must contain at least 20 entries")
    if len(exclusions) < 2:
        errors.append("exclusion_terms must contain at least 2 entries")

    retrieval = _load(root / "retrieval_vocabulary.json")
    retrieval_terms = {
        clean_space(row.get("term") if isinstance(row, dict) else row).casefold()
        for rows in retrieval.values() if isinstance(rows, list)
        for row in rows
        if clean_space(row.get("term") if isinstance(row, dict) else row)
    }
    review_terms = {
        clean_space(row.get("term") if isinstance(row, dict) else row).casefold()
        for key, rows in review.items() if key != "document_type_terms" and isinstance(rows, list)
        for row in rows
        if clean_space(row.get("term") if isinstance(row, dict) else row)
    }
    if review_terms and retrieval_terms and review_terms <= retrieval_terms and len(review_terms) <= 8:
        errors.append("review vocabulary degenerates to retrieval terms")

    validation_cases = _load(root / "validation_cases.json")
    positives = validation_cases.get("positive") or validation_cases.get("positive_cases") or []
    negatives = validation_cases.get("negative") or validation_cases.get("negative_cases") or []
    if len(positives) < 2 or len(negatives) < 2:
        errors.append("validation cases require at least 2 positive and 2 negative cases")

    return not errors, errors, manifest


def load_bundled_vocabulary(project_root: Path, profile_id: str) -> dict[str, Any]:
    valid, errors, manifest = validate_bundled_vocabulary(project_root, profile_id)
    if not valid:
        raise RuntimeError(f"invalid bundled vocabulary for {profile_id}: {'; '.join(errors)}")
    root = bundle_dir(project_root, profile_id)
    profile = _load(root / "profile.json")
    review_record = _load(root / "review_vocabulary.json")
    glossary_record = _load(root / "translation_glossary.json")
    profile["vocabulary"] = deepcopy(review_record.get("review_vocabulary") or {})
    profile["translation_glossary"] = deepcopy(glossary_record.get("translation_glossary") or [])
    profile["profile_semantic_fingerprint"] = clean_space(manifest.get("profile_semantic_fingerprint"))
    profile["vocabulary_bundle_version"] = clean_space(manifest.get("bundle_version")) or DEFAULT_BUNDLE_VERSION
    profile["vocabulary_source"] = "bundled"
    return {
        "manifest": manifest,
        "profile": profile,
        "review_vocabulary": deepcopy(profile["vocabulary"]),
        "translation_glossary": deepcopy(profile["translation_glossary"]),
        "validation_cases": _load(root / "validation_cases.json"),
        "authoritative_sources": (
            _load(root / "authoritative_sources.json").get("sources")
            or _load(root / "authoritative_sources.json").get("authoritative_sources")
            or []
        ),
        "retrieval_vocabulary": _load(root / "retrieval_vocabulary.json"),
        "exclusion_vocabulary": _load(root / "exclusion_vocabulary.json"),
    }


def apply_bundled_profile(runtime_profile: dict[str, Any], bundle_profile: dict[str, Any]) -> dict[str, Any]:
    """Keep runtime scheduling/source fields while replacing semantic contracts."""
    merged = deepcopy(runtime_profile)
    for key in (
        "display_name_en", "display_name_zh", "target_scope", "search_strategy",
        "query_policy", "source_policy", "authoritative_sources", "vocabulary",
        "translation_glossary", "profile_semantic_fingerprint", "vocabulary_bundle_version",
        "vocabulary_source",
    ):
        if key in bundle_profile:
            merged[key] = deepcopy(bundle_profile[key])
    return merged
