from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .utils import clean_space


ROLE_PRIORITY = {
    "results": 9.0,
    "methods": 8.5,
    "design_population": 8.0,
    "limitations": 7.5,
    "conclusion": 7.0,
    "interpretation": 6.5,
    "implications": 6.0,
    "objective": 5.5,
    "background": 4.0,
    "general": 2.0,
}

NUMERIC_RESULT_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?%|\b\d+\s+of\s+\d+|\bp\s*[=<]|confidence interval|odds ratio|hazard ratio|risk ratio)",
    re.I,
)
METHOD_RE = re.compile(
    r"\b(?:assay|ELISA|PCR|sequenc|regression|model|phylogen|sampling|questionnaire|database|machine learning|statistical)\b",
    re.I,
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())


def _near_duplicate(text: str, selected: list[dict[str, Any]]) -> bool:
    tokens = set(_tokens(text))
    if not tokens:
        return False
    for row in selected:
        other = set(_tokens(clean_space(row.get("text"))))
        if not other:
            continue
        overlap = len(tokens & other) / max(1, len(tokens | other))
        if overlap >= 0.84:
            return True
    return False


def select_evidence_rows(
    rows: list[dict[str, Any]],
    *,
    max_chars: int,
    min_per_role: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select a compact, role-balanced evidence pack without any remote embedding call."""

    max_chars = max(2500, int(max_chars))
    cleaned = [
        {
            "id": clean_space(row.get("id")),
            "role": clean_space(row.get("role")) or "general",
            "text": clean_space(row.get("text")),
        }
        for row in rows
        if clean_space(row.get("id")) and clean_space(row.get("text"))
    ]
    if not cleaned:
        return [], {
            "selector": "role_bm25_numeric_v1",
            "original_rows": 0,
            "selected_rows": 0,
            "original_chars": 0,
            "selected_chars": 0,
        }

    document_frequency: Counter[str] = Counter()
    for row in cleaned:
        document_frequency.update(set(_tokens(row["text"])))
    n_docs = len(cleaned)

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, row in enumerate(cleaned):
        terms = _tokens(row["text"])
        tf = Counter(terms)
        lexical = 0.0
        for token, count in tf.items():
            idf = math.log((n_docs + 1) / (document_frequency[token] + 0.5)) + 1.0
            lexical += min(count, 3) * idf
        lexical = min(lexical / 8.0, 5.0)
        score = ROLE_PRIORITY.get(row["role"], 2.0) + lexical
        if NUMERIC_RESULT_RE.search(row["text"]):
            score += 5.0
        if METHOD_RE.search(row["text"]):
            score += 3.0
        if 60 <= len(row["text"]) <= 900:
            score += 1.5
        position = index / max(1, n_docs - 1)
        if row["role"] in {"results", "conclusion", "limitations", "implications"} and position > 0.35:
            score += 1.0
        scored.append((score, index, row))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    used_chars = 0
    role_counts: Counter[str] = Counter()

    # First reserve at least one row for every diagnostic role that exists.
    roles = [
        "methods",
        "results",
        "design_population",
        "limitations",
        "conclusion",
        "interpretation",
        "implications",
        "objective",
        "background",
        "general",
    ]
    for role in roles:
        candidates = sorted(
            (item for item in scored if item[2]["role"] == role),
            key=lambda item: (-item[0], item[1]),
        )
        for _, _, row in candidates[:max(1, min_per_role)]:
            cost = len(row["text"]) + 80
            if used_chars + cost > max_chars or row["id"] in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row["id"])
            role_counts[role] += 1
            used_chars += cost

    for _, _, row in sorted(scored, key=lambda item: (-item[0], item[1])):
        if row["id"] in selected_ids or _near_duplicate(row["text"], selected):
            continue
        cost = len(row["text"]) + 80
        if used_chars + cost > max_chars:
            continue
        selected.append(row)
        selected_ids.add(row["id"])
        role_counts[row["role"]] += 1
        used_chars += cost

    order = {row["id"]: index for index, row in enumerate(cleaned)}
    selected.sort(key=lambda row: order.get(row["id"], 10**9))
    return selected, {
        "selector": "role_bm25_numeric_v1",
        "original_rows": len(cleaned),
        "selected_rows": len(selected),
        "original_chars": sum(len(row["text"]) for row in cleaned),
        "selected_chars": sum(len(row["text"]) for row in selected),
        "role_counts": dict(role_counts),
        "max_chars": max_chars,
    }
