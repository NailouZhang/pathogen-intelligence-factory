from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

from .utils import clean_space, unique_strings

POLICY_VERSION = "v14-scholarly-record-type-hard-gate-1"

DEFAULT_REJECT_TYPES = {
    "dataset", "data set", "component", "grant", "peer review", "report component",
    "supplementary material", "supplement", "posted content dataset", "reference entry",
}
DEFAULT_REPOSITORY_HOSTS = {
    "figshare.com", "api.figshare.com", "zenodo.org", "dryad.org", "datadryad.org",
}
DEFAULT_REPOSITORY_NAMES = {"figshare", "zenodo", "dryad", "data dryad"}
TITLE_CUES = re.compile(
    r"\b(dataset|data set|supplementary material|supporting information|source data|"
    r"figure dataset|dataset of figures|code repository|data repository|raw data files?)\b|"
    r"补充材料|数据集|原始数据|源数据|代码仓库",
    flags=re.I,
)


def _csv_set(name: str, defaults: set[str]) -> set[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return set(defaults)
    return {clean_space(item).casefold() for item in raw.split(",") if clean_space(item)}


def _host(value: Any) -> str:
    text = clean_space(value)
    if not text:
        return ""
    try:
        return (urlparse(text).hostname or "").casefold()
    except ValueError:
        return ""


def assess_scholarly_record(record: dict[str, Any]) -> dict[str, Any]:
    types = unique_strings(record.get("publication_types") or [])
    type_norm = {clean_space(value).casefold().replace("_", "-") for value in types}
    reject_types = _csv_set("PIF_REJECT_PUBLICATION_TYPES", DEFAULT_REJECT_TYPES)
    type_hits = sorted(value for value in type_norm if value in reject_types or any(x in value for x in reject_types))

    source_text = " ".join(
        clean_space(record.get(key)) for key in ("source", "journal", "publisher", "venue") if record.get(key)
    ).casefold()
    hosts = unique_strings(
        _host(value)
        for value in [record.get("url"), record.get("open_access_pdf"), *(record.get("full_text_urls") or [])]
        if value
    )
    repository_hosts = _csv_set("PIF_REJECT_REPOSITORY_HOSTS", DEFAULT_REPOSITORY_HOSTS)
    repository_names = _csv_set("PIF_REJECT_REPOSITORY_NAMES", DEFAULT_REPOSITORY_NAMES)
    host_hits = sorted(host for host in hosts if any(host == blocked or host.endswith("." + blocked) for blocked in repository_hosts))
    source_hits = sorted(name for name in repository_names if name in source_text)
    doi = clean_space(record.get("doi")).casefold()
    doi_hit = "figshare" if doi.startswith("10.6084/m9.figshare") else "zenodo" if doi.startswith("10.5281/zenodo") else ""
    title = clean_space(record.get("title"))
    title_hit = bool(TITLE_CUES.search(title))

    # A repository hit is a hard rejection by default.  This prevents data and
    # supplementary objects from consuming relevance, full-text and LLM quotas.
    # Operators can remove a host/name through environment configuration if a
    # future profile explicitly needs repository records.
    reasons: list[str] = []
    if type_hits:
        reasons.append("rejected_publication_type")
    if host_hits or source_hits or doi_hit:
        reasons.append("rejected_repository_platform")
    if title_hit:
        reasons.append("rejected_non_article_title_cue")
    accepted = not reasons
    return {
        "accepted": accepted,
        "policy_version": POLICY_VERSION,
        "reasons": reasons,
        "publication_types": types,
        "type_hits": type_hits,
        "source_hits": source_hits,
        "host_hits": host_hits,
        "doi_platform_hit": doi_hit,
        "title_cue_hit": title_hit,
    }


def filter_scholarly_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for record in records:
        decision = assess_scholarly_record(record)
        record["scholarly_record_type_gate"] = decision
        if decision["accepted"]:
            accepted.append(record)
        else:
            rejected.append({
                "source": record.get("source"),
                "journal": record.get("journal"),
                "doi": record.get("doi"),
                "title": record.get("title"),
                "publication_types": record.get("publication_types") or [],
                "decision": decision,
            })
    return accepted, {
        "policy_version": POLICY_VERSION,
        "input": len(records),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rejected_records": rejected,
    }
