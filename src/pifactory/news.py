from __future__ import annotations

import re
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
from bs4 import BeautifulSoup

from .http import HttpClient
from .source_status import SourceAudit
from .utils import clean_space, safe_date_string, strip_tags, unique_strings


def _feed_date(entry: Any) -> str | None:
    for key in ("published", "updated"):
        value = entry.get(key)
        if not value:
            continue
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (TypeError, ValueError, OverflowError):
            parsed = safe_date_string(value)
            if parsed:
                return parsed
    return None


def _rss_candidate_urls(entry: Any) -> list[str]:
    """Collect article candidates before RSS HTML is stripped.

    Google/Bing entries may expose the publisher article in entry.links or in
    the summary HTML. Keeping every candidate lets the later content resolver
    escape aggregator URLs instead of treating the aggregator page as the
    original report.
    """
    urls: list[str] = []
    for link in entry.get("links") or []:
        if isinstance(link, dict) and link.get("href"):
            urls.append(clean_space(link.get("href")))
    summary_html = entry.get("summary") or entry.get("description") or ""
    try:
        soup = BeautifulSoup(summary_html, "lxml")
        for anchor in soup.find_all("a", href=True):
            urls.append(clean_space(anchor.get("href")))
    except Exception:
        pass
    return unique_strings(urls)


def search_google_news(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    per_query: int = 35,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    locales = [
        ("en-US", "US", "US:en", "Google News English", "en"),
        ("zh-CN", "CN", "CN:zh-Hans", "Google News Chinese", "zh"),
    ]
    for query in unique_strings(queries):
        # Do not run every English query again in the Chinese locale or every
        # Chinese query again in the English locale.  This preserves the same
        # five concepts per language while halving redundant Google RSS calls.
        query_locales = [locales[1]] if re.search(r"[\u4e00-\u9fff]", query) else [locales[0]]
        for hl, gl, ceid, source_name, language in query_locales:
            url = f"https://news.google.com/rss/search?q={quote_plus(query + ' when:7d')}&hl={hl}&gl={gl}&ceid={quote_plus(ceid)}"
            try:
                feed = feedparser.parse(http.get_text(url))
                rows = feed.entries[:per_query]
                accepted = 0
                for entry in rows:
                    published = _feed_date(entry)
                    if published and not (start.isoformat() <= published <= end.isoformat()):
                        continue
                    source = entry.get("source") or {}
                    source_url = clean_space(source.get("href") if isinstance(source, dict) else "")
                    output.append({
                        "source": source_name,
                        "title": clean_space(entry.get("title")),
                        "url": clean_space(entry.get("link")),
                        "published_date": published,
                        "excerpt": strip_tags(entry.get("summary")),
                        "rss_summary_html": entry.get("summary") or "",
                        "candidate_urls": _rss_candidate_urls(entry),
                        "publisher": clean_space(source.get("title") if isinstance(source, dict) else ""),
                        "publisher_url": source_url,
                        "language": language,
                        "retrieval_queries": [query],
                    })
                    accepted += 1
                if audit:
                    audit.add(source=source_name, query=query, status="success", records=accepted, pages=1, endpoint=url)
            except Exception as exc:
                if audit:
                    audit.add(source=source_name, query=query, status="failed", endpoint=url, error=exc)
    return output


def search_bing_news(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    per_query: int = 30,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for query in unique_strings(queries):
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss"
        try:
            feed = feedparser.parse(http.get_text(url))
            accepted = 0
            for entry in feed.entries[:per_query]:
                published = _feed_date(entry)
                if published and not (start.isoformat() <= published <= end.isoformat()):
                    continue
                output.append({
                    "source": "Bing News RSS",
                    "title": clean_space(entry.get("title")),
                    "url": clean_space(entry.get("link")),
                    "published_date": published,
                    "excerpt": strip_tags(entry.get("summary")),
                    "rss_summary_html": entry.get("summary") or "",
                    "candidate_urls": _rss_candidate_urls(entry),
                    "publisher": clean_space(entry.get("author")),
                    "language": "unknown",
                    "retrieval_queries": [query],
                })
                accepted += 1
            if audit:
                audit.add(source="Bing News RSS", query=query, status="success", records=accepted, pages=1, endpoint=url)
        except Exception as exc:
            if audit:
                audit.add(source="Bing News RSS", query=query, status="failed", endpoint=url, error=exc)
    return output


def search_gdelt(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    per_query: int = 60,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"
    for query in unique_strings(queries):
        try:
            payload = http.get_json(endpoint, params={
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": min(250, per_query),
                "startdatetime": start.strftime("%Y%m%d000000"),
                "enddatetime": end.strftime("%Y%m%d235959"),
                "sort": "HybridRel",
            })
            rows = payload.get("articles", []) or []
            for item in rows:
                output.append({
                    "source": "GDELT DOC 2.0",
                    "title": clean_space(item.get("title")),
                    "url": clean_space(item.get("url")),
                    "published_date": safe_date_string(item.get("seendate")),
                    # socialimage is not an excerpt; keep it separately.
                    "excerpt": "",
                    "image_url": clean_space(item.get("socialimage")),
                    "publisher": clean_space(item.get("domain")),
                    "language": clean_space(item.get("language")) or "unknown",
                    "retrieval_queries": [query],
                })
            if audit:
                audit.add(source="GDELT DOC 2.0", query=query, status="success", records=len(rows), pages=1, endpoint=endpoint)
        except Exception as exc:
            if audit:
                audit.add(source="GDELT DOC 2.0", query=query, status="failed", endpoint=endpoint, error=exc)
    return output


def search_reliefweb(
    http: HttpClient,
    queries: list[str],
    start: date,
    end: date,
    appname: str = "",
    per_query: int = 40,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    """Search ReliefWeb V2 with a pre-approved appname.

    The requested project appname is attempted even while approval is pending.
    A 401/403-style rejection is recorded as ``skipped`` with an explicit
    approval note so it is not confused with a genuine zero-result window.
    """
    endpoint = "https://api.reliefweb.int/v2/reports"
    if not appname:
        if audit:
            audit.add(source="ReliefWeb", status="skipped", endpoint=endpoint, error="RELIEFWEB_APPNAME is not configured")
        return []
    output: list[dict[str, Any]] = []
    for query in unique_strings(queries):
        payload = {
            "limit": min(1000, per_query),
            "query": {
                "value": query,
                "fields": ["title^6", "body", "source.name"],
                "operator": "OR",
            },
            "filter": {
                "operator": "AND",
                "conditions": [
                    {"field": "date.created", "value": {"from": start.isoformat(), "to": end.isoformat()}},
                ],
            },
            "fields": {"include": ["title", "body", "date.created", "source.name", "url"]},
            "sort": ["date.created:desc"],
        }
        try:
            body = http.request("POST", endpoint, params={"appname": appname}, json=payload).json()
            rows = body.get("data", []) or []
            for item in rows:
                fields = item.get("fields") or {}
                sources = fields.get("source") or []
                output.append({
                    "source": "ReliefWeb",
                    "title": clean_space(fields.get("title")),
                    "url": clean_space(fields.get("url")),
                    "published_date": safe_date_string((fields.get("date") or {}).get("created")),
                    "excerpt": strip_tags(fields.get("body"))[:1800],
                    "publisher": ", ".join(clean_space(x.get("name")) for x in sources if x.get("name")),
                    "language": "unknown",
                    "official": True,
                    "retrieval_queries": [query],
                    "retrieval_channels": ["reliefweb_v2"],
                })
            if audit:
                audit.add(source="ReliefWeb", query=query, mode="v2_reports", status="success", records=len(rows), pages=1, endpoint=endpoint, details={"appname": appname})
        except Exception as exc:
            message = clean_space(exc)
            approval_related = any(token in message for token in ("401", "403", "appname", "approved"))
            if audit:
                audit.add(
                    source="ReliefWeb",
                    query=query,
                    mode="v2_reports",
                    status="skipped" if approval_related else "failed",
                    endpoint=endpoint,
                    error=exc,
                    details={
                        "appname": appname,
                        "approval_status": "pending_or_not_approved" if approval_related else "unknown",
                    },
                )
            if approval_related:
                break
    return output


def search_who(
    http: HttpClient,
    terms: list[str],
    start: date,
    end: date,
    audit: SourceAudit | None = None,
) -> list[dict[str, Any]]:
    """Best-effort WHO site discovery.

    The page is treated as an auxiliary source. Undated links are retained for
    body extraction but are marked as such and never counted as proof that a
    time-window query succeeded.
    """
    output: list[dict[str, Any]] = []
    for term in unique_strings(terms)[:8]:
        endpoint = f"https://www.who.int/home/search-results?indexCatalogue=genericsearchindex1&searchQuery={quote_plus(term)}&wordsMode=AnyWord"
        try:
            raw = http.get_text(endpoint)
            rows = 0
            for href, title in re.findall(r'href="([^"]+)"[^>]*>([^<]{15,220})</a>', raw, flags=re.I):
                title = clean_space(title)
                if term.casefold() not in title.casefold():
                    continue
                if href.startswith("/"):
                    href = "https://www.who.int" + href
                output.append({
                    "source": "WHO website search",
                    "title": title,
                    "url": href,
                    "published_date": None,
                    "excerpt": "",
                    "publisher": "World Health Organization",
                    "language": "unknown",
                    "official": True,
                    "undated_candidate": True,
                    "retrieval_queries": [term],
                })
                rows += 1
            if audit:
                audit.add(source="WHO website search", query=term, status="success", records=rows, pages=1, endpoint=endpoint)
        except Exception as exc:
            if audit:
                audit.add(source="WHO website search", query=term, status="failed", endpoint=endpoint, error=exc)
    return output


def filter_news_window(records: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        published = safe_date_string(record.get("published_date"))
        if published and not (start.isoformat() <= published <= end.isoformat()):
            continue
        if not clean_space(record.get("title")) or not clean_space(record.get("url")):
            continue
        record["published_date"] = published
        output.append(record)
    return output
