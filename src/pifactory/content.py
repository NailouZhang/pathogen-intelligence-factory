from __future__ import annotations

import io
import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

import fitz
import trafilatura
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import partial_ratio, ratio, token_set_ratio

from .http import HttpClient
from .browser_fetch import browser_enabled, fetch_rendered_html
from .relevance import relevance_assessment
from .utils import clean_space, extract_doi, normalize_title, sha256_text, split_sentences, strip_tags, truncate, unique_strings, utc_now_iso


LEGAL_FULLTEXT_POLICY = "legal_open_access_only"
LEGAL_FULLTEXT_SOURCES = [
    "Europe PMC/PMC Open Access full text",
    "PMC BioC",
    "OpenAlex best open-access location",
    "Unpaywall open-access locations",
    "Crossref-provided full-text links",
    "publisher or DOI landing pages accessible without access-control bypass",
]

BOILERPLATE_PATTERNS = [
    r"comprehensive up[- ]to[- ]date news coverage",
    r"aggregated from sources all over the world",
    r"google news provides",
    r"by google news",
    r"view full coverage",
    r"click here to read more",
    r"original abstract or excerpt is unavailable",
]


def remove_boilerplate(text: str) -> str:
    value = clean_space(text)
    for pattern in BOILERPLATE_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.I)
    value = re.sub(r"^[\s,;:.\-–—]+|[\s,;:.\-–—]+$", "", value)
    return clean_space(value)


def _meta_content(soup: BeautifulSoup, names: list[tuple[str, str]]) -> str:
    for attr, value in names:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            return clean_space(tag.get("content"))
    return ""


def _extract_jsonld(soup: BeautifulSoup) -> tuple[str, str]:
    title = ""
    body = ""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ")
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph") if isinstance(node.get("@graph"), list) else [node]
            for item in graph:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("@type", "")).lower()
                if "article" in item_type or "report" in item_type or "news" in item_type:
                    title = title or clean_space(item.get("headline") or item.get("name"))
                    body = body or clean_space(item.get("articleBody") or item.get("description"))
    return title, body


def _paragraph_text(soup: BeautifulSoup) -> str:
    paragraphs: list[str] = []
    for node in soup.select("article p, main p, [role='main'] p, .article-body p, .story-body p, .entry-content p, .post-content p"):
        value = clean_space(node.get_text(" "))
        if len(value) >= 35:
            paragraphs.append(value)
    return clean_space(" ".join(unique_strings(paragraphs)))


def _is_aggregator_url(value: str | None) -> bool:
    url = clean_space(value).lower()
    return any(host in url for host in (
        "news.google.", "google.com/rss", "googleusercontent.com",
        "bing.com/news", "msn.com/",
    ))


TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "mc_cid", "mc_eid", "ocid", "cmpid", "ref",
    "ref_src", "src", "guccounter", "guce_referrer", "guce_referrer_sig",
}


BLOCKED_NEWS_DESTINATION_HOSTS = {
    "w3.org",
    "www.w3.org",
    "schema.org",
    "www.schema.org",
    "xml.org",
    "www.xml.org",
}


def _host(value: str | None) -> str:
    try:
        return urlparse(clean_space(value)).netloc.casefold().split(":", 1)[0]
    except Exception:
        return ""


def _site_key(value: str | None) -> str:
    host = _host(value)
    if host.startswith("www."):
        host = host[4:]
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return host
    # Keep the common country-code second-level suffixes together without
    # adding a heavy public-suffix dependency to the runtime.
    if labels[-2] in {"co", "com", "org", "net", "gov", "ac"} and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _same_site(left: str | None, right: str | None) -> bool:
    return bool(_site_key(left) and _site_key(left) == _site_key(right))


def _blocked_news_destination(value: str | None) -> bool:
    host = _host(value)
    return host in BLOCKED_NEWS_DESTINATION_HOSTS


def clean_news_url(value: str | None) -> str:
    url = _decode_embedded_url(clean_space(value))
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        query = []
        for key, values in parse_qs(parsed.query, keep_blank_values=False).items():
            if key.casefold() in TRACKING_QUERY_KEYS or key.casefold().startswith("utm_"):
                continue
            for item in values:
                query.append((key, item))
        cleaned = parsed._replace(fragment="", query=urlencode(query, doseq=True))
        return urlunparse(cleaned)
    except Exception:
        return url


def _candidate_news_urls(record: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for value in (
        record.get("resolved_url"), record.get("url"), record.get("source_url"),
        record.get("canonical_url"), record.get("link"), record.get("original_url"),
        record.get("publisher_url"),
    ):
        cleaned = clean_news_url(value)
        if cleaned:
            urls.append(cleaned)
    for value in record.get("candidate_urls") or []:
        cleaned = clean_news_url(value)
        if cleaned:
            urls.append(cleaned)
    for duplicate in record.get("duplicate_sources") or []:
        if isinstance(duplicate, dict):
            cleaned = clean_news_url(duplicate.get("url"))
            if cleaned:
                urls.append(cleaned)
    # Direct publisher URLs are tried before Google/Bing aggregation pages.
    # Standards/documentation sites are never valid news destinations and were
    # a recurring source of false full text.
    urls = [x for x in unique_strings(urls) if not _blocked_news_destination(x)]
    return sorted(urls, key=lambda x: (1 if _is_aggregator_url(x) else 0, len(x)))


def _decode_embedded_url(value: str) -> str:
    url = clean_space(value)
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        for key in ("url", "u", "q", "target", "redirect", "redirect_url"):
            for candidate in query.get(key, []):
                decoded = unquote(candidate)
                if decoded.startswith(("http://", "https://")):
                    return decoded
    except Exception:
        pass
    return url


def _external_news_urls(
    soup: BeautifulSoup,
    raw: str,
    base_url: str,
    record: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Discover plausible article URLs without crawling arbitrary navigation.

    Publisher pages expose only same-site canonical/meta/JSON-LD URLs. Arbitrary
    anchors and escaped script URLs are considered only on known aggregators,
    then checked against the expected publisher or headline.
    """
    candidates: list[dict[str, str]] = []
    for attr, value in (("property", "og:url"), ("name", "twitter:url"), ("name", "citation_public_url")):
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            candidates.append({
                "url": urljoin(base_url, clean_space(tag.get("content"))),
                "provenance": f"meta:{value}",
                "label": "",
            })
    canonical = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
    if canonical and canonical.get("href"):
        candidates.append({
            "url": urljoin(base_url, clean_space(canonical.get("href"))),
            "provenance": "canonical",
            "label": "",
        })
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text(" "))
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        for node in stack:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph") if isinstance(node.get("@graph"), list) else [node]
            for item in graph:
                if not isinstance(item, dict):
                    continue
                label = clean_space(item.get("headline") or item.get("name"))
                for key in ("url", "mainEntityOfPage"):
                    value = item.get(key)
                    if isinstance(value, dict):
                        value = value.get("@id") or value.get("url")
                    if isinstance(value, str):
                        candidates.append({
                            "url": urljoin(base_url, value),
                            "provenance": "jsonld",
                            "label": label,
                        })

    aggregator = _is_aggregator_url(base_url)
    if aggregator:
        for anchor in soup.find_all("a", href=True):
            href = _decode_embedded_url(urljoin(base_url, anchor.get("href")))
            if href.startswith(("http://", "https://")):
                candidates.append({
                    "url": href,
                    "provenance": "aggregator_anchor",
                    "label": clean_space(anchor.get_text(" ")),
                })
        for found in re.findall(r'https?://[^"\'<>\s]+', raw):
            candidates.append({
                "url": found.replace("\u0026", "&").replace("\\/", "/"),
                "provenance": "aggregator_script",
                "label": "",
            })

    blocked = (
        "news.google.", "google.com/", "googleusercontent.com", "gstatic.com",
        "bing.com/", "microsoft.com/", "facebook.com/", "twitter.com/",
        "x.com/", "youtube.com/", "doubleclick.net/",
    )
    expected_title = clean_space(record.get("title"))
    expected_sites = unique_strings(
        _site_key(value)
        for value in (
            record.get("publisher_url"), record.get("source_url"), record.get("original_url"),
            record.get("resolved_url"), record.get("url"),
        )
        if value and not _is_aggregator_url(value)
    )
    trusted_metadata = {
        "canonical", "jsonld", "meta:og:url", "meta:twitter:url", "meta:citation_public_url",
    }
    output: list[str] = []
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        decoded = clean_news_url(_decode_embedded_url(item.get("url", "")))
        if not decoded or decoded in seen:
            continue
        seen.add(decoded)
        decision: dict[str, Any] = {
            "url": decoded,
            "provenance": item.get("provenance"),
            "label": truncate(clean_space(item.get("label")), 180),
            "accepted": False,
            "reason": "",
        }
        try:
            parsed = urlparse(decoded)
        except Exception:
            decision["reason"] = "invalid_url"
            decisions.append(decision)
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            decision["reason"] = "invalid_url"
            decisions.append(decision)
            continue
        lower = decoded.lower()
        if any(token in lower for token in blocked) or _blocked_news_destination(decoded):
            decision["reason"] = "blocked_destination"
            decisions.append(decision)
            continue
        if parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js")):
            decision["reason"] = "asset_url"
            decisions.append(decision)
            continue

        if not aggregator:
            if not _same_site(decoded, base_url):
                decision["reason"] = "publisher_page_external_link"
                decisions.append(decision)
                continue
            if item.get("provenance") not in trusted_metadata:
                decision["reason"] = "publisher_navigation_not_discoverable"
                decisions.append(decision)
                continue
        else:
            candidate_site = _site_key(decoded)
            publisher_match = bool(expected_sites and candidate_site in expected_sites)
            label_score = (
                token_set_ratio(normalize_title(expected_title), normalize_title(item.get("label"))) / 100
                if expected_title and item.get("label") else 0.0
            )
            decision["headline_similarity"] = round(label_score, 3)
            if item.get("provenance") == "aggregator_script" and not publisher_match:
                decision["reason"] = "unverified_script_url"
                decisions.append(decision)
                continue
            if expected_sites and not publisher_match and label_score < 0.55:
                decision["reason"] = "publisher_or_headline_mismatch"
                decisions.append(decision)
                continue
            if not expected_sites and item.get("provenance") == "aggregator_anchor" and label_score < 0.55:
                decision["reason"] = "headline_mismatch"
                decisions.append(decision)
                continue

        decision["accepted"] = True
        decision["reason"] = "trusted_discovery"
        decisions.append(decision)
        output.append(decoded)
    return unique_strings(output)[:20], decisions


def _news_text_quality(text: str, title: str) -> tuple[bool, float, dict[str, Any]]:
    value = remove_boilerplate(text)
    title_norm = normalize_title(title)
    value_norm = normalize_title(value)
    title_similarity = ratio(title_norm, value_norm) / 100 if title_norm and value_norm else 0.0
    sentences = split_sentences(value, max_sentences=200)
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value.lower())
    unique_ratio = len(set(words)) / max(1, len(words))
    title_only = bool(
        value
        and len(value) <= max(260, len(clean_space(title)) * 4)
        and title_similarity >= 0.82
    )
    navigation_noise = sum(
        value.lower().count(marker)
        for marker in (
            "cookie",
            "privacy policy",
            "sign in",
            "subscribe",
            "all rights reserved",
            "advertisement",
            "accept cookies",
        )
    )
    valid = len(value) >= 260 and len(sentences) >= 2 and not title_only and unique_ratio >= 0.10
    score = min(len(value), 12000) / 50 + len(sentences) * 3 + unique_ratio * 40 - navigation_noise * 10
    if title_only:
        score -= 300
    return valid, score, {
        "chars": len(value),
        "sentences": len(sentences),
        "unique_word_ratio": round(unique_ratio, 3),
        "title_body_similarity": round(title_similarity, 3),
        "title_only": title_only,
        "navigation_noise": navigation_noise,
    }


def _news_summary_quality(text: str, title: str) -> tuple[bool, dict[str, Any]]:
    """Validate a syndicated/RSS summary without pretending it is full text."""
    value = remove_boilerplate(text)
    title_norm = normalize_title(title)
    value_norm = normalize_title(value)
    similarity = ratio(title_norm, value_norm) / 100 if title_norm and value_norm else 0.0
    tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value)
    sentences = split_sentences(value, max_sentences=20)
    valid = (
        len(value) >= int(os.getenv("PIF_NEWS_EXCERPT_MIN_CHARS", "100"))
        and len(tokens) >= 18
        and similarity < 0.90
        and (len(sentences) >= 2 or len(value) >= 220)
    )
    return valid, {
        "chars": len(value),
        "tokens": len(tokens),
        "sentences": len(sentences),
        "title_body_similarity": round(similarity, 3),
        "title_only": similarity >= 0.90 and len(value) < 300,
    }


def _news_content_identity(
    record: dict[str, Any],
    text: str,
    page_title: str,
    candidate_url: str,
    profile: dict[str, Any] | None,
) -> tuple[bool, dict[str, Any]]:
    """Require the extracted body itself to identify the target pathogen.

    Candidate relevance was previously computed from the RSS headline and could
    therefore rescue a completely unrelated body. The hard gate deliberately
    calls ``relevance_assessment`` with an empty title so only body evidence can
    satisfy the identity requirement.
    """
    value = remove_boilerplate(text)
    expected_title = clean_space(record.get("title"))
    page_title = clean_space(page_title)
    title_similarity = (
        max(
            token_set_ratio(normalize_title(expected_title), normalize_title(page_title)),
            partial_ratio(normalize_title(expected_title), normalize_title(page_title)),
        ) / 100
        if expected_title and page_title else 0.0
    )
    if not profile:
        return True, {
            "accepted": True,
            "reason": "profile_not_supplied_legacy_mode",
            "page_title_similarity": round(title_similarity, 3),
            "candidate_url": candidate_url,
        }

    body_assessment = relevance_assessment("", value, profile)
    identity_frequency = int(body_assessment.get("identity_frequency") or 0)
    identity_hits = unique_strings(
        (body_assessment.get("body_identity_hits") or [])
        + (body_assessment.get("qualified_identity_hits") or [])
    )
    context_hits = unique_strings(body_assessment.get("context_hits") or [])
    identity_sentences = unique_strings(
        sentence
        for sentence in split_sentences(value, max_sentences=250)
        if any(_term.casefold() in sentence.casefold() for _term in identity_hits)
    )
    # Repetition of one sidebar headline must not become "strong evidence".
    # Require multiple distinct identity-bearing sentences, multiple distinct
    # pathogen identities, or a pathogen identity plus relevant event context.
    strong_body = len(identity_sentences) >= 2 or len(identity_hits) >= 2
    body_identity = bool(body_assessment.get("identity_present") and identity_hits)

    accepted = bool(
        body_identity
        and body_assessment.get("decision") in {"accept", "review"}
        and (strong_body or title_similarity >= 0.55)
    )
    if not body_identity:
        reason = "body_missing_pathogen_identity"
    elif body_assessment.get("decision") == "reject":
        reason = "body_relevance_rejected"
    elif not strong_body and page_title and title_similarity < 0.55:
        reason = "weak_body_identity_and_headline_mismatch"
    else:
        reason = "body_identity_confirmed"
    return accepted, {
        "accepted": accepted,
        "reason": reason,
        "candidate_url": candidate_url,
        "page_title": truncate(page_title, 300),
        "page_title_similarity": round(title_similarity, 3),
        "body_relevance": body_assessment,
        "body_identity_hits": identity_hits,
        "body_identity_frequency": identity_frequency,
        "body_context_hits": context_hits,
        "identity_sentence_count": len(identity_sentences),
        "strong_body_identity": strong_body,
    }


def _extract_news_candidates(raw: str, soup: BeautifulSoup, url: str = "") -> list[tuple[str, str]]:
    _, jsonld_body = _extract_jsonld(soup)
    candidates: list[tuple[str, str]] = []
    if jsonld_body:
        candidates.append(("jsonld_articleBody", jsonld_body))

    precision = trafilatura.extract(
        raw, url=url or None, include_comments=False, include_tables=False, favor_precision=True,
    ) or ""
    if precision:
        candidates.append(("trafilatura_precision", precision))
    recall = trafilatura.extract(
        raw, url=url or None, include_comments=False, include_tables=False, favor_recall=True,
    ) or ""
    if recall:
        candidates.append(("trafilatura_recall", recall))

    # Readability and newspaper3k use different layout heuristics, which is
    # useful when news sites change templates. They are lazy imports so a
    # missing optional dependency is recorded rather than breaking the cycle.
    try:
        from readability import Document
        readable = Document(raw).summary(html_partial=True)
        readable_text = BeautifulSoup(readable, "lxml").get_text(" ")
        if readable_text:
            candidates.append(("readability_lxml", readable_text))
    except Exception:
        pass
    try:
        from newspaper import Article
        article_obj = Article(url or "https://local.invalid/", language="en")
        article_obj.set_html(raw)
        article_obj.parse()
        if article_obj.text:
            candidates.append(("newspaper3k", article_obj.text))
    except Exception:
        pass

    article = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
    if article:
        candidates.append(("article_or_main", article.get_text(" ")))
    paragraphs = _paragraph_text(soup)
    if paragraphs:
        candidates.append(("paragraph_selector", paragraphs))
    try:
        basic = trafilatura.html2txt(raw) or ""
        if basic:
            candidates.append(("trafilatura_html2txt", basic))
    except Exception:
        pass
    return [(method, remove_boilerplate(value)) for method, value in candidates if clean_space(value)]


def resolve_and_extract_news(
    http: HttpClient,
    record: dict[str, Any],
    profile: dict[str, Any] | None = None,
    max_chars: int = 18000,
) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "attempted_urls": [],
        "extraction_attempts": [],
        "browser_attempts": [],
        "retrieved_at": utc_now_iso(),
        "policy_version": "v11-news-identity-gate-circuit-breaker-1",
    }
    queue = _candidate_news_urls(record)
    visited: set[str] = set()
    best_text = ""
    best_title = clean_space(record.get("title"))
    best_method = "none"
    best_score = float("-inf")
    best_quality: dict[str, Any] = {}
    best_identity: dict[str, Any] = {}
    best_url = ""
    final_url = clean_news_url(record.get("url"))
    static_limit = max(1, int(os.getenv("PIF_NEWS_STATIC_MAX_URLS", "8")))

    def evaluate_html(raw: str, page_url: str, title_hint: str = "", channel: str = "static") -> None:
        nonlocal best_text, best_title, best_method, best_score, best_quality, best_identity, best_url
        soup = BeautifulSoup(raw, "lxml")
        jsonld_title, _ = _extract_jsonld(soup)
        candidate_title = jsonld_title or _meta_content(
            soup, [("property", "og:title"), ("name", "twitter:title"), ("name", "citation_title")],
        ) or clean_space(soup.title.get_text(" ") if soup.title else "") or title_hint or clean_space(record.get("title"))
        canonical = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
        canonical_url = clean_news_url(urljoin(page_url, canonical.get("href"))) if canonical and canonical.get("href") else ""
        discovered, discovery_audit = _external_news_urls(soup, raw, page_url, record)
        for candidate_url in discovered:
            if candidate_url and candidate_url not in visited and candidate_url not in queue:
                queue.append(candidate_url)
        if discovery_audit:
            audit.setdefault("url_discovery", []).extend(discovery_audit)
        for method, extracted in _extract_news_candidates(raw, soup, page_url):
            valid, score, quality = _news_text_quality(extracted, record.get("title"))
            identity_ok, identity = _news_content_identity(
                record, extracted, candidate_title, canonical_url or page_url, profile,
            )
            audit["extraction_attempts"].append({
                "url": page_url, "channel": channel, "method": method,
                "status": "valid" if valid and identity_ok else "rejected",
                "structural_valid": valid,
                "identity_valid": identity_ok,
                "identity": identity,
                **quality,
            })
            if valid and identity_ok and score > best_score:
                best_text = extracted
                best_title = candidate_title
                best_method = f"{channel}:{method}"
                best_score = score
                best_quality = quality
                best_identity = identity
                best_url = canonical_url or page_url

    while queue and len(visited) < static_limit:
        url = clean_news_url(queue.pop(0))
        if not url or url in visited:
            continue
        visited.add(url)
        audit["attempted_urls"].append(url)
        try:
            response = http.request("GET", url, allow_redirects=True, timeout=25, retry_attempts=2)
            final_url = clean_news_url(response.url) or url
            if final_url and final_url != url and final_url not in visited and final_url not in queue:
                # Aggregator endpoints frequently redirect to the publisher.
                # Preserve that resolved address as a first-class candidate so
                # another extractor/browser attempt can use it directly.
                queue.insert(0, final_url)
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "text" not in content_type:
                audit["extraction_attempts"].append({"url": url, "status": "unsupported_content_type", "content_type": content_type})
                continue
            evaluate_html(response.text, final_url, channel="static")
            if best_text and len(best_text) >= 2200:
                break
        except Exception as exc:
            audit.setdefault("errors", []).append({"url": url, "channel": "static", "error": clean_space(exc)[:400]})

    # Only candidates that failed static extraction are escalated. Browser
    # rendering is bounded and prefers direct publisher URLs.
    if not best_text and browser_enabled():
        browser_limit = max(1, int(os.getenv("PIF_NEWS_BROWSER_MAX_PAGES", "3")))
        browser_urls = sorted(unique_strings(list(visited) + queue), key=lambda x: (1 if _is_aggregator_url(x) else 0, len(x)))[:browser_limit]
        for url in browser_urls:
            rendered = fetch_rendered_html(url)
            audit["browser_attempts"].append({k: v for k, v in rendered.items() if k != "html"})
            if rendered.get("status") != "success" or not rendered.get("html"):
                continue
            final_url = clean_news_url(rendered.get("url")) or url
            evaluate_html(rendered["html"], final_url, title_hint=rendered.get("title") or "", channel="playwright")
            if best_text and len(best_text) >= 1200:
                break

    rss_excerpt = remove_boilerplate(record.get("excerpt") or "")
    summary_valid, summary_quality = _news_summary_quality(rss_excerpt, record.get("title"))
    summary_identity_ok, summary_identity = _news_content_identity(
        record,
        rss_excerpt,
        clean_space(record.get("title")),
        clean_news_url(record.get("url")),
        profile,
    )
    if best_text:
        content_status = "full" if len(best_text) >= 1500 else "partial"
        content = truncate(best_text, max_chars)
    elif summary_valid and summary_identity_ok:
        content_status = "syndicated_summary"
        content = truncate(rss_excerpt, min(max_chars, 6000))
        best_method = "rss_syndicated_summary"
        best_quality = summary_quality
        best_identity = summary_identity
        # A rejected landing page must never replace the source URL of a valid
        # syndicated summary.
        best_url = clean_news_url(record.get("url"))
    elif rss_excerpt:
        if summary_quality.get("title_only"):
            content_status = "title_only_rejected"
        elif not summary_identity_ok:
            content_status = "identity_rejected"
        else:
            content_status = "excerpt_only"
        content = ""
        best_method = "rss_excerpt_not_substantive"
        best_quality = summary_quality
        best_identity = summary_identity
    else:
        content_status = "unavailable"
        content = ""

    record["resolved_url"] = best_url or final_url or clean_news_url(record.get("url"))
    if best_url:
        record["canonical_url"] = best_url
    record["retrieved_at"] = audit["retrieved_at"]
    record["content_title"] = best_title
    record["content"] = content
    record["content_status"] = content_status
    record["content_method"] = best_method
    record["content_hash"] = sha256_text(content) if content else None
    record["title_body_similarity"] = best_quality.get("title_body_similarity")
    record["content_identity"] = best_identity
    record["content_audit"] = {
        **audit,
        "selected_quality": best_quality,
        "selected_identity": best_identity,
        "provenance": content_status,
    }
    return record


def apply_news_content_circuit_breaker(
    records: list[dict[str, Any]],
    *,
    title_similarity_threshold: float = 0.62,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reject shared error pages and collapse true post-fetch duplicates.

    Different RSS records resolving to the same URL or identical body is a
    strong signal that extraction landed on a home page, standards document, or
    another common fallback page. If the headlines are dissimilar, every member
    of the group is rejected. If the headlines describe the same story, one
    highest-quality representative is retained and the rest are deduplicated.
    """
    indexed = list(enumerate(records))
    groups: list[tuple[str, str, list[int]]] = []
    for field in ("resolved_url", "content_hash"):
        buckets: dict[str, list[int]] = {}
        for index, record in indexed:
            value = clean_space(record.get(field))
            if not value:
                continue
            buckets.setdefault(value, []).append(index)
        for value, members in buckets.items():
            if len(members) >= 2:
                groups.append((field, value, members))

    rejected: dict[int, dict[str, Any]] = {}
    group_audit: list[dict[str, Any]] = []
    for field, value, members in groups:
        active = [index for index in members if index not in rejected]
        if len(active) < 2:
            continue
        similarities: list[float] = []
        for left_pos, left in enumerate(active):
            for right in active[left_pos + 1:]:
                similarities.append(
                    token_set_ratio(
                        normalize_title(records[left].get("title")),
                        normalize_title(records[right].get("title")),
                    ) / 100
                )
        maximum_similarity = max(similarities or [0.0])
        shared_error = maximum_similarity < title_similarity_threshold
        audit = {
            "group_by": field,
            "value": truncate(value, 500),
            "record_ids": [records[index].get("news_id") for index in active],
            "titles": [records[index].get("title") for index in active],
            "maximum_title_similarity": round(maximum_similarity, 3),
            "action": "reject_all_shared_error_page" if shared_error else "keep_one_duplicate_story",
        }
        group_audit.append(audit)
        if shared_error:
            for index in active:
                rejected[index] = {
                    "reason": "shared_error_page_suspected",
                    "group_by": field,
                    "group_value": value,
                    "maximum_title_similarity": round(maximum_similarity, 3),
                }
            continue

        def quality_key(index: int) -> tuple[int, int, int]:
            record = records[index]
            status_rank = {"full": 3, "partial": 2, "syndicated_summary": 1}.get(record.get("content_status"), 0)
            quality = (record.get("content_audit") or {}).get("selected_quality") or {}
            chars = int(quality.get("chars") or len(clean_space(record.get("content"))))
            direct = 0 if _is_aggregator_url(record.get("resolved_url")) else 1
            return status_rank, chars, direct

        keeper = max(active, key=quality_key)
        for index in active:
            if index == keeper:
                continue
            rejected[index] = {
                "reason": "duplicate_content_after_enrichment",
                "group_by": field,
                "group_value": value,
                "kept_news_id": records[keeper].get("news_id"),
                "maximum_title_similarity": round(maximum_similarity, 3),
            }

    retained: list[dict[str, Any]] = []
    rejected_records: list[dict[str, Any]] = []
    for index, record in indexed:
        decision = rejected.get(index)
        if not decision:
            retained.append(record)
            continue
        record["content_circuit_breaker"] = {"accepted": False, **decision}
        rejected_records.append({
            "news_id": record.get("news_id"),
            "title": record.get("title"),
            "source": record.get("source"),
            "resolved_url": record.get("resolved_url"),
            "content_hash": record.get("content_hash"),
            "decision": decision,
        })
    return retained, {
        "input": len(records),
        "retained": len(retained),
        "rejected": len(rejected_records),
        "groups": group_audit,
        "rejected_records": rejected_records,
        "policy_version": "v11-shared-url-content-circuit-breaker-1",
    }


def _jats_sections(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    sections: dict[str, list[str]] = {"abstract": [], "methods": [], "results": [], "discussion": [], "conclusion": [], "other": []}
    for abstract in root.findall(".//abstract"):
        text = clean_space(" ".join("".join(abstract.itertext()).split()))
        if text:
            sections["abstract"].append(text)
    for sec in root.findall(".//body//sec"):
        title_node = sec.find("title")
        title = clean_space("".join(title_node.itertext()) if title_node is not None else "").lower()
        text = clean_space(" ".join("".join(sec.itertext()).split()))
        if not text:
            continue
        if any(k in title for k in ("method", "material", "patient", "study design")):
            key = "methods"
        elif any(k in title for k in ("result", "finding")):
            key = "results"
        elif "discussion" in title:
            key = "discussion"
        elif any(k in title for k in ("conclusion", "summary")):
            key = "conclusion"
        else:
            key = "other"
        sections[key].append(text)
    return {key: clean_space(" ".join(values)) for key, values in sections.items() if values}


def _pdf_text(raw: bytes, max_pages: int = 60) -> str:
    document = fitz.open(stream=raw, filetype="pdf")
    texts: list[str] = []
    for index, page in enumerate(document):
        if index >= max_pages:
            break
        texts.append(page.get_text("text"))
    return clean_space(" ".join(texts))


def _identity_score(work: dict[str, Any], candidate_text: str, candidate_url: str) -> tuple[bool, dict[str, Any]]:
    expected_title = normalize_title(work.get("title"))
    head = normalize_title(candidate_text[:2500])
    title_score = partial_ratio(expected_title, head) / 100 if expected_title and head else 0.0
    expected_doi = (work.get("doi") or "").lower()
    doi_candidates = {x.lower() for x in re.findall(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", candidate_text[:12000], flags=re.I)}
    doi_match = bool(expected_doi and expected_doi in {x.rstrip(".,;)]}") for x in doi_candidates})
    author_match = False
    lower = candidate_text[:8000].lower()
    for author in (work.get("authors") or [])[:4]:
        family = clean_space(author).split(" ")[-1].lower()
        if len(family) > 3 and family in lower:
            author_match = True
            break
    accepted = title_score >= 0.82 or (doi_match and (title_score >= 0.45 or author_match)) or (title_score >= 0.62 and author_match)
    return accepted, {"title_score": round(title_score, 3), "doi_match": doi_match, "author_match": author_match, "candidate_url": candidate_url}


def enrich_scholarly_work(http: HttpClient, work: dict[str, Any], mailto: str, max_chars: int = 18000) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "attempts": [],
        "retrieved_at": utc_now_iso(),
        "policy": LEGAL_FULLTEXT_POLICY,
        "allowed_sources": list(LEGAL_FULLTEXT_SOURCES),
    }
    work["full_text_policy"] = LEGAL_FULLTEXT_POLICY
    pmcid = clean_space((work.get("source_ids") or {}).get("pmcid"))
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = "PMC" + pmcid
    candidates: list[tuple[str, str]] = []
    if pmcid:
        candidates.extend([
            ("europe_pmc_xml", f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"),
            ("pmc_bioc", f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode"),
        ])
    for link in work.get("full_text_links") or []:
        if isinstance(link, dict) and link.get("URL"):
            candidates.append(("crossref_link", link.get("URL")))
    if work.get("open_access_pdf"):
        candidates.append(("open_access_pdf", work.get("open_access_pdf")))
    for url in work.get("full_text_urls") or []:
        candidates.append(("europe_pmc_link", url))
    doi = work.get("doi")
    if doi and mailto:
        try:
            payload = http.get_json(f"https://api.unpaywall.org/v2/{doi}", params={"email": mailto})
            for location in [payload.get("best_oa_location")] + list(payload.get("oa_locations") or []):
                if not isinstance(location, dict):
                    continue
                if location.get("url_for_pdf"):
                    candidates.append(("unpaywall_pdf", location.get("url_for_pdf")))
                if location.get("url_for_landing_page"):
                    candidates.append(("unpaywall_landing", location.get("url_for_landing_page")))
        except Exception as exc:
            audit["attempts"].append({"method": "unpaywall", "status": "failed", "error": clean_space(exc)[:200]})
        candidates.append(("doi_landing", f"https://doi.org/{doi}"))
    if work.get("url"):
        candidates.append(("source_landing", work.get("url")))

    seen: set[str] = set()
    best_text = clean_space(work.get("abstract"))
    best_sections: dict[str, str] = {"abstract": best_text} if best_text else {}
    best_method = "abstract_api" if best_text else "metadata_only"
    best_url = None

    for method, url in candidates[:12]:
        if not url or url in seen:
            continue
        seen.add(url)
        attempt: dict[str, Any] = {"method": method, "url": url}
        try:
            response = http.request("GET", url, allow_redirects=True, timeout=35)
            content_type = response.headers.get("Content-Type", "").lower()
            final_url = response.url
            if "pdf" in content_type or response.content.startswith(b"%PDF"):
                text = _pdf_text(response.content)
                sections = {"full_text": text}
                parse_method = "pymupdf"
            elif method == "pmc_bioc" or "json" in content_type:
                payload = response.json()
                documents = payload if isinstance(payload, list) else [payload]
                parts = []
                for document in documents:
                    for passage in document.get("documents", [document])[0].get("passages", []) if isinstance(document, dict) else []:
                        if passage.get("text"):
                            parts.append(passage.get("text"))
                text = clean_space(" ".join(parts))
                sections = {"full_text": text}
                parse_method = "pmc_bioc"
            elif "xml" in content_type or method == "europe_pmc_xml":
                sections = _jats_sections(response.text)
                text = clean_space(" ".join(sections.values()))
                parse_method = "jats_xml"
            else:
                raw = response.text
                sniff = raw.lstrip()[:500].lower()
                if sniff.startswith("<?xml") or sniff.startswith("<article") or "<article " in sniff:
                    sections = _jats_sections(raw)
                    text = clean_space(" ".join(sections.values()))
                    parse_method = "jats_xml_sniffed"
                else:
                    extracted = trafilatura.extract(raw, include_comments=False, include_tables=True, favor_recall=True) or ""
                    soup = BeautifulSoup(raw, "lxml")
                    abstract = _meta_content(soup, [("name", "citation_abstract"), ("name", "description"), ("property", "og:description")])
                    text = clean_space(extracted or abstract)
                    sections = {"full_text": text} if text else {}
                    parse_method = "publisher_html"
            accepted, identity = _identity_score(work, text, final_url)
            attempt.update({"status": "accepted" if accepted else "identity_rejected", "identity": identity, "chars": len(text), "parse_method": parse_method})
            audit["attempts"].append(attempt)
            if not accepted or len(text) < 400:
                continue
            if len(text) > len(best_text):
                best_text = text
                best_sections = sections
                best_method = parse_method
                best_url = final_url
            if len(best_text) >= 6000:
                break
        except Exception as exc:
            attempt.update({"status": "failed", "error": clean_space(exc)[:300]})
            audit["attempts"].append(attempt)

    work["full_text"] = truncate(best_text, max_chars) if len(best_text) > len(clean_space(work.get("abstract"))) else ""
    work["full_text_sections"] = {k: truncate(v, 8000) for k, v in best_sections.items()}
    work["full_text_method"] = best_method
    work["full_text_url"] = best_url
    work["evidence_level"] = "E2" if work.get("full_text") else ("E1" if work.get("abstract") else "E0")
    work["content_audit"] = audit
    return work

