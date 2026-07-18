from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz.fuzz import ratio, token_set_ratio

from .llm import LLMError, LLMRouter
from .utils import clean_space, extract_doi, normalize_title, sha256_text, unique_strings


def _title_signature(value: str | None) -> str:
    title = normalize_title(value)
    title = re.sub(r"\b(preprint|early view|online ahead of print|accepted manuscript|version \d+)\b", " ", title)
    title = re.sub(r"\b(a|an|the)\b", " ", title)
    return clean_space(title)


def _canonical_url(value: str | None) -> str:
    url = clean_space(value)
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid", "ref"}]
        path = re.sub(r"/+$", "", parts.path) or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(keep), ""))
    except Exception:
        return url


def _paper_key(record: dict[str, Any]) -> str:
    doi = clean_space(record.get("doi")).lower()
    if doi:
        return f"doi:{doi}"
    ids = record.get("source_ids") or {}
    for field in ("pmid", "pmcid"):
        value = clean_space(ids.get(field)).lower()
        if value:
            return f"{field}:{value}"
    title = _title_signature(record.get("title"))
    first_author = normalize_title((record.get("authors") or [""])[0])
    return f"title:{title}|author:{first_author}"


def _merge_paper(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    base.setdefault("sources", [])
    base["sources"] = unique_strings(base["sources"] + [base.get("source"), incoming.get("source")])
    base.setdefault("source_records", []).append(incoming)
    base["authors"] = unique_strings((base.get("authors") or []) + (incoming.get("authors") or []))
    base["publication_types"] = unique_strings((base.get("publication_types") or []) + (incoming.get("publication_types") or []))
    base["source_ids"] = {**(base.get("source_ids") or {}), **{k: v for k, v in (incoming.get("source_ids") or {}).items() if v}}
    for field in (
        "doi", "journal", "year", "volume", "issue", "pages", "online_date", "created_date",
        "indexed_date", "published_date", "print_date", "availability_date", "availability_date_basis", "url",
    ):
        if not base.get(field) and incoming.get(field):
            base[field] = incoming[field]
    if len(clean_space(incoming.get("abstract"))) > len(clean_space(base.get("abstract"))):
        base["abstract"] = incoming.get("abstract")
        base["abstract_source"] = incoming.get("source")
    for field in ("full_text_links", "full_text_urls", "retrieval_queries", "retrieval_channels"):
        base[field] = unique_strings((base.get(field) or []) + (incoming.get(field) or []))
    if not base.get("open_access_pdf") and incoming.get("open_access_pdf"):
        base["open_access_pdf"] = incoming.get("open_access_pdf")
    return base


def dedup_papers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    loose: list[dict[str, Any]] = []
    for record in records:
        if not record.get("title"):
            continue
        key = _paper_key(record)
        if key in merged:
            existing = merged[key]
            title_score = ratio(normalize_title(existing.get("title")), normalize_title(record.get("title")))
            existing_authors = {normalize_title(a).split(" ")[-1] for a in existing.get("authors") or [] if normalize_title(a)}
            incoming_authors = {normalize_title(a).split(" ")[-1] for a in record.get("authors") or [] if normalize_title(a)}
            author_overlap = bool(existing_authors & incoming_authors)
            # A shared DOI/PMID is strong evidence but publisher metadata can be wrong.
            # Do not merge obviously incompatible titles without author support.
            if title_score < 58 and not author_overlap:
                conflict_key = key + "|conflict:" + sha256_text(normalize_title(record.get("title")))[:10]
                copied = dict(record)
                copied["identifier_conflict"] = {"shared_key": key, "title_score": title_score}
                copied["sources"] = unique_strings([record.get("source")])
                copied["source_records"] = [record]
                merged[conflict_key] = copied
            else:
                merged[key] = _merge_paper(existing, record)
        else:
            copied = dict(record)
            copied["sources"] = unique_strings([record.get("source")])
            copied["source_records"] = [record]
            merged[key] = copied
    # A second title-author pass catches records with missing identifiers.
    for item in merged.values():
        matched = False
        for existing in loose:
            title_score = token_set_ratio(_title_signature(item.get("title")), _title_signature(existing.get("title")))
            if title_score >= 92:
                author_a = normalize_title((item.get("authors") or [""])[0])
                author_b = normalize_title((existing.get("authors") or [""])[0])
                year_a = str(item.get("year") or "")
                year_b = str(existing.get("year") or "")
                author_ok = not author_a or not author_b or ratio(author_a, author_b) >= 68
                year_ok = not year_a or not year_b or abs(int(year_a[:4]) - int(year_b[:4])) <= 1
                if author_ok and year_ok:
                    existing.setdefault("version_relations", []).append({
                        "source": item.get("source"),
                        "url": item.get("url"),
                        "title_similarity": title_score,
                    })
                    _merge_paper(existing, item)
                    matched = True
                    break
        if not matched:
            loose.append(item)
    for index, item in enumerate(loose, 1):
        item["paper_id"] = "paper-" + sha256_text(_paper_key(item))[:16]
        item["rank"] = index
    return loose


def _is_news_aggregator_url(value: str | None) -> bool:
    url = clean_space(value).lower()
    return any(host in url for host in (
        "news.google.", "google.com/rss", "googleusercontent.com",
        "bing.com/news", "msn.com/",
    ))


def dedup_news(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_url: dict[str, dict[str, Any]] = {}
    for record in records:
        title = _title_signature(record.get("title"))
        if not title:
            continue
        canonical = _canonical_url(record.get("resolved_url") or record.get("url"))
        duplicate = by_url.get(canonical) if canonical else None
        if duplicate is None:
            for existing in out:
                score = token_set_ratio(title, _title_signature(existing.get("title")))
                date_a = clean_space(record.get("published_date"))[:10]
                date_b = clean_space(existing.get("published_date"))[:10]
                date_ok = not date_a or not date_b or date_a == date_b
                if score >= 87 and date_ok:
                    duplicate = existing
                    break
        if duplicate:
            duplicate.setdefault("duplicate_sources", []).append({
                "source": record.get("source"),
                "url": record.get("url"),
                "publisher": record.get("publisher"),
            })
            duplicate["retrieval_queries"] = unique_strings((duplicate.get("retrieval_queries") or []) + (record.get("retrieval_queries") or []))
            duplicate["retrieval_concepts"] = unique_strings((duplicate.get("retrieval_concepts") or []) + (record.get("retrieval_concepts") or []))
            duplicate["candidate_urls"] = unique_strings(
                (duplicate.get("candidate_urls") or [])
                + [duplicate.get("url"), duplicate.get("resolved_url")]
                + (record.get("candidate_urls") or [])
                + [record.get("url"), record.get("resolved_url"), record.get("canonical_url")]
            )
            incoming_url = clean_space(record.get("resolved_url") or record.get("url"))
            current_url = clean_space(duplicate.get("resolved_url") or duplicate.get("url"))
            if incoming_url and _is_news_aggregator_url(current_url) and not _is_news_aggregator_url(incoming_url):
                duplicate["url"] = incoming_url
                duplicate["resolved_url"] = incoming_url
                duplicate["canonical_url"] = _canonical_url(incoming_url)
            if len(clean_space(record.get("excerpt"))) > len(clean_space(duplicate.get("excerpt"))):
                duplicate["excerpt"] = record.get("excerpt")
                duplicate["rss_summary_html"] = record.get("rss_summary_html") or duplicate.get("rss_summary_html")
            if not duplicate.get("publisher") and record.get("publisher"):
                duplicate["publisher"] = record.get("publisher")
            continue
        copied = dict(record)
        copied["canonical_url"] = canonical
        copied["candidate_urls"] = unique_strings(
            (record.get("candidate_urls") or [])
            + [record.get("url"), record.get("resolved_url"), record.get("canonical_url")]
        )
        copied["news_id"] = "news-" + sha256_text(title + "|" + clean_space(record.get("published_date")))[:16]
        copied["duplicate_sources"] = []
        out.append(copied)
        if canonical:
            by_url[canonical] = copied
    return out


def attach_news_to_papers(news: list[dict[str, Any]], papers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    remaining: list[dict[str, Any]] = []
    for article in news:
        text = clean_space(article.get("title")) + " " + clean_space(article.get("excerpt"))
        article_doi = extract_doi(text)
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for paper in papers:
            if article_doi and paper.get("doi") and article_doi == paper.get("doi"):
                best = (1.0, paper)
                break
            score = ratio(normalize_title(article.get("title")), normalize_title(paper.get("title"))) / 100
            if score > best[0]:
                best = (score, paper)
        event_terms = re.search(r"\b(outbreak|case|death|quarantine|alert|confirmed|suspected|response)\b", text, flags=re.I)
        if best[1] is not None and best[0] >= 0.78 and not event_terms:
            best[1].setdefault("media_mentions", []).append(article)
            article["related_paper_id"] = best[1].get("paper_id")
            continue
        remaining.append(article)
    return remaining, papers


def llm_review_ambiguous_duplicates(
    items: list[dict[str, Any]], llm: LLMRouter, prompt_text: str
) -> list[dict[str, Any]]:
    if not llm.available or len(items) < 2:
        return items
    candidates: list[list[int]] = []
    for i, item in enumerate(items):
        group = [i]
        for j in range(i + 1, len(items)):
            score = ratio(normalize_title(item.get("title")), normalize_title(items[j].get("title")))
            if 72 <= score < 94:
                group.append(j)
        if len(group) > 1:
            candidates.append(group[:6])
    if not candidates:
        return items
    remove: set[int] = set()
    for group in candidates[:8]:
        payload = [{
            "index": idx,
            "title": items[idx].get("title"),
            "doi": items[idx].get("doi"),
            "authors": (items[idx].get("authors") or [])[:4],
            "journal_or_publisher": items[idx].get("journal") or items[idx].get("publisher"),
            "date": items[idx].get("availability_date") or items[idx].get("published_date"),
            "abstract_or_excerpt": clean_space(items[idx].get("abstract") or items[idx].get("excerpt"))[:1200],
        } for idx in group]
        try:
            result = llm.json_task(system=prompt_text, prompt=json.dumps(payload, ensure_ascii=False), provider_order=getattr(llm, "provider_order", lambda purpose: None)("relevance"), max_models_per_provider=1)
        except LLMError:
            continue
        for cluster in result.data.get("duplicate_clusters", []) if isinstance(result.data, dict) else []:
            indexes = [int(x) for x in cluster.get("indexes", []) if isinstance(x, int) or str(x).isdigit()]
            if len(indexes) < 2:
                continue
            keep = int(cluster.get("keep_index", indexes[0]))
            for idx in indexes:
                if idx != keep:
                    remove.add(idx)
    return [item for idx, item in enumerate(items) if idx not in remove]
