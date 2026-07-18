from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from .dates import assess_publication_date
from .http import HttpClient
from .source_status import SourceAudit
from .utils import clean_space, safe_date_string, strip_tags, unique_strings

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _xml_text(node: ET.Element | None) -> str:
    return clean_space("".join(node.itertext())) if node is not None else ""


def _date_from_parts(year: Any, month: Any = None, day: Any = None) -> str | None:
    """Preserve source precision instead of silently coercing missing parts to Jan 1."""
    months = {name.lower(): i for i, name in enumerate(
        ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )}
    try:
        y = int(str(year))
        if month in (None, ""):
            return f"{y:04d}"
        raw = str(month)
        m = months.get(raw[:3].lower(), int(raw) if raw.isdigit() else 1)
        if day in (None, ""):
            date(y, m, 1)  # validate year/month
            return f"{y:04d}-{m:02d}"
        d = int(str(day))
        return date(y, m, d).isoformat()
    except (TypeError, ValueError):
        return None


def _pubmed_term(query: str, start: date, end: date) -> str:
    # Publication fields only. CRDT/EDAT describe PubMed record processing
    # and must never be allowed to make an old paper look newly published.
    date_exprs = [
        f'("{start:%Y/%m/%d}"[EPDAT] : "{end:%Y/%m/%d}"[EPDAT])',
        f'("{start:%Y/%m/%d}"[PPDAT] : "{end:%Y/%m/%d}"[PPDAT])',
        f'("{start:%Y/%m/%d}"[PDAT] : "{end:%Y/%m/%d}"[PDAT])',
    ]
    return f"({query}) AND ({' OR '.join(date_exprs)})"


def _pubmed_request(http: HttpClient, params: dict[str, Any]) -> dict[str, Any]:
    # NCBI recommends POST for queries longer than several hundred characters.
    term = str(params.get("term") or "")
    if len(term) > 450:
        return http.request("POST", f"{EUTILS}/esearch.fcgi", data=params).json()
    return http.get_json(f"{EUTILS}/esearch.fcgi", params=params)


def _pubmed_search(
    http: HttpClient,
    query: str,
    start: date,
    end: date,
    api_key: str,
    limit: int,
) -> tuple[list[str], int, int]:
    term = _pubmed_term(query, start, end)
    base: dict[str, Any] = {
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "sort": "pub date",
        "usehistory": "y",
        "retmax": 0,
    }
    if api_key:
        base["api_key"] = api_key
    first = _pubmed_request(http, base)
    result = first.get("esearchresult", {})
    count = int(result.get("count") or 0)
    ids: list[str] = []
    pages = 0
    target = min(count, max(0, limit))
    for retstart in range(0, target, 100):
        params = dict(base)
        params.update({"retstart": retstart, "retmax": min(100, target - retstart)})
        payload = _pubmed_request(http, params)
        ids.extend(payload.get("esearchresult", {}).get("idlist", []))
        pages += 1
    return unique_strings(ids), count, pages


def _pubmed_fetch(http: HttpClient, pmids: list[str], api_key: str) -> list[dict[str, Any]]:
    if not pmids:
        return []
    params: dict[str, Any] = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    if api_key:
        params["api_key"] = api_key
    raw = http.request("POST", f"{EUTILS}/efetch.fcgi", data=params).text
    root = ET.fromstring(raw)
    output: list[dict[str, Any]] = []
    for article_node in root.findall(".//PubmedArticle"):
        citation = article_node.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        if article is None:
            continue
        journal = article.find("Journal")
        issue = journal.find("JournalIssue") if journal is not None else None
        pub_date = issue.find("PubDate") if issue is not None else None
        abstract_parts: list[str] = []
        for part in article.findall("Abstract/AbstractText"):
            label = clean_space(part.attrib.get("Label"))
            text = _xml_text(part)
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        authors: list[str] = []
        for author in article.findall("AuthorList/Author"):
            collective = _xml_text(author.find("CollectiveName"))
            name = collective or clean_space(f"{_xml_text(author.find('ForeName'))} {_xml_text(author.find('LastName'))}")
            if name:
                authors.append(name)
        ids: dict[str, str] = {}
        pubmed_data = article_node.find("PubmedData")
        if pubmed_data is not None:
            for node in pubmed_data.findall("ArticleIdList/ArticleId"):
                ids[node.attrib.get("IdType", "")] = _xml_text(node)
        article_date = article.find("ArticleDate")
        online = _date_from_parts(
            _xml_text(article_date.find("Year")) if article_date is not None else None,
            _xml_text(article_date.find("Month")) if article_date is not None else None,
            _xml_text(article_date.find("Day")) if article_date is not None else None,
        )
        print_date = _date_from_parts(
            _xml_text(pub_date.find("Year")) if pub_date is not None else None,
            _xml_text(pub_date.find("Month")) if pub_date is not None else None,
            _xml_text(pub_date.find("Day")) if pub_date is not None else None,
        )
        date_created = citation.find("DateCreated") if citation is not None else None
        created = _date_from_parts(
            _xml_text(date_created.find("Year")) if date_created is not None else None,
            _xml_text(date_created.find("Month")) if date_created is not None else None,
            _xml_text(date_created.find("Day")) if date_created is not None else None,
        )
        pmid = ids.get("pubmed") or _xml_text(citation.find("PMID") if citation is not None else None)
        output.append({
            "source": "PubMed",
            "source_ids": {"pmid": pmid, "pmcid": ids.get("pmc")},
            "doi": clean_space(ids.get("doi")).lower() or None,
            "title": _xml_text(article.find("ArticleTitle")),
            "abstract": clean_space(" ".join(abstract_parts)),
            "authors": authors,
            "journal": _xml_text(journal.find("Title") if journal is not None else None),
            "year": int((online or print_date)[:4]) if (online or print_date) else None,
            "volume": _xml_text(issue.find("Volume") if issue is not None else None),
            "issue": _xml_text(issue.find("Issue") if issue is not None else None),
            "pages": _xml_text(article.find("Pagination/MedlinePgn")),
            "online_date": online,
            "created_date": created,
            "published_date": print_date,
            "print_date": print_date,
            "publication_types": [_xml_text(n) for n in article.findall("PublicationTypeList/PublicationType")],
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        })
    return output


def search_pubmed(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    api_key: str,
    per_query: int = 180,
    max_total: int = 2000,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    """Search every compiled PubMed query and fetch the union of PMIDs.

    ESearch uses POST for long expressions and paginates with retstart/retmax.
    ``max_total`` is a safety budget across all query modes, not a random
    sample: IDs are collected in PubMed date order and de-duplicated.
    """
    ids: list[str] = []
    query_by_id: dict[str, list[str]] = {}
    for query in unique_strings(queries):
        try:
            found, total, pages = _pubmed_search(http, query, start, end, api_key, per_query)
            ids.extend(found)
            for pmid in found:
                query_by_id.setdefault(pmid, []).append(query)
            if audit:
                audit.add(
                    source="PubMed",
                    query=query,
                    mode="provider_boolean",
                    status="success",
                    records=len(found),
                    pages=pages,
                    endpoint=f"{EUTILS}/esearch.fcgi",
                    details={"total_matches": total, "api_key_configured": bool(api_key)},
                )
        except Exception as exc:
            if audit:
                audit.add(source="PubMed", query=query, mode="provider_boolean", status="failed", endpoint=f"{EUTILS}/esearch.fcgi", error=exc)
    ids = unique_strings(ids)[:max(0, max_total)]
    works: list[dict[str, Any]] = []
    for index in range(0, len(ids), 100):
        batch = ids[index : index + 100]
        try:
            rows = _pubmed_fetch(http, batch, api_key)
            for row in rows:
                pmid = clean_space((row.get("source_ids") or {}).get("pmid"))
                row["retrieval_queries"] = query_by_id.get(pmid, [])
                row["retrieval_channels"] = ["pubmed_esearch"]
            works.extend(rows)
        except Exception as exc:
            if audit:
                audit.add(source="PubMed EFetch", status="failed", records=0, endpoint=f"{EUTILS}/efetch.fcgi", error=exc, details={"batch_size": len(batch)})
    return works


def probe_pubmed_anchor_counts(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    api_key: str = "",
    audit: SourceAudit | None = None,
) -> dict[str, int]:
    """Count-only 90-day diagnostic; results never enter the daily report."""
    counts: dict[str, int] = {}
    endpoint = f"{EUTILS}/esearch.fcgi"
    for query in unique_strings(queries):
        try:
            _, total, _ = _pubmed_search(http, query, start, end, api_key, 0)
            counts[query] = total
            if audit:
                audit.add(
                    source="PubMed 90-day anchor probe", query=query, mode="diagnostic_count_only",
                    status="success", records=total, endpoint=endpoint,
                    details={"diagnostic_only": True, "window_start": start.isoformat(), "window_end": end.isoformat()},
                )
        except Exception as exc:
            if audit:
                audit.add(
                    source="PubMed 90-day anchor probe", query=query, mode="diagnostic_count_only",
                    status="failed", endpoint=endpoint, error=exc,
                    details={"diagnostic_only": True, "window_start": start.isoformat(), "window_end": end.isoformat()},
                )
    return counts


def probe_europe_pmc_anchor_counts(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    audit: SourceAudit | None = None,
) -> dict[str, int]:
    """Count-only Europe PMC diagnostic used only when the 7-day core search is empty."""
    counts: dict[str, int] = {}
    endpoint = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for query in unique_strings(queries):
        epmc_query = f"({query}) AND FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]"
        try:
            payload = http.get_json(endpoint, params={
                "query": epmc_query, "format": "json", "resultType": "lite", "pageSize": 1,
            })
            total = int(payload.get("hitCount") or 0)
            counts[query] = total
            if audit:
                audit.add(
                    source="Europe PMC 90-day anchor probe", query=query, mode="diagnostic_count_only",
                    status="success", records=total, endpoint=endpoint,
                    details={"diagnostic_only": True, "window_start": start.isoformat(), "window_end": end.isoformat()},
                )
        except Exception as exc:
            if audit:
                audit.add(
                    source="Europe PMC 90-day anchor probe", query=query, mode="diagnostic_count_only",
                    status="failed", endpoint=endpoint, error=exc,
                    details={"diagnostic_only": True, "window_start": start.isoformat(), "window_end": end.isoformat()},
                )
    return counts

def search_europe_pmc(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    per_query: int = 150,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    endpoint = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for query in unique_strings(queries):
        epmc_query = f"({query}) AND FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}]"
        cursor = "*"
        collected = 0
        pages = 0
        failed: Exception | None = None
        while collected < per_query:
            try:
                page_size = min(1000, per_query - collected)
                payload = http.get_json(endpoint, params={
                    "query": epmc_query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": page_size,
                    "cursorMark": cursor,
                })
                rows = payload.get("resultList", {}).get("result", []) or []
                pages += 1
                for item in rows:
                    authors = [a.get("fullName") for a in item.get("authorList", {}).get("author", []) if a.get("fullName")]
                    full_text_urls = [x.get("url") for x in item.get("fullTextUrlList", {}).get("fullTextUrl", []) or [] if x.get("url")]
                    output.append({
                        "source": "Europe PMC",
                        "source_ids": {"pmid": item.get("pmid"), "pmcid": item.get("pmcid"), "epmc": item.get("id")},
                        "doi": clean_space(item.get("doi")).lower() or None,
                        "title": strip_tags(item.get("title")),
                        "abstract": strip_tags(item.get("abstractText")),
                        "authors": authors or unique_strings(str(item.get("authorString", "")).split(",")),
                        "journal": clean_space(item.get("journalTitle")),
                        "year": item.get("pubYear"),
                        "volume": clean_space(item.get("journalVolume")),
                        "issue": clean_space(item.get("issue")),
                        "pages": clean_space(item.get("pageInfo")),
                        "online_date": safe_date_string(item.get("firstPublicationDate") or item.get("electronicPublicationDate")),
                        "first_publication_date": safe_date_string(item.get("firstPublicationDate")),
                        "created_date": safe_date_string(item.get("creationDate")),
                        "published_date": safe_date_string((item.get("journalInfo") or {}).get("printPublicationDate") if isinstance(item.get("journalInfo"), dict) else None),
                        "publication_types": [item.get("pubType")] if item.get("pubType") else [],
                        "open_access": str(item.get("isOpenAccess", "")).upper() == "Y",
                        "full_text_urls": full_text_urls,
                        "url": f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id')}",
                        "retrieval_queries": [query],
                    })
                collected += len(rows)
                next_cursor = payload.get("nextCursorMark")
                if not rows or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            except Exception as exc:
                failed = exc
                break
        if audit:
            audit.add(source="Europe PMC", query=query, status="failed" if failed else "success", records=collected, pages=pages, endpoint=endpoint, error=failed)
    return output


def _crossref_date(item: dict[str, Any], key: str) -> str | None:
    parts = (((item.get(key) or {}).get("date-parts") or [[None]])[0])
    return _date_from_parts(*parts) if parts and parts[0] else None


def search_crossref(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    mailto: str,
    per_query: int = 45,
    include_indexed: bool = False,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    """Search Crossref with provider-native simple identity queries.

    Crossref does not receive PubMed Boolean syntax. Each identity is queried
    independently through publication and optionally indexed date channels.
    The indexed channel recovers newly deposited or corrected metadata without
    tripling request volume with a separate created-date channel.
    """
    output: list[dict[str, Any]] = []
    endpoint = "https://api.crossref.org/works"
    channels = [
        ("published", f"from-pub-date:{start.isoformat()},until-pub-date:{end.isoformat()}"),
    ]
    if include_indexed:
        channels.append(("indexed", f"from-index-date:{start.isoformat()},until-index-date:{end.isoformat()}"))

    for term in unique_strings(queries):
        for channel, filter_value in channels:
            params: dict[str, Any] = {
                "query.bibliographic": term,
                "filter": filter_value,
                "rows": min(1000, max(1, per_query)),
            }
            if mailto:
                params["mailto"] = mailto
            try:
                payload = http.get_json(endpoint, params=params)
                rows = payload.get("message", {}).get("items", []) or []
                for item in rows:
                    authors = [clean_space(f"{a.get('given', '')} {a.get('family', '')}") for a in item.get("author", []) if a.get("family") or a.get("given")]
                    output.append({
                        "source": "Crossref",
                        "source_ids": {},
                        "doi": clean_space(item.get("DOI")).lower() or None,
                        "title": strip_tags(" ".join(item.get("title") or [])),
                        "abstract": strip_tags(item.get("abstract")),
                        "authors": authors,
                        "journal": clean_space(" ".join(item.get("container-title") or [])),
                        "online_date": _crossref_date(item, "published-online"),
                        "created_date": safe_date_string((item.get("created") or {}).get("date-time")),
                        "indexed_date": safe_date_string((item.get("indexed") or {}).get("date-time")),
                        "published_date": _crossref_date(item, "published") or _crossref_date(item, "issued"),
                        "print_date": _crossref_date(item, "published-print"),
                        "publication_types": [item.get("type")] if item.get("type") else [],
                        "volume": clean_space(item.get("volume")),
                        "issue": clean_space(item.get("issue")),
                        "pages": clean_space(item.get("page")),
                        "citation_count": item.get("is-referenced-by-count") or 0,
                        "full_text_links": [x.get("URL") for x in item.get("link", []) if x.get("URL")],
                        "url": item.get("URL") or (f"https://doi.org/{item.get('DOI')}" if item.get("DOI") else ""),
                        "retrieval_queries": [term],
                        "retrieval_channels": [f"crossref_{channel}"],
                    })
                if audit:
                    audit.add(source="Crossref", query=term, mode=channel, status="success", records=len(rows), pages=1, endpoint=endpoint)
            except Exception as exc:
                if audit:
                    audit.add(source="Crossref", query=term, mode=channel, status="failed", endpoint=endpoint, error=exc)
    return output

def search_semantic_scholar(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    api_key: str = "",
    per_query: int = 80,
    anonymous_query_limit: int = 0,
    anonymous_delay_ms: int = 500,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    """Search Semantic Scholar through the bulk endpoint.

    The adapter accepts provider-native text/Boolean queries only. When no API
    key is available it still runs a bounded anonymous subset so the source can
    contribute without dominating runtime or repeatedly hitting rate limits.
    """
    output: list[dict[str, Any]] = []
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"
    headers = {"x-api-key": api_key} if api_key else {}
    fields = "paperId,title,abstract,authors,venue,year,publicationDate,publicationTypes,externalIds,url,openAccessPdf,citationCount"
    effective_queries = unique_strings(queries)
    effective_per_query = per_query
    if not api_key:
        if anonymous_query_limit > 0:
            effective_queries = effective_queries[:anonymous_query_limit]
        effective_per_query = min(per_query, 40)
        if audit:
            audit.add(
                source="Semantic Scholar",
                mode="anonymous_mode",
                status="success",
                records=0,
                endpoint=endpoint,
                details={
                    "message": "API key not configured; all compiled queries are attempted unless an explicit limit is configured",
                    "query_limit": anonymous_query_limit,
                    "queries_planned": len(effective_queries),
                    "delay_ms": max(0, anonymous_delay_ms),
                },
            )

    for query_index, query in enumerate(effective_queries):
        if not api_key and query_index and anonymous_delay_ms > 0:
            time.sleep(anonymous_delay_ms / 1000.0)
        token: str | None = None
        collected = 0
        pages = 0
        failed: Exception | None = None
        while collected < effective_per_query:
            params: dict[str, Any] = {
                "query": query,
                "fields": fields,
                "publicationDateOrYear": f"{start.isoformat()}:{end.isoformat()}",
                "limit": min(100, effective_per_query - collected),
                "sort": "publicationDate:desc",
            }
            if token:
                params["token"] = token
            try:
                payload = http.get_json(endpoint, params=params, headers=headers)
                rows = payload.get("data", []) or []
                pages += 1
                for item in rows:
                    external = item.get("externalIds") or {}
                    output.append({
                        "source": "Semantic Scholar",
                        "source_ids": {"semantic_scholar": item.get("paperId"), "pmid": external.get("PubMed"), "arxiv": external.get("ArXiv")},
                        "doi": clean_space(external.get("DOI")).lower() or None,
                        "title": clean_space(item.get("title")),
                        "abstract": clean_space(item.get("abstract")),
                        "authors": [a.get("name") for a in item.get("authors", []) if a.get("name")],
                        "journal": clean_space(item.get("venue")),
                        "year": item.get("year"),
                        "online_date": safe_date_string(item.get("publicationDate")),
                        "published_date": safe_date_string(item.get("publicationDate")),
                        "publication_types": item.get("publicationTypes") or [],
                        "citation_count": item.get("citationCount") or 0,
                        "open_access_pdf": (item.get("openAccessPdf") or {}).get("url"),
                        "url": item.get("url"),
                        "retrieval_queries": [query],
                        "retrieval_channels": ["semantic_bulk_authenticated" if api_key else "semantic_bulk_anonymous"],
                    })
                collected += len(rows)
                token = payload.get("token")
                if not rows or not token:
                    break
            except Exception as exc:
                failed = exc
                break
        if audit:
            audit.add(
                source="Semantic Scholar",
                query=query,
                mode="bulk_authenticated" if api_key else "bulk_anonymous",
                status="failed" if failed else "success",
                records=collected,
                pages=pages,
                endpoint=endpoint,
                error=failed,
                details={"authenticated": bool(api_key)},
            )
    return output

def _openalex_abstract(inverted: Any) -> str:
    if not isinstance(inverted, dict):
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted.items():
        for pos in positions or []:
            if isinstance(pos, int):
                pairs.append((pos, str(word)))
    return clean_space(" ".join(word for _, word in sorted(pairs)))


def search_openalex(
    http: HttpClient,
    exact_queries: list[str],
    normal_queries: list[str],
    start: date,
    end: date,
    api_key: str = "",
    per_query: int = 100,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    """Run OpenAlex exact and normal full-text search channels.

    Exact search protects precision for canonical identities. Normal search
    adds stemming, punctuation and word-order tolerance. Both channels are
    date-filtered, cursor-paginated and subjected to the same local relevance
    gate after retrieval.
    """
    endpoint = "https://api.openalex.org/works"
    if not api_key:
        if audit:
            audit.add(source="OpenAlex", status="skipped", endpoint=endpoint, error="OPENALEX_API_KEY is not configured")
        return []
    output: list[dict[str, Any]] = []
    channels = [
        ("exact", "search.exact", unique_strings(exact_queries)),
        ("normal", "search", unique_strings(normal_queries)),
    ]
    for channel, parameter, queries in channels:
        for query in queries:
            cursor = "*"
            collected = 0
            pages = 0
            failed: Exception | None = None
            while collected < per_query:
                try:
                    params = {
                        "api_key": api_key,
                        parameter: query,
                        "filter": f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()}",
                        "per_page": min(100, per_query - collected),
                        "cursor": cursor,
                    }
                    payload = http.get_json(endpoint, params=params)
                    rows = payload.get("results", []) or []
                    pages += 1
                    for item in rows:
                        doi = clean_space(item.get("doi")).removeprefix("https://doi.org/").lower() or None
                        output.append({
                            "source": "OpenAlex",
                            "source_ids": {"openalex": item.get("id")},
                            "doi": doi,
                            "title": clean_space(item.get("display_name") or item.get("title")),
                            "abstract": _openalex_abstract(item.get("abstract_inverted_index")),
                            "authors": [clean_space((a.get("author") or {}).get("display_name")) for a in item.get("authorships", []) if (a.get("author") or {}).get("display_name")],
                            "journal": clean_space(((item.get("primary_location") or {}).get("source") or {}).get("display_name")),
                            "year": item.get("publication_year"),
                            "online_date": safe_date_string(item.get("publication_date")),
                            "published_date": safe_date_string(item.get("publication_date")),
                            "publication_types": [item.get("type")] if item.get("type") else [],
                            "citation_count": item.get("cited_by_count") or 0,
                            "open_access": bool((item.get("open_access") or {}).get("is_oa")),
                            "open_access_pdf": (item.get("best_oa_location") or {}).get("pdf_url"),
                            "url": (item.get("primary_location") or {}).get("landing_page_url") or item.get("id"),
                            "retrieval_queries": [query],
                            "retrieval_channels": [f"openalex_{channel}"],
                        })
                    collected += len(rows)
                    next_cursor = (payload.get("meta") or {}).get("next_cursor")
                    if not rows or not next_cursor or next_cursor == cursor:
                        break
                    cursor = next_cursor
                except Exception as exc:
                    failed = exc
                    break
            if audit:
                audit.add(source="OpenAlex", query=query, mode=channel, status="failed" if failed else "success", records=collected, pages=pages, endpoint=endpoint, error=failed)
    return output

def filter_publication_window(
    records: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    future_days: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hard-gate scholarly records by real publication dates.

    created_date/indexed_date remain available for provenance but never count as
    publication evidence. Rejected records are returned for audit/quarantine.
    """
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for original in records:
        record = dict(original)
        decision = assess_publication_date(record, start, end, future_days=future_days)
        record["publication_date_gate"] = decision.to_dict()
        if not decision.accepted:
            rejected.append(record)
            continue
        record["availability_date"] = decision.canonical_date
        record["availability_date_basis"] = decision.canonical_basis
        record["publication_date_status"] = decision.status
        accepted.append(record)
    return accepted, rejected


def filter_window(
    records: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    future_days: int = 0,
) -> list[dict[str, Any]]:
    """Backward-compatible wrapper returning only accepted records."""
    accepted, _ = filter_publication_window(records, start, end, future_days=future_days)
    return accepted


def search_biorxiv_medrxiv(
    http: HttpClient,
    start: date,
    end: date,
    max_records_per_server: int = 1200,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for server in ("biorxiv", "medrxiv"):
        cursor = 0
        collected = 0
        pages = 0
        failed: Exception | None = None
        endpoint = f"https://api.biorxiv.org/details/{server}/{start.isoformat()}/{end.isoformat()}"
        while collected < max_records_per_server:
            try:
                payload = http.get_json(f"{endpoint}/{cursor}")
                rows = payload.get("collection", []) or []
                pages += 1
                for item in rows:
                    output.append({
                        "source": "bioRxiv" if server == "biorxiv" else "medRxiv",
                        "source_ids": {server: item.get("doi")},
                        "doi": clean_space(item.get("doi")).lower() or None,
                        "title": clean_space(item.get("title")),
                        "abstract": clean_space(item.get("abstract")),
                        "authors": unique_strings(str(item.get("authors", "")).split(";")),
                        "journal": "bioRxiv" if server == "biorxiv" else "medRxiv",
                        "online_date": safe_date_string(item.get("date")),
                        "published_date": safe_date_string(item.get("date")),
                        "publication_types": ["preprint"],
                        "url": f"https://doi.org/{item.get('doi')}" if item.get("doi") else "",
                    })
                collected += len(rows)
                cursor += len(rows)
                total = int(((payload.get("messages") or [{}])[0]).get("total") or collected)
                if not rows or collected >= total:
                    break
            except Exception as exc:
                failed = exc
                break
        if audit:
            audit.add(source="bioRxiv" if server == "biorxiv" else "medRxiv", status="failed" if failed else "success", records=collected, pages=pages, endpoint=endpoint, error=failed)
    return output
