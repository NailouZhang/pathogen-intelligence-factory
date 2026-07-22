from __future__ import annotations

import math
import os
from typing import Any

from .relevance import relevance_assessment
from .utils import clean_space


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _hard_conflict(record: dict[str, Any]) -> bool:
    identity = record.get("content_identity") or {}
    return bool(
        record.get("identifier_conflict")
        or (record.get("metadata_verification") or {}).get("conflict")
        or clean_space(record.get("content_identity_status")) == "identity_conflict"
        or (identity and identity.get("accepted") is False and identity.get("reason") in {"identity_conflict", "wrong_entity"})
        or clean_space(record.get("relevance_llm_code")) == "N"
    )


def _field_assessments(record: dict[str, Any], profile: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    title = clean_space(record.get("title"))
    fields = (
        {"title": (title, ""), "abstract": ("", record.get("abstract")), "full_body": ("", record.get("full_text"))}
        if kind == "paper"
        else {"title": (title, ""), "brief": ("", record.get("excerpt")), "full_body": ("", record.get("content"))}
    )
    return {
        name: relevance_assessment(left, clean_space(right), profile)
        for name, (left, right) in fields.items()
        if clean_space(left) or clean_space(right)
    }


def _field_threshold(field: str, level: int, kind: str) -> int:
    standard = {
        "paper": {"title": 4, "abstract": 5, "full_body": 4},
        "news": {"title": 5, "brief": 6, "full_body": 4},
    }[kind][field]
    return max(1 if level >= 3 else 2, standard - level)


def _accept_field(audit: dict[str, Any], field: str, level: int, kind: str) -> bool:
    score = int(audit.get("score") or 0)
    identity = bool(audit.get("identity_present"))
    title_hits = bool(audit.get("title_identity_hits"))
    body_hits = bool(audit.get("body_identity_hits"))
    contexts = bool(audit.get("context_hits"))
    exclusions = bool(audit.get("excluded_hits"))
    frequency = int(audit.get("identity_frequency") or 0)
    if not identity:
        return False

    threshold = _field_threshold(field, level, kind)
    if exclusions:
        if level == 0:
            return False
        if level == 1 and not (title_hits or frequency >= 2):
            return False
        if level == 2 and not (title_hits or body_hits or (frequency >= 1 and contexts)):
            return False
        if level >= 3 and not (title_hits and frequency >= 1):
            return False

    if level >= 3:
        # Final continuity recovery remains identity anchored. A generic context
        # word can never establish relevance by itself.
        return bool(
            title_hits
            or score >= threshold and (frequency >= 2 or (body_hits and contexts))
        )
    return bool(score >= threshold or (field == "title" and title_hits and score >= threshold - 1))


def _record_continuity_eligible(record: dict[str, Any], kind: str) -> bool:
    if kind == "paper":
        ids = record.get("source_ids") or {}
        return bool(
            (record.get("metadata_verification") or {}).get("verified")
            or record.get("doi")
            or ids.get("pmid")
            or ids.get("pmcid")
            or len(record.get("sources") or []) >= 2
        )
    identity = record.get("content_identity") or {}
    return bool(
        clean_space(record.get("title"))
        and clean_space(record.get("resolved_url") or record.get("url"))
        and (not identity or identity.get("accepted") is not False)
    )


def _relaxed_accept(record: dict[str, Any], profile: dict[str, Any], kind: str, level: int) -> tuple[bool, dict[str, Any]]:
    if _hard_conflict(record):
        return False, {"hard_conflict": True, "level": level, "fields": {}}
    if level >= 3 and not _record_continuity_eligible(record, kind):
        return False, {"hard_conflict": False, "continuity_ineligible": True, "level": level, "fields": {}}
    fields = _field_assessments(record, profile, kind)
    accepted_fields = [name for name, audit in fields.items() if _accept_field(audit, name, level, kind)]
    if not accepted_fields and level >= 2:
        identity_fields = [name for name, audit in fields.items() if audit.get("identity_present")]
        context_fields = [name for name, audit in fields.items() if audit.get("context_hits")]
        if level == 2 and (len(identity_fields) >= 2 or (identity_fields and context_fields)):
            accepted_fields = identity_fields[:2]
        elif level >= 3:
            strong = [
                name for name, audit in fields.items()
                if audit.get("title_identity_hits")
                or (int(audit.get("identity_frequency") or 0) >= 2 and audit.get("context_hits"))
            ]
            accepted_fields = strong[:1]
    return bool(accepted_fields), {
        "hard_conflict": False,
        "level": level,
        "accepted_fields": accepted_fields,
        "fields": fields,
    }


def _guard_settings() -> dict[str, float | int]:
    return {
        # The absolute-count floor remains conservative for large pools.
        "min_candidates": int(os.getenv("PIF_REVIEW_CLIFF_GUARD_MIN_CANDIDATES", "100")),
        # Ratio/historical collapse detection covers medium pools independently
        # from the conservative absolute-count gate.
        "ratio_min_candidates": int(os.getenv("PIF_REVIEW_CLIFF_GUARD_RATIO_MIN_CANDIDATES", "10")),
        "min_accepted": int(os.getenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED", "10")),
        "previous_ratio": float(os.getenv("PIF_REVIEW_CLIFF_GUARD_PREVIOUS_RATIO", "0.20")),
        # Trigger and recovery target are intentionally different. A 30% trigger
        # requests review; it never means that 30% must be force-accepted.
        "trigger_acceptance_ratio": float(os.getenv("PIF_REVIEW_CLIFF_GUARD_TRIGGER_RATIO", "0.30")),
        "minimum_acceptance_ratio": float(os.getenv("PIF_REVIEW_CLIFF_GUARD_MIN_ACCEPTED_RATIO", "0.15")),
    }


def _cliff_detected(candidate_count: int, accepted_count: int, previous_accepted: int | None) -> tuple[bool, list[str]]:
    settings = _guard_settings()
    reasons: list[str] = []
    min_candidates = int(settings["min_candidates"])
    ratio_min_candidates = int(settings["ratio_min_candidates"])
    if candidate_count >= min_candidates and accepted_count < int(settings["min_accepted"]):
        reasons.append("absolute_acceptance_floor")
    if (
        candidate_count >= ratio_min_candidates
        and candidate_count
        and accepted_count / candidate_count < float(settings["trigger_acceptance_ratio"])
    ):
        reasons.append("candidate_acceptance_ratio")
    if (
        candidate_count >= ratio_min_candidates
        and previous_accepted
        and previous_accepted >= int(settings["min_accepted"])
        and accepted_count < previous_accepted * float(settings["previous_ratio"])
    ):
        reasons.append("historical_acceptance_ratio")
    return bool(reasons), reasons


def _target_count(candidate_count: int, previous_accepted: int | None, reasons: list[str]) -> int:
    settings = _guard_settings()
    targets: list[int] = []
    # The fixed ten-record floor belongs only to the large-pool absolute-floor
    # alarm.  A ratio-only alarm in a 10-record pool must not request all ten.
    if "absolute_acceptance_floor" in reasons:
        targets.append(int(settings["min_accepted"]))
    if "candidate_acceptance_ratio" in reasons:
        targets.append(math.ceil(candidate_count * float(settings["minimum_acceptance_ratio"])))
    if "historical_acceptance_ratio" in reasons and previous_accepted:
        targets.append(math.ceil(previous_accepted * float(settings["previous_ratio"])))
    return min(candidate_count, max(targets or [0]))


def _initial_rejection_diagnostic(record: dict[str, Any], profile: dict[str, Any], kind: str) -> dict[str, Any]:
    final = record.get("relevance_final") or {}
    identity = record.get("content_identity") or {}
    codes: list[str] = []
    if record.get("identifier_conflict") or (record.get("metadata_verification") or {}).get("conflict"):
        codes.append("identifier_conflict")
    if clean_space(record.get("content_identity_status")) == "identity_conflict":
        codes.append("content_identity_conflict")
    if identity and identity.get("accepted") is False:
        codes.append(clean_space(identity.get("reason")) or "content_identity_rejected")
    llm_code = clean_space(record.get("relevance_llm_code"))
    if llm_code:
        codes.append(f"llm_code_{llm_code}")
    if final.get("ambiguous_abbreviation_hits"):
        codes.append("ambiguous_abbreviation_without_context")
    if final.get("hard_excluded_hits") or final.get("excluded_hits"):
        codes.append("hard_excluded_entity_hit")
    if final.get("related_hits"):
        codes.append("related_entity_supplementary_route")
    if not final.get("identity_present"):
        codes.append("identity_not_established")
    if final.get("decision") == "reject":
        codes.append("score_below_final_threshold")
    if not codes:
        codes.append(clean_space(record.get("relevance_decision")) or "unclassified_rejection")
    fields = _field_assessments(record, profile, kind)
    return {
        "id": record.get("paper_id") or record.get("news_id"),
        "title": record.get("title"),
        "decision": record.get("relevance_decision"),
        "method": record.get("relevance_review_method"),
        "llm_code": llm_code,
        "llm_reason": clean_space(record.get("relevance_llm_reason")),
        "reason_codes": list(dict.fromkeys(codes)),
        "final_assessment": final,
        "field_assessments": fields,
    }


def apply_relevance_cliff_guard(
    candidates: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    kind: str,
    previous_accepted: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Protect target-primary continuity without deleting related supplements.

    Related-only records are carried through independently and never count
    toward either the target-primary acceptance ratio or its recovery target.
    They are also never promoted by recovery.
    """
    enabled = _env_bool("PIF_REVIEW_CLIFF_GUARD_ENABLED", True)

    def is_related(row: dict[str, Any]) -> bool:
        audit = row.get("relevance_final") or row.get("relevance_candidate") or {}
        return bool(
            row.get("relevance_route") == "supplementary_related"
            or row.get("display_eligibility") == "supplementary_only"
            or audit.get("route") == "supplementary_related"
        )

    related_candidates = [row for row in candidates if is_related(row)]
    primary_candidates = [row for row in candidates if not is_related(row)]
    related_accepted = [row for row in accepted if is_related(row)]
    primary_accepted = [row for row in accepted if not is_related(row)]

    detected, reasons = _cliff_detected(len(primary_candidates), len(primary_accepted), previous_accepted)
    target = _target_count(len(primary_candidates), previous_accepted, reasons) if detected else len(primary_accepted)
    audit: dict[str, Any] = {
        "policy_version": "v17.4-primary-continuity-related-independent-1",
        "kind": kind,
        "enabled": enabled,
        "candidate_count": len(candidates),
        "primary_candidate_count": len(primary_candidates),
        "related_supplementary_candidate_count": len(related_candidates),
        "initial_accepted": len(accepted),
        "initial_primary_accepted": len(primary_accepted),
        "initial_related_supplementary_accepted": len(related_accepted),
        "previous_accepted": previous_accepted,
        "triggered": bool(enabled and detected),
        "trigger_reasons": reasons,
        "target_primary_accepted": target,
        # Backward-compatible alias; the value now explicitly refers to target-primary records only.
        "target_accepted": target,
        "guard_settings": _guard_settings(),
        "initial_rejection_diagnostics": [
            _initial_rejection_diagnostic(record, profile, kind)
            for record in primary_candidates
            if record not in primary_accepted
        ],
        "levels": [],
        "field_thresholds": {
            "paper": {"title": [4, 3, 2, 1], "abstract": [5, 4, 3, 2], "full_body": [4, 3, 2, 1]},
            "news": {"title": [5, 4, 3, 2], "brief": [6, 5, 4, 3], "full_body": [4, 3, 2, 1]},
        }[kind],
        "hard_conflicts_never_relaxed": True,
        "related_entities_never_promoted_by_recovery": True,
        "related_entities_preserved_independently": True,
        "core_search_terms_fallback": False,
        "target_cap_enforced": True,
    }
    reason_counts: dict[str, int] = {}
    for row in audit["initial_rejection_diagnostics"]:
        for code in row.get("reason_codes") or []:
            reason_counts[code] = reason_counts.get(code, 0) + 1
    audit["initial_rejection_reason_counts"] = dict(sorted(reason_counts.items()))

    recovered_primary = list(primary_accepted)
    if enabled and detected:
        output_identity_ids = {id(row) for row in recovered_primary}
        for level in (1, 2, 3):
            recovered: list[dict[str, Any]] = []
            rejected_audits: list[dict[str, Any]] = []
            for record in primary_candidates:
                if len(recovered_primary) + len(recovered) >= target:
                    break
                if id(record) in output_identity_ids:
                    continue
                ok, record_audit = _relaxed_accept(record, profile, kind, level)
                if ok:
                    record["relevance_decision"] = f"accept_cliff_guard_level_{level}"
                    record["relevance_review_method"] = "field_aware_deterministic_output_continuity"
                    record["relevance_cliff_recovery"] = record_audit
                    record["relevance_route"] = "primary_candidate"
                    record["display_eligibility"] = "primary_candidate"
                    record["primary_eligible"] = True
                    recovered.append(record)
                    output_identity_ids.add(id(record))
                elif len(rejected_audits) < 25:
                    rejected_audits.append({
                        "id": record.get("paper_id") or record.get("news_id"),
                        "title": record.get("title"),
                        "audit": record_audit,
                    })
            recovered_primary.extend(recovered)
            audit["levels"].append({
                "level": level,
                "strategy": {
                    1: "lower_soft_field_thresholds",
                    2: "relax_soft_target_evidence_with_multi_surface_identity",
                    3: "safe_explicit_target_identity_output_continuity",
                }[level],
                "recovered_primary": len(recovered),
                "primary_accepted_after_level": len(recovered_primary),
                "sample_rejections": rejected_audits,
            })
            if len(recovered_primary) >= target:
                break

    selected_ids = {id(row) for row in recovered_primary + related_accepted}
    # Related candidates retained by final_filter are already in related_accepted.
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for row in candidates:
        if id(row) not in selected_ids:
            continue
        key = clean_space(row.get("paper_id") or row.get("news_id") or row.get("doi") or row.get("url") or row.get("title"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        ordered.append(row)

    final_primary = sum(not is_related(row) for row in ordered)
    final_related = sum(is_related(row) for row in ordered)
    audit["final_accepted"] = len(ordered)
    audit["final_primary_accepted"] = final_primary
    audit["final_related_supplementary_accepted"] = final_related
    audit["recovered"] = max(0, final_primary - len(primary_accepted))
    audit["recovered_primary"] = audit["recovered"]
    audit["resolved"] = not (enabled and detected) or final_primary >= target
    if audit["resolved"] and audit["recovered"]:
        audit["continuity_status"] = "recovered_output"
        audit["continuity_detail"] = "target_primary_recovered_related_supplementary_independent"
    elif final_primary:
        audit["continuity_status"] = "standard_or_qualified_primary_output"
    elif final_related:
        audit["continuity_status"] = "related_supplementary_only_output"
    else:
        audit["continuity_status"] = "empty_valid_issue"
    audit["publication_must_continue"] = True
    audit["fabricated_acceptance_forbidden"] = True
    return ordered, audit

def baseline_value(state: dict[str, Any], profile_id: str, kind: str) -> int | None:
    value = (((state.get("review_cliff_baselines") or {}).get(profile_id) or {}).get(kind))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def update_baseline(state: dict[str, Any], profile_id: str, kind: str, accepted_count: int) -> None:
    root = state.setdefault("review_cliff_baselines", {})
    profile = root.setdefault(profile_id, {})
    previous = baseline_value(state, profile_id, kind)
    profile[kind] = max(int(accepted_count), int(previous or 0))
