from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .utils import clean_space, unique_strings


@dataclass(frozen=True)
class EntityHit:
    term: str
    category: str
    start: int
    end: int
    relation_type: str = ""
    display_route: str = ""


def normalized_text(value: str) -> str:
    value = clean_space(value).casefold()
    value = value.replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def term_pattern(term: str) -> re.Pattern[str] | None:
    value = normalized_text(term)
    if not value:
        return None
    tokens = [re.escape(x) for x in value.split()]
    return re.compile(r"(?<![a-z0-9])" + r"[^a-z0-9]+".join(tokens) + r"(?![a-z0-9])", re.I)


def _term_rows(values: Iterable[Any], *, default_relation: str = "", default_route: str = "") -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            term = clean_space(value.get("term"))
            relation_type = clean_space(value.get("relation_type") or default_relation)
            display_route = clean_space(value.get("display_route") or default_route)
        else:
            term = clean_space(value)
            relation_type = default_relation
            display_route = default_route
        key = normalized_text(term)
        if not term or not key or key in seen:
            continue
        seen.add(key)
        rows.append({"term": term, "relation_type": relation_type, "display_route": display_route})
    return rows


def find_hits(text: str, terms: Iterable[Any], category: str) -> list[EntityHit]:
    hay = clean_space(text).casefold().replace("–", "-").replace("—", "-")
    defaults = {
        "related": ("taxonomic_or_biological_neighbour", "supplementary"),
        "hard_excluded": ("unrelated_or_lexical_noise", "reject"),
    }
    default_relation, default_route = defaults.get(category, ("", ""))
    hits: list[EntityHit] = []
    for row in _term_rows(terms, default_relation=default_relation, default_route=default_route):
        pattern = term_pattern(row["term"])
        if not pattern:
            continue
        for match in pattern.finditer(hay):
            hits.append(
                EntityHit(
                    term=row["term"],
                    category=category,
                    start=match.start(),
                    end=match.end(),
                    relation_type=row["relation_type"],
                    display_route=row["display_route"],
                )
            )
    return hits


def _overlap(a: EntityHit, b: EntityHit) -> bool:
    return a.start < b.end and b.start < a.end


def longest_non_overlapping(hits: Iterable[EntityHit]) -> list[EntityHit]:
    """Resolve complete entities before embedded shorter strings.

    Length is the default signal, but a host/taxon-qualified related entity may
    begin before an overlapping generic target disease phrase.  In that case
    the explicit related entity wins even when the trailing generic phrase is a
    few characters longer (for example ``bovine enterovirus infection``).
    A canonical target that fully contains a shorter related token, such as
    ``SARS-CoV-2`` over ``SARS-CoV``, still wins.  This resolves identity and
    routing; it does not delete related records.
    """
    priority = {
        "hard_excluded": 0,
        "related": 1,
        "target": 2,
        "member": 3,
        "disease": 4,
        "qualified": 5,
        "context": 6,
    }

    def preferred(a: EntityHit, b: EntityHit) -> EntityHit:
        # Terminal hard lexical/unrelated entities always dominate an overlap.
        if a.category == "hard_excluded" and b.category != "hard_excluded":
            return a
        if b.category == "hard_excluded" and a.category != "hard_excluded":
            return b
        a_related = a.category == "related"
        b_related = b.category == "related"
        a_target = a.category in {"target", "member", "disease"}
        b_target = b.category in {"target", "member", "disease"}
        if a_related and b_target:
            if a.start < b.start:
                return a
            if b.start < a.start:
                return b
        if b_related and a_target:
            if b.start < a.start:
                return b
            if a.start < b.start:
                return a
        length_a = a.end - a.start
        length_b = b.end - b.start
        if length_a != length_b:
            return a if length_a > length_b else b
        return a if priority.get(a.category, 9) < priority.get(b.category, 9) else b

    ordered = sorted(list(hits), key=lambda x: (x.start, -(x.end - x.start), priority.get(x.category, 9)))
    selected: list[EntityHit] = []
    for hit in ordered:
        overlaps = [existing for existing in selected if _overlap(hit, existing)]
        if not overlaps:
            selected.append(hit)
            continue
        winners = [preferred(hit, existing) for existing in overlaps]
        if all(winner is hit for winner in winners):
            selected = [existing for existing in selected if existing not in overlaps]
            selected.append(hit)
    return sorted(selected, key=lambda x: (x.start, x.end, priority.get(x.category, 9)))

def resolve_entities(text: str, contract: dict[str, Any]) -> dict[str, Any]:
    target = list(contract.get("target_entities") or [])
    members = list(contract.get("allowed_members") or [])
    diseases = list(contract.get("disease_entities") or [])
    related = list(contract.get("related_entities") or contract.get("supplementary_related_entities") or [])
    hard_excluded = list(contract.get("hard_excluded_entities") or contract.get("excluded_entities") or [])
    hits = longest_non_overlapping(
        find_hits(text, hard_excluded, "hard_excluded")
        + find_hits(text, related, "related")
        + find_hits(text, target, "target")
        + find_hits(text, members, "member")
        + find_hits(text, diseases, "disease")
    )
    keys = ("target", "member", "disease", "related", "hard_excluded")
    by_category: dict[str, list[str]] = {key: [] for key in keys}
    related_details: list[dict[str, str]] = []
    for hit in hits:
        by_category.setdefault(hit.category, []).append(hit.term)
        if hit.category == "related":
            related_details.append(
                {
                    "term": hit.term,
                    "relation_type": hit.relation_type,
                    "display_route": hit.display_route or "supplementary",
                }
            )
    for key in by_category:
        by_category[key] = unique_strings(by_category[key])
    dedup_details: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in related_details:
        key = (row["term"].casefold(), row["relation_type"], row["display_route"])
        if key not in seen:
            seen.add(key)
            dedup_details.append(row)
    return {
        "hits": [hit.__dict__ for hit in hits],
        "target_hits": by_category["target"],
        "member_hits": by_category["member"],
        "disease_hits": by_category["disease"],
        "related_hits": by_category["related"],
        "related_hit_details": dedup_details,
        "hard_excluded_hits": by_category["hard_excluded"],
        # Compatibility alias: v17.4 uses this only for genuinely hard rejects.
        "excluded_hits": by_category["hard_excluded"],
        "target_identity_present": bool(by_category["target"] or by_category["member"] or by_category["disease"]),
        "related_identity_present": bool(by_category["related"]),
    }
