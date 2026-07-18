from __future__ import annotations

import json
import math
import re
from typing import Any, Callable, Iterable

from .llm import LLMError, LLMRouter
from .utils import clean_space, sha256_text, unique_strings


REVIEW_POLICY_VERSION = "v7-python-first-ambiguous-1"


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
    rules = profile.get("post_retrieval_relevance_rules") or {}
    title = clean_space(title)
    body = clean_space(body)
    combined = clean_space(title + " " + body)
    members = unique_strings(rules.get("member_patterns") or [])
    diseases = unique_strings(rules.get("disease_patterns") or [])
    anchors = unique_strings(
        rules.get("identity_anchor_patterns")
        or [x for x in (rules.get("title_or_abstract_identity_patterns") or []) if x not in set(members + diseases)]
    )
    contexts = unique_strings(rules.get("context_patterns") or [])
    exclusions = unique_strings(rules.get("excluded_entity_patterns") or [])

    title_anchor = [x for x in anchors if _contains(title, x)]
    title_member = [x for x in members if _contains(title, x)]
    title_disease = [x for x in diseases if _contains(title, x)]
    body_anchor = [x for x in anchors if _contains(body, x)]
    body_member = [x for x in members if _contains(body, x)]
    body_disease = [x for x in diseases if _contains(body, x)]
    context_hits = [x for x in contexts if _contains(combined, x)]
    excluded_title = [x for x in exclusions if _contains(title, x)]
    excluded_body = [x for x in exclusions if _contains(body, x)]

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
    if context_hits:
        score += 1
        reasons.append(f"context:{context_hits[:3]}")

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
    if qualified_ok and not (title_anchor or title_member or title_disease or body_anchor or body_member or body_disease):
        score += 3
        reasons.append(f"qualified_identity:{qualified_ok[:3]}")
    if qualified_bad and not (title_anchor or title_member or body_anchor or body_member):
        score -= 4
        reasons.append(f"ambiguous_abbreviation:{qualified_bad[:3]}")

    has_identity = bool(
        title_anchor or title_member or title_disease or body_anchor or body_member or body_disease or qualified_ok
    )
    if context_hits and not has_identity:
        score -= 4
        reasons.append("context_only")
    if excluded_title and not (title_anchor or title_member or title_disease):
        score -= 6
        reasons.append(f"excluded_title:{excluded_title[:3]}")

    identity_frequency = sum(
        _count_occurrences(combined, term)
        for term in unique_strings(anchors + members + diseases + qualified_ok)
    )
    if has_identity and identity_frequency >= 2:
        score += 1
        reasons.append(f"identity_frequency:{identity_frequency}")

    accept = int(rules.get("minimum_relevance_score", 6))
    review = int(rules.get("review_score_min", 3))
    high_confidence_title = bool(title_anchor or title_member or title_disease)
    decision = (
        "accept"
        if has_identity and (score >= accept or high_confidence_title)
        else "review"
        if score >= review and has_identity
        else "reject"
    )
    return {
        "score": score,
        "decision": decision,
        "reasons": reasons,
        "identity_present": has_identity,
        "identity_hits": unique_strings(title_anchor + title_member + title_disease + body_anchor + body_member + body_disease + qualified_ok),
        "title_identity_hits": unique_strings(title_anchor + title_member + title_disease),
        "body_identity_hits": unique_strings(body_anchor + body_member + body_disease),
        "qualified_identity_hits": qualified_ok,
        "qualified_context_hits": qualified_contexts,
        "ambiguous_abbreviation_hits": qualified_bad,
        "context_hits": context_hits,
        "excluded_hits": unique_strings(excluded_title + excluded_body),
        "identity_frequency": identity_frequency,
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
    """Make the existing post-enrichment relevance result enforceable.

    News is assessed with an empty title so an RSS headline cannot rescue an
    unrelated extracted body. Papers retain title-plus-evidence assessment,
    because a valid abstract may be short but remains tied to a scholarly title.
    """
    if kind not in {"paper", "news"}:
        raise ValueError(f"Unsupported post-enrichment kind: {kind}")
    retained: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for record in records:
        body = _record_body(record, kind)
        title = "" if kind == "news" else clean_space(record.get("title"))
        assessment = relevance_assessment(title, body, profile)
        record["relevance_post_enrichment"] = assessment
        record["relevance_decision"] = assessment.get("decision")
        content_identity = record.get("content_identity") or {}
        content_identity_rejected = bool(kind == "news" and content_identity and not content_identity.get("accepted", False))
        accepted = bool(
            clean_space(body)
            and assessment.get("decision") in {"accept", "review"}
            and not content_identity_rejected
        )
        if accepted:
            retained.append(record)
            continue
        reason = (
            "content_identity_rejected"
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
        "rejected_records": rejected,
        "policy_version": "v11-post-enrichment-hard-gate-1",
    }


def _deterministic_medium_accept(record: dict[str, Any], assessment: dict[str, Any], kind: str) -> bool:
    body = _record_body(record, kind)
    return bool(
        assessment.get("identity_present")
        and (
            assessment.get("context_hits")
            or assessment.get("title_identity_hits")
            or len(assessment.get("body_identity_hits") or []) >= 2
        )
        and len(body) >= (80 if kind == "paper" else 120)
    )


def _sentences(text: str) -> list[str]:
    value = clean_space(text)
    if not value:
        return []
    parts = re.split(r"(?<=[.!?。！？])\s+|[\r\n]+", value)
    return unique_strings(clean_space(x) for x in parts if clean_space(x))


def _evidence_terms(profile: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    rules = profile.get("post_retrieval_relevance_rules") or {}
    identity = unique_strings(
        (rules.get("title_or_abstract_identity_patterns") or [])
        + [x.get("term") for x in (rules.get("qualified_abbreviation_rules") or []) if isinstance(x, dict)]
    )
    contexts = unique_strings(rules.get("context_patterns") or [])
    exclusions = unique_strings(rules.get("excluded_entity_patterns") or [])
    return identity, contexts, exclusions


def _evidence_snippets(text: str, profile: dict[str, Any], *, max_snippets: int = 4) -> list[str]:
    """Select complete evidence-bearing sentences, never an arbitrary prefix."""
    sentences = _sentences(text)
    if not sentences:
        return []
    identity, contexts, exclusions = _evidence_terms(profile)
    scored: list[tuple[int, int, str]] = []
    for index, sentence in enumerate(sentences):
        identity_hits = sum(1 for term in identity if _contains(sentence, term))
        context_hits = sum(1 for term in contexts if _contains(sentence, term))
        exclusion_hits = sum(1 for term in exclusions if _contains(sentence, term))
        score = identity_hits * 12 + context_hits * 2 + exclusion_hits * 4
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
        "xh": unique_strings(assessment.get("excluded_hits") or [])[:8],
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
    return {
        "topic": [scope.get("topic_en"), scope.get("topic_zh")],
        "include": unique_strings(scope.get("scope_included") or [])[:12],
        "exclude": unique_strings(scope.get("scope_excluded") or [])[:12],
        "allowed": unique_strings(scope.get("allowed_members") or [])[:40],
        "near": unique_strings(scope.get("non_target_near_neighbors") or [])[:20],
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
        aliases = {"ACCEPT": "A", "RELEVANT": "A", "REJECT": "N", "IRRELEVANT": "N", "UNCERTAIN": "U"}
        code = aliases.get(code, code)
        if rid and code in {"A", "C", "B", "N", "U"}:
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
            "B=target is background/list/reference only; N=wrong entity/abbreviation; U=still insufficient."
        )
    else:
        task = (
            "Use the compact Python-extracted evidence. A=target main subject; C=material comparative/co-infection evidence; "
            "B=background/list/reference only; N=wrong entity/abbreviation; U=needs fuller evidence."
        )
    payload = {
        "task": task,
        "kind": kind,
        "scope": _compact_scope(profile),
        "records": packets,
        "out": {"d": [{"id": "record id", "c": "A|C|B|N|U", "p": 0, "r": "short reason code"}]},
    }
    system = (
        "You are a strict biomedical retrieval adjudicator. Use only supplied evidence and scope. "
        "Do not reward a mere mention. Do not reject a material comparison, co-infection, differential-diagnosis, "
        "or surveillance record when the target has substantive results. Return compact JSON only."
    )
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            temperature=0.0,
            max_models_per_provider=1,
        )
    except LLMError:
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
    profile_fingerprint = clean_space(profile.get("profile_fingerprint") or profile.get("seed_hash") or profile.get("profile_id"))
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
        if isinstance(cached, dict) and cached.get("code") in {"A", "C", "B", "N", "U"}:
            decisions[rid] = cached
            record["relevance_review_cache"] = "hit"
        else:
            record["relevance_review_cache"] = "miss"
            pending.append((record, build_compact_evidence_packet(record, profile, kind, rid)))

    if llm.available:
        for batch in pack_by_token_budget(pending, token_budget=max(2000, compact_batch_tokens)):
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
        if code in {"A", "C"}:
            record["relevance_decision"] = "accept_after_compact_llm_review"
            record["relevance_review_method"] = "escalated_llm" if result.get("stage") == "escalated" else "compact_llm"
            record["relevance_llm_code"] = code
            record["relevance_llm_confidence"] = result.get("confidence")
            record["relevance_llm_reason"] = result.get("reason")
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
            accepted.append(record)
        else:
            record["relevance_decision"] = "reject_after_deterministic_full_review"
            record["relevance_review_method"] = "python_full_corpus_fallback"

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
                accepted.append(record)
    return accepted


def filter_relevant_papers(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return assess_records(records, profile, ("abstract", "full_text", "title"), stage="legacy", keep=("accept",))


def filter_relevant_news(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    return assess_records(records, profile, ("excerpt", "content", "title"), stage="legacy", keep=("accept",))
