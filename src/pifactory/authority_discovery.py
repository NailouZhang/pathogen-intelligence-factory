from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from .http import HttpClient
from .utils import clean_space, unique_strings

ALLOWED_AUTHORITY_DOMAINS = ("ictv.global", "viralzone.expasy.org")


def _allowed(url: str, domains: tuple[str, ...]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _decode_ddg_url(url: str) -> str:
    parsed = urlparse(html.unescape(url))
    if "duckduckgo.com" in (parsed.hostname or ""):
        value = parse_qs(parsed.query).get("uddg", [""])[0]
        if value:
            return unquote(value)
    return html.unescape(url)


def _google_cse(
    http: HttpClient,
    query: str,
    api_key: str,
    cse_id: str,
    domains: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not api_key or not cse_id:
        return []
    try:
        payload = http.get_json(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cse_id, "q": query, "num": 10},
        )
    except Exception:
        return []
    output = []
    for item in payload.get("items", []) or []:
        url = clean_space(item.get("link"))
        if url and _allowed(url, domains):
            output.append({
                "url": url,
                "title": clean_space(item.get("title")),
                "snippet": clean_space(item.get("snippet")),
                "discovery_method": "google_custom_search",
            })
    return output


def _duckduckgo(
    http: HttpClient,
    query: str,
    domains: tuple[str, ...],
) -> list[dict[str, Any]]:
    try:
        raw = http.get_text(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=35,
        )
    except Exception:
        return []
    soup = BeautifulSoup(raw, "lxml")
    output = []
    for anchor in soup.select("a.result__a, a[data-testid='result-title-a']"):
        url = _decode_ddg_url(clean_space(anchor.get("href")))
        if not url or not _allowed(url, domains):
            continue
        parent = anchor.find_parent(class_=re.compile("result"))
        snippet = ""
        if parent:
            node = parent.select_one(".result__snippet")
            snippet = clean_space(node.get_text(" ")) if node else ""
        output.append({
            "url": url,
            "title": clean_space(anchor.get_text(" ")),
            "snippet": snippet,
            "discovery_method": "duckduckgo_html",
        })
    return output


def discover_authoritative_urls(
    seed: dict[str, Any],
    http: HttpClient,
    *,
    google_api_key: str = "",
    google_cse_id: str = "",
) -> list[dict[str, Any]]:
    domains = tuple(seed.get("authority_search_domains") or ALLOWED_AUTHORITY_DOMAINS)
    terms = unique_strings(
        list(seed.get("seed_terms") or [])
        + list(seed.get("virus_names") or [])
        + [seed.get("display_name_en")]
    )[:6]

    output: list[dict[str, Any]] = []
    # Zero-key site-search fallbacks are always added. They are less precise
    # than discovered report pages but keep first-run profile construction
    # operational when Google CSE and general web search are unavailable.
    if terms:
        anchor = quote_plus(terms[0])
        output.extend([
            {
                "url": f"https://ictv.global/search?search_api_fulltext={anchor}",
                "title": "ICTV site search",
                "snippet": "",
                "discovery_method": "authority_site_search",
            },
            {
                "url": f"https://viralzone.expasy.org/search?query={anchor}",
                "title": "ViralZone site search",
                "snippet": "",
                "discovery_method": "authority_site_search",
            },
        ])
    for url in seed.get("authoritative_urls") or []:
        if _allowed(str(url), domains):
            output.append({
                "url": str(url),
                "title": "seed-provided authority URL",
                "snippet": "",
                "discovery_method": "seed",
            })

    for domain in domains:
        for term in terms[:3]:
            query = f'site:{domain} "{term}" virus taxonomy'
            output.extend(_google_cse(http, query, google_api_key, google_cse_id, domains))
            if not any(row.get("url") for row in output if row.get("discovery_method") == "google_custom_search"):
                output.extend(_duckduckgo(http, query, domains))

    # Keep the first occurrence of each canonical URL and cap network work.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in output:
        url = clean_space(row.get("url")).split("#", 1)[0]
        if not url or url in seen or not _allowed(url, domains):
            continue
        seen.add(url)
        row = dict(row)
        row["url"] = url
        unique.append(row)
    return unique[:20]
