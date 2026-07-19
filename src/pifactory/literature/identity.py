from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from ..utils import clean_space, normalize_title, unique_strings
from ..dates import parse_date_span
from .normalization import canonical_identifiers

IDENTITY_POLICY_VERSION = "v15.1-multifactor-content-identity-1"


def _year(value: Any) -> int | None:
    text = clean_space(value)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _author_families(values: Any) -> set[str]:
    output: set[str] = set()
    for value in values or []:
        name = normalize_title(value)
        if name:
            output.add(name.split()[-1])
    return output


def _similarity(left: Any, right: Any) -> float:
    a = normalize_title(left)
    b = normalize_title(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def assess_completion_identity(expected: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Classify a completion candidate without allowing weak matches to hide conflicts."""

    expected_ids = canonical_identifiers(expected)
    candidate_ids = canonical_identifiers(candidate)
    identifier_matches: list[str] = []
    identifier_conflicts: list[str] = []
    for key in ("doi", "pmid", "pmcid"):
        left = clean_space(expected_ids.get(key)).casefold()
        right = clean_space(candidate_ids.get(key)).casefold()
        if left and right:
            if left == right:
                identifier_matches.append(key)
            else:
                identifier_conflicts.append(key)

    title_score = _similarity(expected.get("title"), candidate.get("title"))
    expected_authors = _author_families(expected.get("authors"))
    candidate_authors = _author_families(candidate.get("authors"))
    author_overlap = sorted(expected_authors & candidate_authors)
    author_match = bool(author_overlap)
    journal_score = _similarity(
        expected.get("journal") or expected.get("venue") or expected.get("publisher"),
        candidate.get("journal") or candidate.get("venue") or candidate.get("publisher"),
    )
    expected_year = _year(expected.get("year") or expected.get("canonical_publication_date") or expected.get("online_date"))
    candidate_year = _year(candidate.get("year") or candidate.get("first_publication_date") or candidate.get("published_date"))
    year_match = bool(expected_year and candidate_year and abs(expected_year - candidate_year) <= 1)
    year_conflict = bool(expected_year and candidate_year and abs(expected_year - candidate_year) >= 3)

    if identifier_conflicts:
        status = "identity_conflict"
        reason = "explicit_identifier_mismatch"
    elif identifier_matches:
        # A shared DOI/PMID/PMCID is strong evidence. A clearly unrelated title is
        # still retained in the audit as suspicious, but explicit identifier equality
        # remains the stronger signal.
        status = "identity_verified"
        reason = "shared_identifier"
    else:
        support = sum([title_score >= 0.88, author_match, journal_score >= 0.78, year_match])
        weaker_support = sum([title_score >= 0.72, author_match, journal_score >= 0.62, year_match])
        if title_score >= 0.88 and support >= 3 and not year_conflict:
            status = "identity_verified"
            reason = "bibliographic_multifactor_match"
        elif title_score >= 0.72 and weaker_support >= 3 and not year_conflict:
            status = "identity_uncertain"
            reason = "probable_bibliographic_match_requires_caution"
        elif year_conflict or (candidate_ids and title_score < 0.45):
            status = "identity_conflict"
            reason = "bibliographic_conflict"
        else:
            status = "identity_uncertain"
            reason = "insufficient_identity_evidence"

    return {
        "policy_version": IDENTITY_POLICY_VERSION,
        "status": status,
        "reason": reason,
        "identifier_matches": identifier_matches,
        "identifier_conflicts": identifier_conflicts,
        "title_score": round(title_score, 4),
        "author_match": author_match,
        "author_overlap": author_overlap,
        "journal_score": round(journal_score, 4),
        "year_match": year_match,
        "year_conflict": year_conflict,
        "expected_year": expected_year,
        "candidate_year": candidate_year,
        "expected_identifiers": expected_ids,
        "candidate_identifiers": candidate_ids,
    }


def register_identity_assessment(record: dict[str, Any], assessment: dict[str, Any], *, method: str) -> None:
    row = {**assessment, "method": method}
    record.setdefault("content_identity_assessments", []).append(row)
    if assessment.get("status") == "identity_conflict":
        # Conflicts are monotonic. Later title-only or metadata-only matches cannot
        # clear an explicit DOI/PMID/PMCID conflict.
        record["identifier_conflict"] = {
            "policy_version": IDENTITY_POLICY_VERSION,
            "method": method,
            "reason": assessment.get("reason"),
            "identifier_conflicts": assessment.get("identifier_conflicts") or [],
            "assessment": row,
        }
        record["content_identity_status"] = "identity_conflict"
    elif record.get("content_identity_status") != "identity_conflict":
        record["content_identity_status"] = assessment.get("status") or "identity_uncertain"


def _earliest_date(left: Any, right: Any) -> Any:
    a = parse_date_span(left)
    b = parse_date_span(right)
    if not a:
        return right
    if not b:
        return left
    return left if a.start <= b.start else right


def merge_verified_candidate(record: dict[str, Any], candidate: dict[str, Any], *, method: str) -> dict[str, Any]:
    assessment = assess_completion_identity(record, candidate)
    register_identity_assessment(record, assessment, method=method)
    if assessment["status"] != "identity_verified" or record.get("identifier_conflict"):
        return assessment

    expected_ids = record.get("source_ids") or {}
    candidate_ids = candidate.get("source_ids") or {}
    record["source_ids"] = {**expected_ids, **{k: v for k, v in candidate_ids.items() if v}}
    if not record.get("doi") and candidate.get("doi"):
        record["doi"] = clean_space(candidate.get("doi")).lower()
    for field in ("title", "journal", "year", "volume", "issue", "pages", "url"):
        if not record.get(field) and candidate.get(field):
            record[field] = candidate[field]
    for field in ("first_publication_date", "online_date", "published_date", "print_date"):
        if candidate.get(field):
            record[field] = _earliest_date(record.get(field), candidate.get(field))
    record.setdefault("date_completion_sources", []).append({
        "method": method,
        "first_publication_date": candidate.get("first_publication_date"),
        "online_date": candidate.get("online_date"),
        "published_date": candidate.get("published_date"),
        "print_date": candidate.get("print_date"),
    })
    if len(clean_space(candidate.get("abstract"))) > len(clean_space(record.get("abstract"))):
        record["abstract"] = clean_space(candidate.get("abstract"))
        record["abstract_source"] = method
    record["authors"] = unique_strings((record.get("authors") or []) + (candidate.get("authors") or []))
    record["publication_types"] = unique_strings((record.get("publication_types") or []) + (candidate.get("publication_types") or []))
    record["sources"] = unique_strings((record.get("sources") or []) + [candidate.get("source") or method])
    return assessment
