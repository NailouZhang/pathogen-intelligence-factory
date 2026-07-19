from __future__ import annotations

import re
from typing import Any

from ..utils import clean_space, normalize_title, unique_strings
from ..dates import REAL_PUBLICATION_DATE_FIELDS, parse_date_span

NORMALIZATION_POLICY_VERSION = "v15.1-canonical-literature-object-2"


def _normalize_doi(value: Any) -> str:
    text = clean_space(value).lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".,;)]}")


def _normalize_pmid(value: Any) -> str:
    return re.sub(r"\D", "", clean_space(value))


def _normalize_pmcid(value: Any) -> str:
    text = clean_space(value).upper().replace(" ", "")
    if text and not text.startswith("PMC") and text.isdigit():
        text = "PMC" + text
    return text


def canonical_identifiers(record: dict[str, Any]) -> dict[str, str]:
    ids = dict(record.get("source_ids") or {})
    doi = _normalize_doi(record.get("doi") or ids.get("doi"))
    pmid = _normalize_pmid(ids.get("pmid") or record.get("pmid"))
    pmcid = _normalize_pmcid(ids.get("pmcid") or record.get("pmcid"))
    return {key: value for key, value in {"doi": doi, "pmid": pmid, "pmcid": pmcid}.items() if value}


def normalize_literature_record(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    identifiers = canonical_identifiers(out)
    out["doi"] = identifiers.get("doi") or None
    out["source_ids"] = {**(out.get("source_ids") or {}), **identifiers}
    out["title"] = clean_space(out.get("title"))
    out["authors"] = unique_strings(out.get("authors") or [])
    out["journal"] = clean_space(out.get("journal") or out.get("venue") or out.get("publisher"))
    out["sources"] = unique_strings((out.get("sources") or []) + [out.get("source")])
    span = None
    basis = None
    for field in REAL_PUBLICATION_DATE_FIELDS:
        candidate = parse_date_span(out.get(field))
        if candidate:
            span = candidate
            basis = field
            break
    out["canonical_publication_date"] = span.raw if span else clean_space(
        out.get("canonical_publication_date") or out.get("availability_date")
    )
    out["canonical_publication_date_basis"] = basis or clean_space(
        out.get("canonical_publication_date_basis") or out.get("availability_date_basis")
    )
    out["availability_date"] = out.get("canonical_publication_date")
    out["availability_date_basis"] = out.get("canonical_publication_date_basis")
    out["normalization_policy_version"] = NORMALIZATION_POLICY_VERSION
    return out


def metadata_verification(record: dict[str, Any]) -> dict[str, Any]:
    identifiers = canonical_identifiers(record)
    title = clean_space(record.get("title"))
    source = clean_space(record.get("source"))
    sources = unique_strings((record.get("sources") or []) + [source])
    journal = clean_space(record.get("journal"))
    authors = unique_strings(record.get("authors") or [])
    canonical_date = clean_space(record.get("canonical_publication_date") or record.get("availability_date"))
    reasons: list[str] = []
    conflicts: list[str] = []
    if not title or len(normalize_title(title)) < 8:
        reasons.append("missing_or_unverifiable_title")
    if not canonical_date:
        reasons.append("missing_canonical_publication_date")
    if record.get("identifier_conflict"):
        conflicts.append("identifier_title_conflict")
    strong_identifier = bool(identifiers)
    multi_source = len(sources) >= 2
    bibliographic_support = bool(journal and (authors or record.get("year")))
    source_url_support = bool(source and clean_space(record.get("url")))
    if not (strong_identifier or multi_source or bibliographic_support or source_url_support):
        reasons.append("insufficient_identity_evidence")
    existing_identity = clean_space(record.get("content_identity_status"))
    if conflicts or existing_identity == "identity_conflict":
        status = "identity_conflict"
    elif not reasons:
        status = "identity_verified"
    else:
        status = "identity_uncertain"
    result = {
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "status": status,
        "verified": status == "identity_verified",
        "uncertain": status == "identity_uncertain",
        "conflict": status == "identity_conflict",
        "identifiers": identifiers,
        "sources": sources,
        "strong_identifier": strong_identifier,
        "multi_source": multi_source,
        "bibliographic_support": bibliographic_support,
        "reasons": reasons,
        "conflicts": conflicts,
    }
    record["metadata_verification"] = result
    return result


def verified_evidence_status(record: dict[str, Any]) -> dict[str, Any]:
    abstract = clean_space(record.get("abstract"))
    full_text = clean_space(record.get("full_text"))
    audit = record.get("content_audit") or {}
    identity_rejected = any(
        attempt.get("status") == "identity_rejected"
        for attempt in (audit.get("attempts") or []) if isinstance(attempt, dict)
    ) and not (abstract and clean_space(record.get("abstract_source")))
    if full_text and not identity_rejected:
        level = "full_text"
        source = clean_space(record.get("full_text_method")) or "retrieved_full_text"
    elif abstract:
        level = "abstract"
        source = clean_space(record.get("abstract_source") or record.get("source")) or "retrieved_abstract"
    else:
        level = "metadata_only"
        source = "verified_metadata"
    result = {
        "policy_version": NORMALIZATION_POLICY_VERSION,
        "level": level,
        "has_verified_evidence": level in {"abstract", "full_text"},
        "source": source,
        "identity_rejected": identity_rejected,
    }
    record["evidence_status"] = result
    record["evidence_level"] = "E2" if level == "full_text" else "E1" if level == "abstract" else "E0"
    return result
