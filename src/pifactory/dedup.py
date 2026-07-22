from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz.fuzz import ratio, token_set_ratio

from .llm import LLMError, LLMRouter
from .utils import clean_scholarly_abstract, clean_space, extract_doi, normalize_title, sha256_text, unique_strings
from .dates import parse_date_span


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



def _author_key(value: Any) -> str:
    text = clean_space(value)
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'\-]*", text)
    if not tokens:
        return ""
    # Database abbreviations commonly use ``Surname AB`` while full records use
    # ``Alice B Surname``.  Detect a terminal initials token before choosing the
    # surname so both forms collapse to the same conservative key.
    terminal = re.sub(r"[^A-Za-z]", "", tokens[-1])
    if len(tokens) >= 2 and 1 <= len(terminal) <= 3 and tokens[-1].upper() == tokens[-1]:
        surname = tokens[-2].casefold()
        first_initial = terminal[0].casefold()
    else:
        surname = tokens[-1].casefold()
        first_initial = tokens[0][0].casefold() if tokens[0] else ""
    return f"{surname}|{first_initial}"


def _dedup_authors(values: list[Any]) -> list[str]:
    groups: dict[str, str] = {}
    order: list[str] = []
    for raw in values:
        value = clean_space(raw)
        if not value:
            continue
        key = _author_key(value) or value.casefold()
        if key not in groups:
            groups[key] = value
            order.append(key)
        elif len(value) > len(groups[key]):
            groups[key] = value
    return [groups[key] for key in order]

def _earliest_publication_value(left: Any, right: Any) -> Any:
    a = parse_date_span(left)
    b = parse_date_span(right)
    if not a:
        return right
    if not b:
        return left
    return left if a.start <= b.start else right


def _merge_paper(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    base.setdefault("sources", [])
    base["sources"] = unique_strings(base["sources"] + [base.get("source"), incoming.get("source")])
    base.setdefault("source_records", []).append(incoming)
    base["authors"] = _dedup_authors((base.get("authors") or []) + (incoming.get("authors") or []))
    base["publication_types"] = unique_strings((base.get("publication_types") or []) + (incoming.get("publication_types") or []))
    base["source_ids"] = {**(base.get("source_ids") or {}), **{k: v for k, v in (incoming.get("source_ids") or {}).items() if v}}
    for field in ("doi", "journal", "year", "volume", "issue", "pages", "url"):
        if not base.get(field) and incoming.get(field):
            base[field] = incoming[field]
    for field in ("first_publication_date", "online_date", "published_date", "print_date"):
        if incoming.get(field):
            base[field] = _earliest_publication_value(base.get(field), incoming.get(field))
    for field in ("created_date", "indexed_date"):
        if not base.get(field) and incoming.get(field):
            base[field] = incoming[field]
    base.setdefault("date_source_records", []).append({
        "source": incoming.get("source"),
        "first_publication_date": incoming.get("first_publication_date"),
        "online_date": incoming.get("online_date"),
        "published_date": incoming.get("published_date"),
        "print_date": incoming.get("print_date"),
        "created_date": incoming.get("created_date"),
        "indexed_date": incoming.get("indexed_date"),
    })
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
    for source_record in records:
        record = dict(source_record)
        if record.get("abstract"):
            record["abstract"] = clean_scholarly_abstract(record.get("abstract"))
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
        item["authors"] = _dedup_authors(item.get("authors") or [])
        item["paper_id"] = "paper-" + sha256_text(_paper_key(item))[:16]
        item["rank"] = index
    return loose


def _is_news_aggregator_url(value: str | None) -> bool:
    url = clean_space(value).lower()
    return any(host in url for host in (
        "news.google.", "google.com/rss", "googleusercontent.com",
        "bing.com/news", "msn.com/",
    ))


def _news_duplicate_evidence(
    incoming: dict[str, Any], existing: dict[str, Any], title_score: float
) -> tuple[bool, dict[str, Any]]:
    """Require document-level corroboration before collapsing two news records.

    Similar headlines about the same outbreak are not necessarily duplicate
    documents.  Fuzzy merging therefore requires a near-identical title plus a
    matching date and either publisher or excerpt evidence.  Exact canonical URL
    identity is handled separately by ``dedup_news``.
    """
    date_a = clean_space(incoming.get("published_date"))[:10]
    date_b = clean_space(existing.get("published_date"))[:10]
    same_date = bool(date_a and date_b and date_a == date_b)
    publisher_a = normalize_title(incoming.get("publisher") or incoming.get("source"))
    publisher_b = normalize_title(existing.get("publisher") or existing.get("source"))
    same_publisher = bool(publisher_a and publisher_b and publisher_a == publisher_b)
    excerpt_a = _title_signature(incoming.get("excerpt"))
    excerpt_b = _title_signature(existing.get("excerpt"))
    excerpt_score = token_set_ratio(excerpt_a, excerpt_b) if excerpt_a and excerpt_b else 0
    exact_title = _title_signature(incoming.get("title")) == _title_signature(existing.get("title"))

    aggregator_pair = bool(
        _is_news_aggregator_url(incoming.get("resolved_url") or incoming.get("url"))
        != _is_news_aggregator_url(existing.get("resolved_url") or existing.get("url"))
    )
    supported = bool(
        (title_score >= 98 and same_date and (same_publisher or excerpt_score >= 88))
        or (exact_title and same_date and (same_publisher or excerpt_score >= 82 or aggregator_pair))
        or (title_score >= 99 and same_publisher and excerpt_score >= 92)
    )
    return supported, {
        "title_similarity": title_score,
        "same_date": same_date,
        "same_publisher": same_publisher,
        "excerpt_similarity": excerpt_score,
        "exact_title": exact_title,
        "aggregator_direct_pair": aggregator_pair,
    }


def dedup_news(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_url: dict[str, dict[str, Any]] = {}
    for source_record in records:
        record = dict(source_record)
        title = _title_signature(record.get("title"))
        if not title:
            continue
        excerpt = clean_space(record.get("excerpt"))
        if excerpt:
            excerpt_signature = _title_signature(excerpt)
            duplicate_score = token_set_ratio(title, excerpt_signature) if excerpt_signature else 0
            if excerpt_signature == title or duplicate_score >= 96:
                record["excerpt_original"] = excerpt
                record["excerpt"] = ""
                record["snippet_duplicate_of_title"] = True
                record["snippet_title_similarity"] = duplicate_score
            else:
                record["snippet_duplicate_of_title"] = False
                record["snippet_title_similarity"] = duplicate_score
        canonical = _canonical_url(record.get("resolved_url") or record.get("url"))
        duplicate = by_url.get(canonical) if canonical else None
        duplicate_evidence: dict[str, Any] = {"method": "canonical_url"} if duplicate is not None else {}
        if duplicate is None:
            for existing in out:
                score = token_set_ratio(title, _title_signature(existing.get("title")))
                supported, evidence = _news_duplicate_evidence(record, existing, score)
                if supported:
                    duplicate = existing
                    duplicate_evidence = {"method": "corroborated_fuzzy", **evidence}
                    break
        if duplicate:
            duplicate.setdefault("duplicate_sources", []).append({
                "source": record.get("source"),
                "url": record.get("url"),
                "publisher": record.get("publisher"),
                "evidence": duplicate_evidence,
            })
            duplicate["retrieval_queries"] = unique_strings((duplicate.get("retrieval_queries") or []) + (record.get("retrieval_queries") or []))
            duplicate["retrieval_concepts"] = unique_strings((duplicate.get("retrieval_concepts") or []) + (record.get("retrieval_concepts") or []))
            duplicate["retrieval_channels"] = unique_strings((duplicate.get("retrieval_channels") or []) + (record.get("retrieval_channels") or []))
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


def _ambiguous_paper_pair_evidence(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    title_score = ratio(normalize_title(left.get("title")), normalize_title(right.get("title")))
    authors_left = {_author_key(x) for x in left.get("authors") or [] if _author_key(x)}
    authors_right = {_author_key(x) for x in right.get("authors") or [] if _author_key(x)}
    author_overlap = bool(authors_left & authors_right)
    date_left = clean_space(left.get("availability_date") or left.get("published_date") or left.get("year"))[:10]
    date_right = clean_space(right.get("availability_date") or right.get("published_date") or right.get("year"))[:10]
    year_left = date_left[:4] if len(date_left) >= 4 else ""
    year_right = date_right[:4] if len(date_right) >= 4 else ""
    date_compatible = bool(
        date_left and date_right and (date_left == date_right or (year_left and year_left == year_right))
    )
    journal_left = normalize_title(left.get("journal") or left.get("publisher"))
    journal_right = normalize_title(right.get("journal") or right.get("publisher"))
    same_venue = bool(journal_left and journal_right and journal_left == journal_right)
    abstract_left = clean_space(left.get("abstract") or left.get("excerpt"))
    abstract_right = clean_space(right.get("abstract") or right.get("excerpt"))
    abstract_score = token_set_ratio(abstract_left, abstract_right) if abstract_left and abstract_right else 0
    supported = bool(
        title_score >= 92
        or (
            title_score >= 84
            and author_overlap
            and (date_compatible or same_venue or abstract_score >= 82)
        )
    )
    return {
        "supported": supported,
        "title_similarity": title_score,
        "author_overlap": author_overlap,
        "date_compatible": date_compatible,
        "same_venue": same_venue,
        "abstract_similarity": abstract_score,
    }


def llm_review_ambiguous_duplicates(
    items: list[dict[str, Any]], llm: LLMRouter, prompt_text: str, audit: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    audit = audit if audit is not None else {}
    audit.update({
        "policy_version": "v17.4-r1-identifier-first-ambiguous-llm-2",
        "input": len(items),
        "candidate_groups": 0,
        "removed": 0,
        "merged": 0,
        "groups": [],
        "rejected_model_clusters": [],
    })
    if not llm.available or len(items) < 2:
        audit["status"] = "skipped_no_llm_or_too_few"
        return items
    candidates: list[list[int]] = []
    seen_groups: set[tuple[int, ...]] = set()
    for i, item in enumerate(items):
        # DOI/PMID/PMCID records already have deterministic identity. LLM review
        # is reserved for identifierless records with genuinely ambiguous titles.
        ids_i = item.get("source_ids") or {}
        if item.get("doi") or ids_i.get("pmid") or ids_i.get("pmcid"):
            continue
        group = [i]
        for j in range(i + 1, len(items)):
            ids_j = items[j].get("source_ids") or {}
            if items[j].get("doi") or ids_j.get("pmid") or ids_j.get("pmcid"):
                continue
            score = ratio(normalize_title(item.get("title")), normalize_title(items[j].get("title")))
            if 72 <= score < 94:
                group.append(j)
        signature = tuple(group[:6])
        if len(signature) > 1 and signature not in seen_groups:
            candidates.append(list(signature))
            seen_groups.add(signature)
    audit["candidate_groups"] = len(candidates)
    if not candidates:
        audit["status"] = "no_ambiguous_identifierless_groups"
        return items

    merged_items = list(items)
    remove: set[int] = set()
    for group in candidates[:8]:
        payload = [{
            "index": idx,
            "title": merged_items[idx].get("title"),
            "doi": merged_items[idx].get("doi"),
            "authors": (merged_items[idx].get("authors") or [])[:4],
            "journal_or_publisher": merged_items[idx].get("journal") or merged_items[idx].get("publisher"),
            "date": merged_items[idx].get("availability_date") or merged_items[idx].get("published_date"),
            "abstract_or_excerpt": clean_space(merged_items[idx].get("abstract") or merged_items[idx].get("excerpt"))[:1200],
        } for idx in group]
        try:
            result = llm.json_task(
                system=prompt_text,
                prompt=json.dumps(payload, ensure_ascii=False),
                provider_order=getattr(llm, "provider_order", lambda purpose: None)("relevance"),
                max_models_per_provider=1,
            )
        except LLMError as exc:
            audit["rejected_model_clusters"].append({"group": group, "reason": "llm_error", "error": str(exc)})
            continue
        clusters = result.data.get("duplicate_clusters", []) if isinstance(result.data, dict) else []
        for cluster in clusters:
            raw_indexes = cluster.get("indexes", []) if isinstance(cluster, dict) else []
            indexes = [int(x) for x in raw_indexes if isinstance(x, int) or str(x).isdigit()]
            indexes = list(dict.fromkeys(indexes))
            try:
                keep = int(cluster.get("keep_index", indexes[0] if indexes else -1))
            except (TypeError, ValueError):
                keep = -1
            try:
                confidence = float(cluster.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            same_work = cluster.get("same_work") is True
            validation_errors: list[str] = []
            if len(indexes) < 2:
                validation_errors.append("fewer_than_two_indexes")
            if any(idx not in group for idx in indexes):
                validation_errors.append("index_outside_candidate_group")
            if keep not in indexes:
                validation_errors.append("keep_index_not_in_cluster")
            if not same_work:
                validation_errors.append("same_work_not_true")
            if confidence < 0.90:
                validation_errors.append("confidence_below_0.90")
            pair_evidence: list[dict[str, Any]] = []
            if not validation_errors:
                for idx in indexes:
                    if idx == keep:
                        continue
                    evidence = _ambiguous_paper_pair_evidence(merged_items[keep], merged_items[idx])
                    pair_evidence.append({"keep_index": keep, "candidate_index": idx, **evidence})
                    if not evidence["supported"]:
                        validation_errors.append(f"deterministic_support_missing:{idx}")
            if validation_errors:
                audit["rejected_model_clusters"].append({
                    "candidate_group": group,
                    "indexes": indexes,
                    "keep_index": keep,
                    "confidence": confidence,
                    "same_work": same_work,
                    "errors": validation_errors,
                    "reason": clean_space(cluster.get("reason")) if isinstance(cluster, dict) else "",
                    "pair_evidence": pair_evidence,
                })
                continue
            removed_here: list[int] = []
            for idx in indexes:
                if idx == keep or idx in remove:
                    continue
                merged_items[keep] = _merge_paper(merged_items[keep], merged_items[idx])
                merged_items[keep].setdefault("llm_dedup_relations", []).append({
                    "merged_index": idx,
                    "confidence": confidence,
                    "reason": clean_space(cluster.get("reason")),
                    "evidence": next((row for row in pair_evidence if row["candidate_index"] == idx), {}),
                })
                remove.add(idx)
                removed_here.append(idx)
            if removed_here:
                audit["groups"].append({
                    "indexes": indexes,
                    "keep_index": keep,
                    "removed_indexes": removed_here,
                    "confidence": confidence,
                    "same_work": same_work,
                    "reason": clean_space(cluster.get("reason")),
                    "pair_evidence": pair_evidence,
                })
    audit["removed"] = len(remove)
    audit["merged"] = len(remove)
    audit["status"] = "completed"
    return [item for idx, item in enumerate(merged_items) if idx not in remove]
