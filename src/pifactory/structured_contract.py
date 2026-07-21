from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

from .language_contract import is_verified_english, sanitize_english_analysis
from .postprocess import complete_text, contains_cross_field_overlap, deduplicate_structured_analysis
from .utils import clean_space


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "research_question_and_background": ("background", "research_question", "question_and_background", "objective_and_background"),
    "study_design_and_population": ("study_design", "design_and_population", "design_population", "population_and_design"),
    "methods": ("method", "methodology", "core_methods", "materials_and_methods"),
    "main_results": ("results", "findings", "key_results", "main_findings"),
    "interpretation_and_novelty": ("interpretation", "novelty", "interpretation_novelty", "discussion"),
    "scientific_and_public_health_significance": ("significance", "implications", "public_health_significance", "scientific_significance"),
    "limitations_and_evidence_strength": ("limitations", "evidence_strength", "limitations_and_strength"),
    "scope_and_question": ("scope", "review_scope", "question", "scope_question"),
    "evidence_base_and_review_method": ("evidence_base", "review_method", "methods", "evidence_and_method"),
    "consensus_and_key_conclusions": ("consensus", "key_conclusions", "conclusions", "consensus_conclusions"),
    "controversies_and_evidence_gaps": ("controversies", "evidence_gaps", "gaps", "controversies_and_gaps"),
    "research_and_practice_implications": ("implications", "research_implications", "practice_implications"),
    "time": ("date", "timing", "event_time", "publication_and_event_time"),
    "location_and_population": ("location", "population", "location_population", "place_and_population"),
    "event": ("what_happened", "event_description", "incident"),
    "scale_impact_and_risk": ("scale", "impact", "risk", "scale_and_impact"),
    "response_status_and_uncertainty": ("response", "status", "uncertainty", "response_and_uncertainty"),
}

SUMMARY_ALIASES = {
    "summary_en": ("summary", "english_summary", "integrated_summary", "brief"),
    "brief_en": ("brief", "summary", "news_brief", "english_brief"),
}

CONFIDENCE_ALIASES = {
    "high": "high",
    "strong": "high",
    "very high": "high",
    "moderate": "moderate",
    "medium": "moderate",
    "mid": "moderate",
    "low": "low",
    "weak": "low",
}

SOURCE_ASSESSMENT_ALIASES = {
    "official": "official",
    "government": "official",
    "public health authority": "official",
    "reputable_media": "reputable_media",
    "reputable media": "reputable_media",
    "major media": "reputable_media",
    "secondary_media": "secondary_media",
    "secondary media": "secondary_media",
    "aggregator": "aggregator",
    "rss": "aggregator",
    "unclear": "unclear",
    "unknown": "unclear",
}


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", clean_space(value).casefold()).strip("_")


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return clean_space(value)
    if isinstance(value, (int, float, bool)):
        return clean_space(value)
    if isinstance(value, list):
        return clean_space(" ".join(_as_text(item) for item in value if _as_text(item)))
    if isinstance(value, dict):
        for candidate in ("text", "value", "content", "answer"):
            if candidate in value:
                return _as_text(value[candidate])
    return ""


def _find_value(mapping: dict[str, Any], canonical: str, aliases: tuple[str, ...]) -> tuple[Any, str]:
    normalized = {_key(key): key for key in mapping}
    for name in (canonical, *aliases):
        actual = normalized.get(_key(name))
        if actual is not None:
            return mapping.get(actual), actual
    return None, ""


def _parse_refs(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped.replace("'", '"'))
                raw = parsed if isinstance(parsed, list) else [stripped]
            except json.JSONDecodeError:
                raw = re.split(r"[,;|\s]+", stripped.strip("[](){}"))
        else:
            raw = re.split(r"[,;|\s]+", stripped.strip("[](){}"))
    elif value is None:
        raw = []
    else:
        raw = [value]
    return list(dict.fromkeys(clean_space(item) for item in raw if clean_space(item)))


def _canonical_refs(refs: list[str], valid_ids: set[str]) -> tuple[list[str], list[str]]:
    lookup = {value.casefold(): value for value in valid_ids}
    output: list[str] = []
    changed: list[str] = []
    for ref in refs:
        canonical = lookup.get(ref.casefold(), ref)
        if canonical != ref:
            changed.append(f"{ref}->{canonical}")
        if canonical not in output:
            output.append(canonical)
    return output, changed


def normalize_structured_candidate(
    data: Any,
    *,
    payload: dict[str, Any],
    kind: str,
    required_fields: list[str],
    source_language: str,
    require_brief: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize provider-specific JSON differences before strict validation.

    This function repairs representation only. It never invents evidence IDs,
    numerical facts, locations, methods or conclusions.
    """
    audit: dict[str, Any] = {
        "policy_version": "v17.2-prevalidation-normalization-1",
        "repairs": [],
        "warnings": [],
    }
    candidate = copy.deepcopy(data) if isinstance(data, dict) else {}
    parser_audit = candidate.pop("_pif_parser_audit", {}) if isinstance(candidate, dict) else {}
    if parser_audit:
        audit["parser_audit"] = parser_audit

    analysis_input = candidate.get("analysis")
    if not isinstance(analysis_input, dict):
        analysis_input = candidate if isinstance(candidate, dict) else {}
        audit["repairs"].append("analysis_container_rebuilt")
    evidence_input = candidate.get("evidence_ids")
    if not isinstance(evidence_input, dict):
        evidence_input = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
        audit["repairs"].append("evidence_ids_container_rebuilt")

    normalized_analysis: dict[str, str] = {}
    normalized_refs: dict[str, list[str]] = {}
    valid_ids = {clean_space(row.get("id")) for row in payload.get("evidence") or [] if clean_space(row.get("id"))}

    for field in required_fields:
        value, used_key = _find_value(analysis_input, field, FIELD_ALIASES.get(field, ()))
        if not used_key and analysis_input is not candidate:
            value, used_key = _find_value(candidate, field, FIELD_ALIASES.get(field, ()))
        normalized_analysis[field] = _as_text(value)
        if used_key and _key(used_key) != _key(field):
            audit["repairs"].append(f"field_alias:{used_key}->{field}")

        refs, refs_key = _find_value(evidence_input, field, FIELD_ALIASES.get(field, ()))
        parsed_refs = _parse_refs(refs)
        canonical_refs, changes = _canonical_refs(parsed_refs, valid_ids)
        normalized_refs[field] = canonical_refs
        if refs_key and _key(refs_key) != _key(field):
            audit["repairs"].append(f"evidence_alias:{refs_key}->{field}")
        audit["repairs"].extend(f"evidence_case:{change}" for change in changes)

    candidate["analysis"] = normalized_analysis
    candidate["evidence_ids"] = normalized_refs

    summary_key = "brief_en" if kind == "news" else "summary_en"
    summary, used_summary_key = _find_value(candidate, summary_key, SUMMARY_ALIASES[summary_key])
    candidate[summary_key] = _as_text(summary)
    if used_summary_key and _key(used_summary_key) != _key(summary_key):
        audit["repairs"].append(f"summary_alias:{used_summary_key}->{summary_key}")

    confidence = CONFIDENCE_ALIASES.get(clean_space(candidate.get("confidence")).casefold(), clean_space(candidate.get("confidence")).casefold())
    if confidence != clean_space(candidate.get("confidence")).casefold():
        audit["repairs"].append("confidence_normalized")
    candidate["confidence"] = confidence

    if kind == "news":
        source_value = clean_space(candidate.get("source_assessment")).casefold().replace("-", "_")
        candidate["source_assessment"] = SOURCE_ASSESSMENT_ALIASES.get(source_value, SOURCE_ASSESSMENT_ALIASES.get(source_value.replace("_", " "), source_value))
        if not require_brief:
            if clean_space(candidate.get("brief_en")):
                audit["repairs"].append("short_source_brief_cleared")
            candidate["brief_en"] = ""

    candidate = deduplicate_structured_analysis(candidate, payload, kind)
    postprocess_audit = candidate.get("postprocess_audit") or {}
    if postprocess_audit:
        audit["postprocess"] = postprocess_audit
    candidate = sanitize_english_analysis(candidate, kind="news" if kind == "news" else "paper", source_language=source_language)
    language_audit = candidate.get("language_contract") or {}
    if language_audit:
        audit["language_contract"] = language_audit

    # A missing integrated summary can be deterministically assembled from the
    # already evidence-bound fields. This does not create new facts.
    summary_value = clean_space(candidate.get(summary_key))
    if kind != "news" and not summary_value:
        candidate[summary_key], _ = complete_text(" ".join(candidate["analysis"].values()), max_chars=1900)
        audit["repairs"].append("summary_built_from_validated_fields")
    elif kind == "news" and require_brief and not summary_value:
        candidate[summary_key], _ = complete_text(" ".join(candidate["analysis"].values()), max_chars=1500)
        audit["repairs"].append("brief_built_from_evidence_bound_fields")
    if kind == "news":
        candidate["summary_en"] = clean_space(candidate.get("brief_en"))

    audit["repairs"] = list(dict.fromkeys(audit["repairs"]))
    audit["repair_count"] = len(audit["repairs"])
    return candidate, audit


def validate_structured_candidate(
    data: Any,
    *,
    required_fields: list[str],
    valid_ids: set[str],
    kind: str,
    role_map: dict[str, str] | None = None,
    allowed_roles: dict[str, set[str]] | None = None,
    require_brief: bool = False,
) -> tuple[bool, dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        issues.append({"category": "json_not_object", "scope": "global"})
        return False, {"issues": issues, "warnings": warnings}
    analysis = data.get("analysis")
    evidence_ids = data.get("evidence_ids")
    if not isinstance(analysis, dict):
        issues.append({"category": "analysis_missing", "scope": "global"})
        analysis = {}
    if not isinstance(evidence_ids, dict):
        issues.append({"category": "evidence_ids_missing", "scope": "global"})
        evidence_ids = {}

    for field in required_fields:
        raw = analysis.get(field)
        if not isinstance(raw, str):
            issues.append({"category": "field_type_error", "field": field, "expected": "string", "actual": type(raw).__name__})
            continue
        value = clean_space(raw)
        minimum = 8 if kind == "news" else 12
        if not value:
            issues.append({"category": "required_field_missing", "field": field})
        elif len(value) < minimum:
            issues.append({"category": "field_too_short", "field": field, "length": len(value), "minimum": minimum})
        if value and not is_verified_english(value):
            issues.append({"category": "language_region_error", "field": field})

        refs = evidence_ids.get(field)
        if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
            issues.append({"category": "evidence_ids_missing", "field": field})
            continue
        unknown = [clean_space(ref) for ref in refs if clean_space(ref) not in valid_ids]
        if unknown:
            issues.append({"category": "evidence_id_unknown", "field": field, "unknown": unknown})
        if role_map and allowed_roles:
            cited_roles = {role_map.get(clean_space(ref), "general") for ref in refs if clean_space(ref) in valid_ids}
            allowed = set(allowed_roles.get(field, {"general"}))
            if cited_roles and not cited_roles.intersection(allowed):
                # Rhetorical role assignment is heuristic. Preserve it as an
                # audit warning rather than invalidating otherwise real evidence.
                warnings.append({
                    "category": "evidence_role_mismatch",
                    "field": field,
                    "cited_roles": sorted(cited_roles),
                    "allowed_roles": sorted(allowed),
                })

    overlap, reason = contains_cross_field_overlap(analysis, required_fields, threshold=0.92 if kind == "news" else 0.88)
    if overlap:
        issues.append({"category": "cross_field_duplicate", "scope": "analysis", "detail": reason})

    summary_key = "brief_en" if kind == "news" else "summary_en"
    summary = data.get(summary_key)
    if not isinstance(summary, str):
        issues.append({"category": "summary_type_error", "field": summary_key})
    else:
        summary_text = clean_space(summary)
        words = len(summary_text.split())
        if kind == "news" and not require_brief:
            if summary_text:
                issues.append({"category": "short_source_brief_must_be_empty", "field": summary_key})
        elif not summary_text:
            issues.append({"category": "summary_missing", "field": summary_key})
        else:
            lower, upper = ((80, 260) if kind == "news" else (60, 280))
            if words < lower or words > upper:
                warnings.append({
                    "category": "summary_length_outside_preferred_range",
                    "field": summary_key,
                    "words": words,
                    "preferred": [lower, upper],
                })
            if not is_verified_english(summary_text):
                issues.append({"category": "language_region_error", "field": summary_key})

    if clean_space(data.get("confidence")) not in {"high", "moderate", "low"}:
        issues.append({"category": "invalid_confidence", "field": "confidence"})
    if kind == "news" and clean_space(data.get("source_assessment")) not in {
        "official", "reputable_media", "secondary_media", "aggregator", "unclear",
    }:
        issues.append({"category": "invalid_source_assessment", "field": "source_assessment"})

    counts = Counter(issue["category"] for issue in issues)
    return not issues, {
        "policy_version": "v17.2-detailed-structured-validation-1",
        "issues": issues,
        "warnings": warnings,
        "issue_counts": dict(sorted(counts.items())),
    }


def repair_targets(validation: dict[str, Any], required_fields: list[str], kind: str) -> list[str]:
    targets: list[str] = []
    for issue in validation.get("issues") or []:
        field = clean_space(issue.get("field"))
        if field and field not in targets:
            targets.append(field)
        if issue.get("category") == "cross_field_duplicate":
            for candidate in required_fields:
                if candidate not in targets:
                    targets.append(candidate)
    allowed = set(required_fields) | {"summary_en", "brief_en", "confidence", "source_assessment"}
    return [field for field in targets if field in allowed]


def merge_repair(base: dict[str, Any], repair: dict[str, Any], targets: list[str]) -> dict[str, Any]:
    output = copy.deepcopy(base)
    repair_analysis = repair.get("analysis") if isinstance(repair.get("analysis"), dict) else {}
    repair_refs = repair.get("evidence_ids") if isinstance(repair.get("evidence_ids"), dict) else {}
    output.setdefault("analysis", {})
    output.setdefault("evidence_ids", {})
    for field in targets:
        if field in output["analysis"] and field in repair_analysis:
            output["analysis"][field] = repair_analysis[field]
            if field in repair_refs:
                output["evidence_ids"][field] = repair_refs[field]
        elif field in repair:
            output[field] = repair[field]
    return output


def candidate_error_count(candidate: dict[str, Any]) -> int:
    validation = candidate.get("validation") or {}
    return len(validation.get("issues") or []) if isinstance(validation, dict) else 999
