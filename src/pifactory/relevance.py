from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from .llm import LLMError, LLMRouter
from .entity_resolution import resolve_entities
from .utils import clean_space, sha256_text, unique_strings


REVIEW_POLICY_VERSION = "v17.4-related-entity-supplementary-contract-1"


def _contains(text: str, term: str) -> bool:
    value = clean_space(term).casefold()
    if not value:
        return False
    hay = clean_space(text).casefold()
    if re.fullmatch(r"[a-z0-9-]{1,8}", value):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", hay, flags=re.I))
    return value in hay


def _count_occurrences(text: str, term: str) -> int:
    value = clean_space(term).casefold()
    hay = clean_space(text).casefold()
    if not value or not hay:
        return 0
    if re.fullmatch(r"[a-z0-9-]{1,8}", value):
        return len(re.findall(rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])", hay, flags=re.I))
    return hay.count(value)


def _record_body(record: dict[str, Any], kind: str) -> str:
    if kind == "paper":
        return clean_space(record.get("abstract") or record.get("full_text") or "")
    return clean_space(record.get("content") or record.get("excerpt") or "")


def relevance_assessment(title: str, body: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Resolve target, related and hard-excluded entities before topic scoring.

    v17.4 treats longest-entity matching as disambiguation rather than deletion.
    A biologically related animal virus or near-neighbour is retained for the
    supplementary catalog when the target identity is absent.  Only explicit
    lexical/unrelated hard exclusions remain terminal rejects.
    """
    rules = profile.get("post_retrieval_relevance_rules") or {}
    title = clean_space(title)
    body = clean_space(body)
    combined = clean_space(title + " " + body)
    contract = rules.get("entity_contract") or {
        "target_entities": rules.get("identity_anchor_patterns") or [],
        "allowed_members": rules.get("member_patterns") or [],
        "disease_entities": rules.get("disease_patterns") or [],
        "related_entities": rules.get("related_entity_patterns") or [],
        "hard_excluded_entities": rules.get("hard_excluded_entity_patterns") or rules.get("excluded_entity_patterns") or [],
    }
    title_entities = resolve_entities(title, contract)
    body_entities = resolve_entities(body, contract)
    title_anchor = title_entities.get("target_hits") or []
    title_member = title_entities.get("member_hits") or []
    title_disease = title_entities.get("disease_hits") or []
    body_anchor = body_entities.get("target_hits") or []
    body_member = body_entities.get("member_hits") or []
    body_disease = body_entities.get("disease_hits") or []
    related_title = title_entities.get("related_hits") or []
    related_body = body_entities.get("related_hits") or []
    related_details = (title_entities.get("related_hit_details") or []) + (body_entities.get("related_hit_details") or [])
    hard_title = title_entities.get("hard_excluded_hits") or []
    hard_body = body_entities.get("hard_excluded_hits") or []
    contexts = unique_strings(rules.get("context_patterns") or [])
    context_hits = [x for x in contexts if _contains(combined, x)]

    score = 0
    reasons: list[str] = []
    if title_anchor:
        score += 6
        reasons.append(f"title_identity:{title_anchor[:3]}")
    if title_member:
        score += 5
        reasons.append(f"title_member:{title_member[:3]}")
    if title_disease:
        score += 4
        reasons.append(f"title_disease:{title_disease[:3]}")
    if body_anchor and not title_anchor:
        score += 3
        reasons.append(f"body_identity:{body_anchor[:3]}")
    if body_member and not title_member:
        score += 2
        reasons.append(f"body_member:{body_member[:3]}")
    if body_disease and not title_disease:
        score += 2
        reasons.append(f"body_disease:{body_disease[:3]}")
    if related_title:
        score += 2
        reasons.append(f"title_related_entity:{related_title[:3]}")
    elif related_body:
        score += 1
        reasons.append(f"body_related_entity:{related_body[:3]}")
    if context_hits:
        score += 1
        reasons.append(f"context:{context_hits[:3]}")

    any_direct_identity = bool(
        title_anchor or title_member or title_disease or body_anchor or body_member or body_disease
    )
    qualified_ok: list[str] = []
    qualified_bad: list[str] = []
    qualified_contexts: dict[str, list[str]] = {}
    for rule in rules.get("qualified_abbreviation_rules") or []:
        term = clean_space(rule.get("term"))
        if not term or not _contains(combined, term):
            continue
        required = unique_strings(rule.get("required_context_terms") or [])
        matched = [x for x in required if _contains(combined, x)]
        if matched:
            qualified_ok.append(term)
            qualified_contexts[term] = matched
        else:
            qualified_bad.append(term)
    if qualified_ok and not any_direct_identity:
        score += 3
        reasons.append(f"qualified_identity:{qualified_ok[:3]}")
    if qualified_bad and not any_direct_identity and not (related_title or related_body):
        score -= 4
        reasons.append(f"ambiguous_abbreviation:{qualified_bad[:3]}")

    has_target_identity = bool(any_direct_identity or qualified_ok)
    has_related_identity = bool(related_title or related_body)
    if context_hits and not (has_target_identity or has_related_identity):
        score -= 4
        reasons.append("context_only")

    hard_excluded_title = bool(hard_title and not has_target_identity)
    hard_excluded_body = bool(hard_body and not has_target_identity and not has_related_identity)
    mixed_target_related = bool(has_target_identity and has_related_identity)
    if hard_excluded_title:
        score -= 10
        reasons.append(f"hard_excluded_title:{hard_title[:3]}")
    elif hard_excluded_body:
        score -= 7
        reasons.append(f"hard_excluded_body:{hard_body[:3]}")
    if mixed_target_related:
        reasons.append(f"material_target_related_comparison:{unique_strings(related_title + related_body)[:3]}")

    identity_frequency = sum(
        _count_occurrences(combined, term)
        for term in unique_strings(
            title_anchor + title_member + title_disease + body_anchor + body_member + body_disease + qualified_ok
        )
    )
    if has_target_identity and identity_frequency >= 2:
        score += 1
        reasons.append(f"identity_frequency:{identity_frequency}")

    accept = int(rules.get("minimum_relevance_score", 6))
    review = int(rules.get("review_score_min", 3))
    high_confidence_title = bool(title_anchor or title_member or title_disease)

    if hard_excluded_title or hard_excluded_body:
        decision = "reject"
        route = "reject"
    elif has_target_identity:
        route = "primary_candidate"
        if mixed_target_related:
            decision = "review"
        elif score >= accept or high_confidence_title:
            decision = "accept"
        elif score >= review:
            decision = "review"
        else:
            decision = "reject"
            route = "reject"
    elif has_related_identity:
        # Related-only records are never promoted into the primary target report.
        # Exact title identity is sufficient for metadata-safe supplementary
        # retention; body-only identity remains reviewable before routing.
        decision = "review"
        route = "supplementary_related"
    else:
        decision = "reject"
        route = "reject"

    related_terms = unique_strings(related_title + related_body)
    hard_terms = unique_strings(hard_title + hard_body)
    return {
        "score": score,
        "decision": decision,
        "route": route,
        "reasons": reasons,
        "identity_present": has_target_identity,
        "target_identity_present": has_target_identity,
        "related_identity_present": has_related_identity,
        "identity_hits": unique_strings(title_anchor + title_member + title_disease + body_anchor + body_member + body_disease + qualified_ok),
        "title_identity_hits": unique_strings(title_anchor + title_member + title_disease),
        "body_identity_hits": unique_strings(body_anchor + body_member + body_disease),
        "qualified_identity_hits": qualified_ok,
        "qualified_context_hits": qualified_contexts,
        "ambiguous_abbreviation_hits": qualified_bad,
        "context_hits": context_hits,
        "related_hits": related_terms,
        "related_hit_details": related_details,
        "hard_excluded_hits": hard_terms,
        # Compatibility alias now contains only terminal hard exclusions.
        "excluded_hits": hard_terms,
        "identity_frequency": identity_frequency,
        "entity_resolution": {"title": title_entities, "body": body_entities},
        "hard_entity_conflict": bool(hard_excluded_title or hard_excluded_body),
        "mixed_entity_comparison": mixed_target_related,
        "primary_eligible": bool(route == "primary_candidate"),
        "supplementary_eligible": bool(route == "supplementary_related" or (route == "primary_candidate" and has_related_identity)),
        "supplementary_reason": "biologically_related_non_target_entity" if route == "supplementary_related" else "",
        "needs_llm_review": bool(
            mixed_target_related
            or route == "supplementary_related"
            or qualified_bad
            or (not has_target_identity and rules.get("core_concept_patterns"))
        ),
        "policy_version": REVIEW_POLICY_VERSION,
    }

def relevance_score(title: str, body: str, profile: dict[str, Any]) -> float:
    assessment = relevance_assessment(title, body, profile)
    return max(0.0, min(1.0, assessment["score"] / 10.0))


def assess_records(
    records: list[dict[str, Any]],
    profile: dict[str, Any],
    body_keys: tuple[str, ...],
    *,
    stage: str,
    keep: tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        body = " ".join(clean_space(record.get(k)) for k in body_keys if record.get(k))
        audit = relevance_assessment(record.get("title", ""), body, profile)
        record[f"relevance_{stage}"] = audit
        record["relevance_raw_score"] = audit["score"]
        record["relevance_score"] = max(0.0, min(1.0, audit["score"] / 10.0))
        record["relevance_decision"] = audit["decision"]
        concept_count = len(record.get("retrieval_concepts") or [])
        provider_count = len(set(record.get("sources") or [record.get("source")]) - {None, ""})
        record["retrieval_concept_count"] = concept_count
        record["provider_convergence_count"] = provider_count
        record["relevance_reason"] = audit["reasons"]
        record["relevance_route"] = audit.get("route", "reject")
        record["primary_eligible"] = bool(audit.get("primary_eligible"))
        record["supplementary_eligible"] = bool(audit.get("supplementary_eligible"))
        if audit.get("supplementary_reason"):
            record["supplementary_reason"] = audit.get("supplementary_reason")
        if keep is None or audit["decision"] in keep:
            output.append(record)
    return output


def _candidate_filter(records: list[dict[str, Any]], profile: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    body_keys = ("abstract", "title") if kind == "paper" else ("excerpt", "title")
    assessed = assess_records(records, profile, body_keys, stage="candidate", keep=None)
    output: list[dict[str, Any]] = []
    for record in assessed:
        audit = record.get("relevance_candidate") or {}
        if audit.get("decision") in {"accept", "review"}:
            output.append(record)
            continue
        # A record returned by an explicit identity query can have sparse
        # metadata or a generic title.  Keep it until abstract/body enrichment,
        # unless an excluded entity clearly dominates the title.
        anchored = bool(record.get("retrieval_queries"))
        excluded = bool(audit.get("excluded_hits"))
        if anchored and record.get("title") and not excluded:
            record["relevance_decision"] = "metadata_pending"
            record["candidate_rescue_reason"] = "lean_core_query_requires_compact_review"
            output.append(record)
    return output


def candidate_filter_papers(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return _candidate_filter(records, profile, "paper")


def candidate_filter_news(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return _candidate_filter(records, profile, "news")


def filter_post_enrichment(
    records: list[dict[str, Any]],
    profile: dict[str, Any],
    kind: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enforce evidence identity while preserving verified related-only records.

    Primary records must retain target identity after enrichment.  A biologically
    related non-target record is not promoted to the target report, but remains
    eligible for a metadata-safe supplementary route.  Only hard entity or
    identifier conflicts are terminal.
    """
    if kind not in {"paper", "news"}:
        raise ValueError(f"Unsupported post-enrichment kind: {kind}")
    retained: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    route_counts = {"primary_candidate": 0, "supplementary_related": 0}
    for record in records:
        body = _record_body(record, kind)
        title = "" if kind == "news" else clean_space(record.get("title"))
        assessment = relevance_assessment(title, body, profile)
        record["relevance_post_enrichment"] = assessment
        record["relevance_decision"] = assessment.get("decision")
        record["relevance_route"] = assessment.get("route")
        record["primary_eligible"] = bool(assessment.get("primary_eligible"))
        record["supplementary_eligible"] = bool(assessment.get("supplementary_eligible"))
        if assessment.get("supplementary_reason"):
            record["supplementary_reason"] = assessment.get("supplementary_reason")

        content_identity = record.get("content_identity") or {}
        content_identity_rejected = bool(
            kind == "news"
            and content_identity
            and not content_identity.get("accepted", False)
            and assessment.get("route") != "supplementary_related"
        )
        scholarly_identity_conflict = bool(
            kind == "paper"
            and (
                clean_space(record.get("content_identity_status")) == "identity_conflict"
                or bool(record.get("identifier_conflict"))
                or (record.get("metadata_verification") or {}).get("conflict")
            )
        )
        hard_conflict = bool(assessment.get("hard_entity_conflict"))
        route = assessment.get("route")
        primary_ok = bool(
            clean_space(body)
            and route == "primary_candidate"
            and assessment.get("decision") in {"accept", "review"}
            and not content_identity_rejected
            and not scholarly_identity_conflict
            and not hard_conflict
        )
        supplementary_ok = bool(
            route == "supplementary_related"
            and not scholarly_identity_conflict
            and not hard_conflict
            and (
                clean_space(record.get("title"))
                or clean_space(body)
                or record.get("doi")
                or (record.get("source_ids") or {}).get("pmid")
                or record.get("url")
            )
        )
        if primary_ok or supplementary_ok:
            if supplementary_ok:
                record["display_eligibility"] = "supplementary_only"
                record["supplementary_reason"] = "biologically_related_non_target_entity"
                route_counts["supplementary_related"] += 1
            else:
                record["display_eligibility"] = "primary_candidate"
                route_counts["primary_candidate"] += 1
            retained.append(record)
            continue
        reason = (
            "identifier_conflict"
            if scholarly_identity_conflict
            else "hard_entity_conflict"
            if hard_conflict
            else "content_identity_rejected"
            if content_identity_rejected
            else "post_enrichment_relevance_rejected"
            if assessment.get("decision") == "reject"
            else "missing_enriched_evidence"
        )
        rejected.append({
            "record_id": record.get("news_id") or record.get("paper_id"),
            "title": record.get("title"),
            "source": record.get("source"),
            "reason": reason,
            "assessment": assessment,
            "content_identity": content_identity,
        })
    return retained, {
        "kind": kind,
        "input": len(records),
        "retained": len(retained),
        "rejected": len(rejected),
        "route_counts": route_counts,
        "rejected_records": rejected,
        "policy_version": "v17.4-post-enrichment-tiered-route-1",
    }

def _deterministic_medium_accept(record: dict[str, Any], assessment: dict[str, Any], kind: str) -> bool:
    """Deterministic fallback based on identity evidence, never character length."""
    title_hits = assessment.get("title_identity_hits") or []
    body_hits = assessment.get("body_identity_hits") or []
    context_hits = assessment.get("context_hits") or []
    exclusions = assessment.get("excluded_hits") or assessment.get("exclusion_hits") or []
    reliable_metadata = bool(
        (record.get("metadata_verification") or {}).get("verified")
        or record.get("doi")
        or (record.get("source_ids") or {}).get("pmid")
        or len(record.get("sources") or []) >= 2
    )
    abbreviation_supported = bool(assessment.get("qualified_identity_hits"))
    return bool(
        assessment.get("route", "primary_candidate") == "primary_candidate"
        and assessment.get("identity_present")
        and not (exclusions and not (title_hits or body_hits or abbreviation_supported))
        and (
            title_hits
            or body_hits
            or abbreviation_supported
            or (context_hits and reliable_metadata)
        )
        and (title_hits or reliable_metadata or len(body_hits) >= 1)
    )


def _sentences(text: str) -> list[str]:
    value = clean_space(text)
    if not value:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", value)
    return unique_strings(clean_space(x) for x in parts if clean_space(x))


def _evidence_terms(profile: dict[str, Any]) -> tuple[list[str], list[str], list[str], list[str]]:
    rules = profile.get("post_retrieval_relevance_rules") or {}
    identity = unique_strings(
        (rules.get("title_or_abstract_identity_patterns") or [])
        + [x.get("term") for x in (rules.get("qualified_abbreviation_rules") or []) if isinstance(x, dict)]
    )
    contexts = unique_strings(rules.get("context_patterns") or [])
    related = unique_strings(rules.get("related_entity_patterns") or [])
    hard_exclusions = unique_strings(
        rules.get("hard_excluded_entity_patterns")
        or rules.get("excluded_entity_patterns")
        or []
    )
    return identity, contexts, related, hard_exclusions


def _evidence_snippets(text: str, profile: dict[str, Any], *, max_snippets: int = 4) -> list[str]:
    """Select complete evidence-bearing sentences, never an arbitrary prefix."""
    sentences = _sentences(text)
    if not sentences:
        return []
    identity, contexts, related, hard_exclusions = _evidence_terms(profile)
    scored: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(sentences):
        identity_hits = sum(1 for term in identity if _contains(sentence, term))
        context_hits = sum(1 for term in contexts if _contains(sentence, term))
        related_hits = sum(1 for term in related if _contains(sentence, term))
        hard_exclusion_hits = sum(1 for term in hard_exclusions if _contains(sentence, term))
        score = identity_hits * 12 + related_hits * 7 + context_hits * 2 + hard_exclusion_hits * 4
        if score:
            scored.append((score, -index, sentence))
    selected = [row[2] for row in sorted(scored, reverse=True)[:max_snippets]]
    if not selected:
        selected = sentences[:1] + (sentences[-1:] if len(sentences) > 1 else [])
    return unique_strings(selected)


def _retrieval_anchor_summary(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for query in record.get("retrieval_queries") or []:
        value = clean_space(query)
        if not value:
            continue
        value = re.sub(r"\[[^\]]+\]", "", value)
        value = re.sub(r"\b(?:AND|OR|NOT)\b", " ", value, flags=re.I)
        value = clean_space(value.replace('"', " ").replace("(", " ").replace(")", " "))
        if value:
            values.append(value)
    return unique_strings(values)[:4]


def build_compact_evidence_packet(
    record: dict[str, Any],
    profile: dict[str, Any],
    kind: str,
    record_id: str,
) -> dict[str, Any]:
    body = _record_body(record, kind)
    assessment = record.get("relevance_final") or relevance_assessment(record.get("title", ""), body, profile)
    return {
        "id": record_id,
        "t": clean_space(record.get("title")),
        "q": _retrieval_anchor_summary(record),
        "ch": unique_strings(record.get("retrieval_channels") or [])[:4],
        "ih": unique_strings(assessment.get("identity_hits") or [])[:12],
        "qh": unique_strings(assessment.get("qualified_identity_hits") or [])[:8],
        "cx": unique_strings(assessment.get("context_hits") or [])[:10],
        "rh": unique_strings(assessment.get("related_hits") or [])[:10],
        "xh": unique_strings(assessment.get("hard_excluded_hits") or assessment.get("excluded_hits") or [])[:8],
        "route": assessment.get("route"),
        "ev": _evidence_snippets(body, profile),
        "py": assessment.get("decision"),
        "ps": assessment.get("score"),
        "src": clean_space(record.get("source")),
    }


def _full_evidence_packet(
    record: dict[str, Any],
    profile: dict[str, Any],
    kind: str,
    record_id: str,
) -> dict[str, Any]:
    body = _record_body(record, kind)
    sentences = _sentences(body)
    selected = _evidence_snippets(body, profile, max_snippets=10)
    # Abstracts are normally compact enough to retain in full.  For long full
    # text/news pages, use complete evidence-bearing sentences plus the opening
    # and conclusion sentences rather than truncating by character position.
    if kind == "paper" and len(sentences) <= 30:
        evidence = body
    else:
        evidence = clean_space(" ".join(unique_strings(sentences[:2] + selected + sentences[-3:])))
    return {
        "id": record_id,
        "title": clean_space(record.get("title")),
        "evidence": evidence,
        "retrieval": _retrieval_anchor_summary(record),
        "source": clean_space(record.get("source")),
    }


def estimate_tokens(value: Any) -> int:
    """Conservative tokenizer-independent estimate for mixed EN/ZH JSON."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, math.ceil(ascii_chars / 3.6 + non_ascii_chars / 1.5))


def pack_by_token_budget(
    items: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    token_budget: int,
    fixed_prompt_tokens: int = 1200,
) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Pack every item into dynamic batches; no document-count cutoff exists."""
    available = max(1000, token_budget - fixed_prompt_tokens)
    batches: list[list[tuple[dict[str, Any], dict[str, Any]]]] = []
    current: list[tuple[dict[str, Any], dict[str, Any]]] = []
    used = 0
    for item in items:
        cost = estimate_tokens(item[1]) + 12
        if current and used + cost > available:
            batches.append(current)
            current = []
            used = 0
        current.append(item)
        used += cost
        if used >= available:
            batches.append(current)
            current = []
            used = 0
    if current:
        batches.append(current)
    return batches


def _compact_scope(profile: dict[str, Any]) -> dict[str, Any]:
    scope = profile.get("target_scope") or {}
    rules = profile.get("post_retrieval_relevance_rules") or {}
    contract = profile.get("topic_contract") or {}
    return {
        "profile_id": profile.get("profile_id"),
        "topic": [scope.get("topic_en") or contract.get("topic_en"), scope.get("topic_zh") or contract.get("topic_zh")],
        "scope_statement": contract.get("scope_statement") or " ".join(scope.get("scope_included") or []),
        "target_entities": unique_strings(contract.get("target_entities") or [])[:80],
        "disease_entities": unique_strings(contract.get("disease_entities") or [])[:50],
        "include": unique_strings(scope.get("scope_included") or [])[:12],
        "allowed": unique_strings(contract.get("allowed_members") or scope.get("allowed_members") or [])[:60],
        "related_entities": [
            x if isinstance(x, str) else {"term": x.get("term"), "relation_type": x.get("relation_type"), "display_route": x.get("display_route")}
            for x in (contract.get("related_entities") or scope.get("related_entities") or [])
        ][:80],
        "hard_excluded_entities": unique_strings(
            contract.get("hard_excluded_entities")
            or contract.get("excluded_entities")
            or scope.get("scope_excluded")
            or []
        )[:60],
        "authoritative_evidence": [
            {"source_id": x.get("source_id"), "role": x.get("role"), "statement": x.get("evidence_statement")}
            for x in (profile.get("authoritative_evidence") or []) if x.get("required")
        ][:4],
        "abbr": [
            {"t": x.get("term"), "r": unique_strings(x.get("required_context_terms") or [])[:8]}
            for x in (rules.get("qualified_abbreviation_rules") or [])
            if isinstance(x, dict)
        ],
    }


def _parse_compact_decisions(data: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict):
        return {}
    rows = data.get("d") or data.get("decisions") or []
    output: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("id", ""))
        code = str(item.get("c") or item.get("decision") or "").upper()
        aliases = {
            "ACCEPT": "A", "RELEVANT": "A", "REJECT": "N", "IRRELEVANT": "N", "UNCERTAIN": "U",
            "SUPPLEMENTARY": "S", "RELATED": "S", "RELATED_ONLY": "S",
        }
        code = aliases.get(code, code)
        if rid and code in {"A", "C", "S", "B", "N", "U"}:
            output[rid] = {
                "code": code,
                "confidence": item.get("p") or item.get("confidence"),
                "reason": clean_space(item.get("r") or item.get("reason"))[:120],
            }
    return output


def _call_review_batch(
    llm: LLMRouter,
    profile: dict[str, Any],
    kind: str,
    packets: list[dict[str, Any]],
    *,
    escalated: bool,
) -> dict[str, dict[str, Any]]:
    if not packets or not llm.available:
        return {}
    if escalated:
        task = (
            "Classify whether each record is substantively about the target virus. "
            "A=target is a main subject; C=comparative/co-infection study with material target evidence; "
            "S=biologically/taxonomically related non-target entity with substantive information, retain supplementary-only; "
            "B=target is background/list/reference only; N=hard unrelated/lexical noise/identifier conflict; U=still insufficient."
        )
    else:
        task = (
            "Use the compact Python-extracted evidence. A=target main subject; C=material comparative/co-infection evidence; "
            "S=related non-target entity with substantive information, supplementary-only; "
            "B=background/list/reference only; N=hard unrelated/lexical noise/identifier conflict; U=needs fuller evidence."
        )
    payload = {
        "task": task,
        "kind": kind,
        "scope": _compact_scope(profile),
        "records": packets,
        "out": {"d": [{"id": "record id", "c": "A|C|S|B|N|U", "p": 0, "r": "short reason code"}]},
    }
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "relevance_review.md"
    system = prompt_path.read_text(encoding="utf-8")
    try:
        task_name = "relevance_escalated_review" if escalated else "relevance_compact_review"
        result = llm.json_task(
            system=system,
            prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            provider_order=getattr(llm, "provider_order", lambda purpose: None)("relevance"),
            temperature=0.0,
            max_models_per_provider=1,
            task_name=task_name,
        )
    except LLMError as exc:
        recorder = getattr(llm, "record_task_failure", None)
        if callable(recorder):
            recorder(
                "relevance_escalated_review" if escalated else "relevance_compact_review",
                exc, kind=kind, record_ids=[packet.get("id") for packet in packets],
            )
        return {}
    parsed = _parse_compact_decisions(result.data)
    stage = "escalated" if escalated else "compact"
    for item in parsed.values():
        item["stage"] = stage
    return parsed


def _review_batches_resilient(
    llm: LLMRouter,
    profile: dict[str, Any],
    kind: str,
    batch: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    escalated: bool,
) -> dict[str, dict[str, Any]]:
    packets = [packet for _, packet in batch]
    decisions = _call_review_batch(llm, profile, kind, packets, escalated=escalated)
    expected = {str(packet.get("id")) for packet in packets}
    missing = expected - set(decisions)
    if not missing:
        return decisions
    unresolved = [item for item in batch if str(item[1].get("id")) in missing]
    if len(unresolved) <= 1:
        return decisions
    midpoint = len(unresolved) // 2
    decisions.update(_review_batches_resilient(llm, profile, kind, unresolved[:midpoint], escalated=escalated))
    decisions.update(_review_batches_resilient(llm, profile, kind, unresolved[midpoint:], escalated=escalated))
    return decisions


def _review_cache_key(record: dict[str, Any], profile: dict[str, Any], kind: str) -> str:
    evidence = clean_space(record.get("abstract") or record.get("content") or record.get("excerpt") or record.get("full_text"))
    identity = clean_space(
        record.get("doi")
        or (record.get("source_ids") or {}).get("pmid")
        or record.get("resolved_url")
        or record.get("url")
        or record.get("title")
    )
    profile_fingerprint = clean_space(profile.get("profile_semantic_fingerprint") or profile.get("profile_fingerprint") or profile.get("seed_hash") or profile.get("profile_id"))
    return sha256_text("|".join([REVIEW_POLICY_VERSION, kind, profile_fingerprint, identity, evidence]))


def final_filter(
    records: list[dict[str, Any]],
    profile: dict[str, Any],
    llm: LLMRouter,
    *,
    kind: str,
    review_cache: dict[str, Any] | None = None,
    review_mode: str = "balanced",
    compact_batch_tokens: int = 12000,
    escalation_batch_tokens: int = 10000,
    # Backward-compatible arguments from v5 are accepted but intentionally no
    # longer impose document or character cutoffs.
    max_llm_reviews: int | None = None,
    review_body_chars: int | None = None,
    continue_check: Callable[[], tuple[bool, str]] | None = None,
) -> list[dict[str, Any]]:
    """Python-assess every candidate and send only ambiguous cases to LLM.

    Retrieval uses a small set of broad, direct concepts. The rich vocabulary
    is applied here to every title/abstract/excerpt. High-confidence accepts and
    clear rejects are resolved locally. Review/metadata-pending records are
    packed into compact token-budgeted LLM batches until the queue is empty.
    Full text and news bodies are fetched only after final Top-N selection.
    """
    del max_llm_reviews, review_body_chars
    review_cache = review_cache if review_cache is not None else {}
    body_keys = ("abstract", "full_text", "title") if kind == "paper" else ("content", "excerpt", "title")
    assessed = assess_records(records, profile, body_keys, stage="final", keep=None)

    # Candidate filtering has already removed obvious broad-source noise.  In
    # all_compact mode every remaining candidate is LLM cross-checked.  The
    # uncertain mode is retained for low-cost emergency operation.
    if review_mode in {"uncertain", "balanced"}:
        # Python still assesses 100% of records.  Only genuinely ambiguous
        # records are sent to the LLM in routine operation; high-confidence
        # accepts and clear rejects are resolved deterministically.
        review_records = [
            x for x in assessed
            if (x.get("relevance_final") or {}).get("decision") == "review"
            or bool((x.get("relevance_final") or {}).get("needs_llm_review"))
            or bool(x.get("candidate_rescue_reason"))
        ]
    else:
        review_records = list(assessed)

    record_by_id: dict[str, dict[str, Any]] = {}
    pending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    decisions: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(review_records):
        rid = f"r{index}"
        record_by_id[rid] = record
        cache_key = _review_cache_key(record, profile, kind)
        record["relevance_review_cache_key"] = cache_key
        cached = review_cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("code") in {"A", "C", "S", "B", "N", "U"}:
            decisions[rid] = cached
            record["relevance_review_cache"] = "hit"
        else:
            record["relevance_review_cache"] = "miss"
            pending.append((record, build_compact_evidence_packet(record, profile, kind, rid)))

    review_stop_reason = ""
    if llm.available:
        for batch in pack_by_token_budget(pending, token_budget=max(2000, compact_batch_tokens)):
            if continue_check is not None:
                allowed, reason = continue_check()
                if not allowed:
                    review_stop_reason = reason
                    break
            decisions.update(_review_batches_resilient(llm, profile, kind, batch, escalated=False))
    # Cache compact decisions immediately.  Missing model decisions intentionally
    # fall back to deterministic evidence rules and never disappear silently.
    for rid, result in decisions.items():
        record = record_by_id.get(rid)
        if record is not None:
            review_cache[record["relevance_review_cache_key"]] = result

    uncertain: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for rid, record in record_by_id.items():
        result = decisions.get(rid)
        if result and result.get("code") == "U":
            uncertain.append((record, _full_evidence_packet(record, profile, kind, rid)))

    escalated_decisions: dict[str, dict[str, Any]] = {}
    if llm.available:
        for batch in pack_by_token_budget(uncertain, token_budget=max(2000, escalation_batch_tokens), fixed_prompt_tokens=1000):
            if continue_check is not None:
                allowed, reason = continue_check()
                if not allowed:
                    review_stop_reason = review_stop_reason or reason
                    break
            escalated_decisions.update(_review_batches_resilient(llm, profile, kind, batch, escalated=True))
    for rid, result in escalated_decisions.items():
        record = record_by_id.get(rid)
        if record is not None:
            decisions[rid] = result
            review_cache[record["relevance_review_cache_key"]] = result

    accepted: list[dict[str, Any]] = []
    for rid, record in record_by_id.items():
        assessment = record.get("relevance_final") or {}
        result = decisions.get(rid)
        code = result.get("code") if result else None
        related_only = bool(
            assessment.get("route") == "supplementary_related"
            and assessment.get("related_identity_present")
            and not assessment.get("target_identity_present")
            and not assessment.get("hard_entity_conflict")
        )

        if code in {"A", "C"}:
            # The model may identify material target evidence in a mixed target/
            # related comparison.  A related-only record can never be promoted
            # merely because the model returned A/C; it remains supplementary.
            if related_only:
                record["relevance_decision"] = "retain_related_supplementary_after_llm"
                record["relevance_route"] = "supplementary_related"
                record["display_eligibility"] = "supplementary_only"
                record["primary_eligible"] = False
                record["supplementary_eligible"] = True
                record["supplementary_reason"] = "biologically_related_non_target_entity"
            else:
                record["relevance_decision"] = "accept_after_compact_llm_review"
                record["relevance_route"] = "primary_candidate"
                record["display_eligibility"] = "primary_candidate"
                record["primary_eligible"] = True
            record["relevance_review_method"] = "escalated_llm" if result.get("stage") == "escalated" else "compact_llm"
            record["relevance_llm_code"] = code
            record["relevance_llm_confidence"] = result.get("confidence")
            record["relevance_llm_reason"] = result.get("reason")
            accepted.append(record)
            continue

        if code == "S" or (related_only and code in {None, "B", "U"}):
            record["relevance_decision"] = "retain_related_supplementary"
            record["relevance_review_method"] = (
                "escalated_llm" if result and result.get("stage") == "escalated"
                else "compact_llm" if result
                else "python_related_entity_route"
            )
            record["relevance_llm_code"] = code or "S"
            record["relevance_llm_confidence"] = result.get("confidence") if result else None
            record["relevance_llm_reason"] = result.get("reason") if result else "related_entity_exact_match"
            record["relevance_route"] = "supplementary_related"
            record["display_eligibility"] = "supplementary_only"
            record["primary_eligible"] = False
            record["supplementary_eligible"] = True
            record["supplementary_reason"] = "biologically_related_non_target_entity"
            accepted.append(record)
            continue

        if code in {"B", "N"}:
            record["relevance_decision"] = "reject_after_compact_llm_review"
            record["relevance_review_method"] = "compact_llm"
            record["relevance_llm_code"] = code
            continue

        # No model, model failure, or unresolved U: deterministic evidence rule.
        deterministic_accept = (
            assessment.get("decision") == "accept"
            or _deterministic_medium_accept(record, assessment, kind)
        )
        if deterministic_accept:
            record["relevance_decision"] = "accept_after_deterministic_full_review"
            record["relevance_review_method"] = "python_full_corpus_fallback"
            record["relevance_route"] = "primary_candidate"
            record["display_eligibility"] = "primary_candidate"
            record["primary_eligible"] = True
            if review_stop_reason:
                record["relevance_review_stop_reason"] = review_stop_reason
            accepted.append(record)
        elif related_only:
            record["relevance_decision"] = "retain_related_supplementary_after_deterministic_review"
            record["relevance_review_method"] = "python_related_entity_route"
            record["relevance_route"] = "supplementary_related"
            record["display_eligibility"] = "supplementary_only"
            record["primary_eligible"] = False
            record["supplementary_eligible"] = True
            record["supplementary_reason"] = "biologically_related_non_target_entity"
            accepted.append(record)
        else:
            record["relevance_decision"] = "reject_after_deterministic_full_review"
            record["relevance_review_method"] = "python_full_corpus_fallback"
            if review_stop_reason:
                record["relevance_review_stop_reason"] = review_stop_reason

    # In balanced/uncertain mode, deterministic high-confidence records not
    # queued above still need to be retained.
    if review_mode in {"uncertain", "balanced"}:
        reviewed_ids = {id(x) for x in review_records}
        for record in assessed:
            if id(record) in reviewed_ids:
                continue
            assessment = record.get("relevance_final") or {}
            if assessment.get("decision") == "accept":
                record["relevance_decision"] = "accept_python_high_confidence"
                record["relevance_review_method"] = "python"
                record["relevance_route"] = "primary_candidate"
                record["display_eligibility"] = "primary_candidate"
                record["primary_eligible"] = True
                accepted.append(record)
    return accepted


def filter_relevant_papers(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return assess_records(records, profile, ("abstract", "full_text", "title"), stage="legacy", keep=("accept",))


def filter_relevant_news(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return assess_records(records, profile, ("excerpt", "content", "title"), stage="legacy", keep=("accept",))
