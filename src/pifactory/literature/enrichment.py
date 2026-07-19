from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from ..utils import clean_space
from .normalization import metadata_verification, normalize_literature_record, verified_evidence_status

ENRICHMENT_POLICY_VERSION = "v15-dedup-first-dynamic-completion-1"

_INVALID_PATTERNS = [
    ("http_404", re.compile(r"\b404\b.{0,80}\b(?:not found|page not found)\b|页面不存在|未找到页面", re.I | re.S)),
    ("login_wall", re.compile(r"\b(?:sign in|log in|institutional access|purchase access|subscribe to access)\b|登录后访问|机构订阅", re.I)),
    ("cookie_wall", re.compile(r"\b(?:cookie preferences|manage cookies|accept all cookies|privacy choices)\b|Cookie设置|接受Cookie", re.I)),
    ("javascript_placeholder", re.compile(r"enable javascript|javascript is required|please turn on javascript|请启用javascript", re.I)),
    ("navigation_only", re.compile(r"^(?:home|menu|search|browse|about|contact|privacy|terms)(?:\s+[|·/>-]\s+.*){2,}$", re.I)),
    ("reference_only", re.compile(r"^(?:references?|bibliography)\s*(?:\[?\d+\]?\s+.{0,180}){3,}$", re.I | re.S)),
    ("copyright_only", re.compile(r"^(?:copyright|©|all rights reserved|permissions?)\b.{0,500}$", re.I | re.S)),
]


def classify_scholarly_payload(text: Any, *, status_code: int | None = None, content_type: str = "", url: str = "") -> dict[str, Any]:
    value = clean_space(text)
    if status_code == 404:
        return {"valid": False, "reason": "http_404", "chars": len(value), "url": url}
    if status_code is not None and status_code >= 400:
        return {"valid": False, "reason": f"http_{status_code}", "chars": len(value), "url": url}
    if not value:
        return {"valid": False, "reason": "empty_payload", "chars": 0, "url": url}
    for reason, pattern in _INVALID_PATTERNS:
        if pattern.search(value[:12000]):
            return {"valid": False, "reason": reason, "chars": len(value), "url": url}
    # No minimum-character deletion: a short abstract or publisher snippet can be
    # legitimate.  Identity verification is handled independently.
    return {"valid": True, "reason": "article_like_payload", "chars": len(value), "content_type": content_type, "url": url}


def complete_literature_catalog(
    records: list[dict[str, Any]],
    *,
    enrich_one: Callable[[dict[str, Any]], dict[str, Any]],
    primary_target: int,
    max_budget: int,
    batch_size: int = 25,
    workers: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Complete content after deduplication using contiguous ranked batches.

    The function stops network completion when enough evidence-bearing records
    exist for the primary-report target, candidates are exhausted, or the explicit
    budget is reached.  Unprocessed/metadata-only records remain in the catalog.
    """

    catalog = [normalize_literature_record(dict(item)) for item in records]
    for item in catalog:
        metadata_verification(item)
        verified_evidence_status(item)
        item.setdefault("content_completion", {
            "policy_version": ENRICHMENT_POLICY_VERSION,
            "status": "not_attempted",
            "reason": "outside_dynamic_budget",
        })

    budget = min(len(catalog), max(0, int(max_budget)))
    batch_size = max(1, int(batch_size))
    processed = 0
    batches: list[dict[str, Any]] = []

    def evidence_count() -> int:
        return sum(bool((item.get("evidence_status") or {}).get("has_verified_evidence")) for item in catalog)

    while processed < budget and evidence_count() < max(0, primary_target):
        stop = min(budget, processed + batch_size)
        batch = catalog[processed:stop]
        outputs: dict[int, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batch)))) as executor:
            future_map = {executor.submit(enrich_one, dict(item)): index for index, item in enumerate(batch)}
            for future in as_completed(future_map):
                index = future_map[future]
                try:
                    outputs[index] = future.result()
                except Exception as exc:  # network/provider failures must not delete metadata
                    failed = dict(batch[index])
                    failed["content_completion"] = {
                        "policy_version": ENRICHMENT_POLICY_VERSION,
                        "status": "failed",
                        "reason": "completion_exception",
                        "error": clean_space(exc)[:500],
                    }
                    outputs[index] = failed
        before = evidence_count()
        for local_index, original in enumerate(batch):
            enriched = normalize_literature_record(outputs.get(local_index, original))
            verification = metadata_verification(enriched)
            evidence = verified_evidence_status(enriched)
            enriched["content_completion"] = {
                "policy_version": ENRICHMENT_POLICY_VERSION,
                "status": "evidence_ready" if evidence["has_verified_evidence"] else "metadata_only",
                "metadata_verified": verification["verified"],
                "evidence_level": evidence["level"],
                "attempted": True,
            }
            catalog[processed + local_index] = enriched
        after = evidence_count()
        batches.append({
            "batch": len(batches) + 1,
            "start_rank": processed + 1,
            "end_rank": stop,
            "processed": len(batch),
            "evidence_before": before,
            "evidence_after": after,
            "new_evidence": max(0, after - before),
        })
        processed = stop

    for index, item in enumerate(catalog):
        if index >= processed and (item.get("content_completion") or {}).get("status") == "not_attempted":
            item["content_completion"]["reason"] = (
                "primary_target_reached" if evidence_count() >= primary_target else "completion_budget_exhausted"
            )

    audit = {
        "policy_version": ENRICHMENT_POLICY_VERSION,
        "catalog_size": len(catalog),
        "primary_target": primary_target,
        "max_budget": max_budget,
        "effective_budget": budget,
        "batch_size": batch_size,
        "processed": processed,
        "evidence_ready": evidence_count(),
        "metadata_only": sum((item.get("evidence_status") or {}).get("level") == "metadata_only" for item in catalog),
        "batches": batches,
        "stop_reason": (
            "primary_target_reached" if evidence_count() >= primary_target
            else "candidate_exhausted" if processed >= len(catalog)
            else "completion_budget_exhausted"
        ),
    }
    return catalog, audit
