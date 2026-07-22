from __future__ import annotations

from typing import Any

from ..utils import clean_space
from .normalization import metadata_verification, verified_evidence_status

SELECTION_POLICY_VERSION = "v17.4-primary-and-related-supplementary-1"

_SUPPLEMENTARY_FIELDS = (
    "paper_id", "title", "title_zh", "authors", "journal", "year", "volume", "issue", "pages",
    "doi", "source_ids", "url", "sources", "source", "canonical_publication_date",
    "canonical_publication_date_basis", "availability_date", "availability_date_basis",
    "publication_date_status", "priority_tier", "priority_tier_reason", "quality_score",
    "metadata_verification", "evidence_status", "content_completion", "publication_types",
    "relevance_route", "display_eligibility", "primary_eligible", "supplementary_eligible",
    "related_hits", "related_hit_details", "relevance_decision", "relevance_review_method",
)


def build_supplementary_view(record: dict[str, Any], *, reason: str) -> dict[str, Any]:
    view = {key: record.get(key) for key in _SUPPLEMENTARY_FIELDS if record.get(key) not in (None, "", [], {})}
    view["supplementary_reason"] = reason
    related = reason == "biologically_related_non_target_entity" or record.get("relevance_route") == "supplementary_related"
    view["display_mode"] = "supplementary_related" if related else "metadata_only"
    if related:
        view["notice_zh"] = "本条涉及与目标病毒具有分类学、宿主、生态、比较研究或鉴别诊断关系的非目标病毒。因当前证据不足以建立目标病毒主文献身份，保留在补充目录，不生成目标病毒结论或结构化分析。"
        view["notice_en"] = "This record concerns a biologically, taxonomically, ecologically or methodologically related non-target virus. Target-virus evidence is insufficient for the primary report, so it is retained only in the supplementary catalog without target-virus conclusions or structured analysis."
    else:
        view["notice_zh"] = "摘要尚未公开或本条未进入深度主报告。本条仅提供经过核验的出版元数据，不生成研究结论和结构化要素。"
        view["notice_en"] = "The abstract is not public or this record was not selected for the deep report. Only verified publication metadata are shown; no research conclusions or structured elements are generated."
    return view


def select_primary_and_supplementary(
    catalog: list[dict[str, Any]],
    *,
    primary_ready: list[dict[str, Any]],
    primary_limit: int,
    supplementary_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep Top-N deep reports while retaining other relevant records."""

    primary = primary_ready[: max(0, primary_limit)]
    primary_ids = {item.get("paper_id") for item in primary}
    supplemental: list[dict[str, Any]] = []
    rejected_unverified = 0
    for record in catalog:
        if record.get("paper_id") in primary_ids:
            continue
        verification = record.get("metadata_verification") or metadata_verification(record)
        evidence = record.get("evidence_status") or verified_evidence_status(record)
        if not verification.get("verified"):
            rejected_unverified += 1
            continue
        if record.get("relevance_route") == "supplementary_related" or record.get("display_eligibility") == "supplementary_only":
            reason = "biologically_related_non_target_entity"
        elif evidence.get("has_verified_evidence"):
            reason = "relevant_evidence_not_selected_for_primary_top_n"
        else:
            reason = "verified_metadata_without_public_abstract_or_full_text"
        supplemental.append(build_supplementary_view(record, reason=reason))
        if len(supplemental) >= max(0, supplementary_limit):
            break
    audit = {
        "policy_version": SELECTION_POLICY_VERSION,
        "catalog_relevant": len(catalog),
        "primary_limit": primary_limit,
        "primary_displayed": len(primary),
        "supplementary_limit": supplementary_limit,
        "supplementary_displayed": len(supplemental),
        "rejected_unverified_metadata": rejected_unverified,
        "related_supplementary_displayed": sum(item.get("supplementary_reason") == "biologically_related_non_target_entity" for item in supplemental),
        "primary_ids": [item.get("paper_id") for item in primary],
        "supplementary_ids": [item.get("paper_id") for item in supplemental],
    }
    return primary, supplemental, audit
