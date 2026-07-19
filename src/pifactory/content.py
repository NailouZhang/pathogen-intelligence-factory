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
from .literature.enrichment import classify_scholarly_payload
from .literature.identity import assess_completion_identity, merge_verified_candidate, register_identity_assessment
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


def _is_official_news_record(record: dict[str, Any]) -> bool:
    if record.get("official"):
        return True
    identity = " ".join(
        clean_space(record.get(key)) for key in ("source", "publisher", "publisher_url", "url")
    ).casefold()
    return any(token in identity for token in (
        "world health organization", "who.int", "reliefweb", "cdc.gov", "ecdc.europa.eu",
        "paho.org", "afro.who.int", "gov.", ".gov", "ministry of health", "public health agency",
    ))

def _news_text_quality(text: str, title: str, *, official: bool = False) -> tuple[bool, float, dict[str, Any]]:
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
    standard_valid = len(value) >= 260 and len(sentences) >= 2 and not title_only and unique_ratio >= 0.10
    # Official public-health notices are often concise. Their eligibility is
    # determined by verified source metadata and the separate pathogen body
    # identity gate, not by a minimum character count.
    official_valid = bool(official and value and not title_only and navigation_noise == 0)
    valid = standard_valid or official_valid
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
        "official_short_notice_override": bool(official_valid and not standard_valid),
    }


def _news_summary_quality(text: str, title: str, *, official: bool = False) -> tuple[bool, dict[str, Any]]:
    """Validate a syndicated/RSS summary without pretending it is full text."""
    value = remove_boilerplate(text)
    title_norm = normalize_title(title)
    value_norm = normalize_title(value)
    similarity = ratio(title_norm, value_norm) / 100 if title_norm and value_norm else 0.0
    tokens = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", value)
    sentences = split_sentences(value, max_sentences=20)
    standard_valid = (
        len(value) >= int(os.getenv("PIF_NEWS_EXCERPT_MIN_CHARS", "100"))
        and len(tokens) >= 18
        and similarity < 0.90
        and (len(sentences) >= 2 or len(value) >= 220)
    )
    official_valid = bool(official and value and similarity < 0.90)
    valid = standard_valid or official_valid
    return valid, {
        "chars": len(value),
        "tokens": len(tokens),
        "sentences": len(sentences),
        "title_body_similarity": round(similarity, 3),
        "title_only": similarity >= 0.90 and len(value) < 300,
        "official_short_notice_override": bool(official_valid and not standard_valid),
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
        selected_url = canonical_url or page_url
        unresolved_aggregator = bool(
            _is_aggregator_url(page_url)
            and (not selected_url or _is_aggregator_url(selected_url))
        )
        for method, extracted in _extract_news_candidates(raw, soup, page_url):
            valid, score, quality = _news_text_quality(extracted, record.get("title"), official=_is_official_news_record(record))
            identity_ok, identity = _news_content_identity(
                record, extracted, candidate_title, selected_url, profile,
            )
            # Google/Bing aggregation landing pages are discovery surfaces, not
            # publisher bodies.  Unless rendering resolves to a non-aggregator
            # canonical/final URL, their navigation or synopsis must never be
            # promoted to ``full``/``partial`` article content.  The original
            # RSS excerpt remains eligible as ``syndicated_summary`` below.
            provenance_ok = not unresolved_aggregator
            audit["extraction_attempts"].append({
                "url": page_url, "channel": channel, "method": method,
                "status": "valid" if valid and identity_ok and provenance_ok else "rejected",
                "structural_valid": valid,
                "identity_valid": identity_ok,
                "provenance_valid": provenance_ok,
                "rejection_reason": "unresolved_aggregator_landing" if not provenance_ok else "",
                "identity": identity,
                **quality,
            })
            if valid and identity_ok and provenance_ok and score > best_score:
                best_text = extracted
                best_title = candidate_title
                best_method = f"{channel}:{method}"
                best_score = score
                best_quality = quality
                best_identity = identity
                best_url = selected_url

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
    summary_valid, summary_quality = _news_summary_quality(rss_excerpt, record.get("title"), official=_is_official_news_record(record))
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


def _page_identity_candidate(soup: BeautifulSoup, candidate_url: str) -> dict[str, Any]:
    """Extract article-level identity metadata without using reference-list DOIs."""
    doi = clean_space(_meta_content(soup, [
        ("name", "citation_doi"), ("name", "dc.identifier"),
        ("name", "DC.Identifier"), ("property", "og:doi"),
    ])).lower()
    url_doi = clean_space(extract_doi(candidate_url)).lower()
    if not doi and url_doi:
        doi = url_doi
    pmid = clean_space(_meta_content(soup, [("name", "citation_pmid"), ("name", "pmid")]))
    pmcid = clean_space(_meta_content(soup, [("name", "citation_pmcid"), ("name", "pmcid")]))
    title = _meta_content(soup, [
        ("name", "citation_title"), ("name", "dc.title"),
        ("property", "og:title"), ("name", "twitter:title"),
    ]) or clean_space(soup.title.get_text(" ") if soup.title else "")
    authors = unique_strings(
        clean_space(tag.get("content"))
        for tag in soup.find_all("meta", attrs={"name": "citation_author"})
        if tag.get("content")
    )
    journal = _meta_content(soup, [
        ("name", "citation_journal_title"), ("name", "dc.source"),
        ("name", "prism.publicationName"),
    ])
    date_value = _meta_content(soup, [
        ("name", "citation_publication_date"), ("name", "citation_date"),
        ("name", "dc.date"), ("name", "prism.publicationDate"),
    ])
    return {
        "source": "publisher_page_identity",
        "source_ids": {"pmid": pmid or None, "pmcid": pmcid or None},
        "doi": doi or None,
        "title": title,
        "authors": authors,
        "journal": journal,
        "first_publication_date": date_value,
        "published_date": date_value,
        "year": date_value[:4] if len(date_value) >= 4 else None,
        "url": candidate_url,
    }


def _identity_score(
    work: dict[str, Any],
    candidate_text: str,
    candidate_url: str,
    candidate_metadata: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Verify retrieved content using identifiers first, then bibliography and text.

    DOIs found in a reference list are not treated as conflicts. A conflict is
    hard only when article-level metadata or the resolved article URL explicitly
    identifies a different DOI/PMID/PMCID.
    """
    metadata = candidate_metadata or {}
    if any(clean_space(metadata.get(key)) for key in ("title", "doi", "journal")) or any(
        clean_space((metadata.get("source_ids") or {}).get(key)) for key in ("pmid", "pmcid")
    ):
        assessment = assess_completion_identity(work, metadata)
        if assessment.get("status") == "identity_conflict":
            return False, assessment
        if assessment.get("status") == "identity_verified":
            return True, assessment

    expected_title = normalize_title(work.get("title"))
    head = normalize_title(candidate_text[:3500])
    title_score = partial_ratio(expected_title, head) / 100 if expected_title and head else 0.0
    expected_doi = clean_space(work.get("doi")).lower()
    url_doi = clean_space(extract_doi(candidate_url)).lower()
    body_dois = {x.lower().rstrip(".,;)]}") for x in re.findall(
        r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", candidate_text[:5000], flags=re.I
    )}
    doi_match = bool(expected_doi and (expected_doi == url_doi or expected_doi in body_dois))
    doi_conflict = bool(expected_doi and url_doi and expected_doi != url_doi)
    author_match = False
    lower = candidate_text[:12000].lower()
    for author in (work.get("authors") or [])[:6]:
        family = clean_space(author).split(" ")[-1].lower()
        if len(family) > 3 and family in lower:
            author_match = True
            break
    journal = clean_space(work.get("journal")).lower()
    journal_match = bool(journal and journal in lower)
    year = str(work.get("year") or clean_space(work.get("canonical_publication_date"))[:4])
    year_match = bool(year.isdigit() and year in candidate_text[:12000])
    if doi_conflict:
        status = "identity_conflict"
        accepted = False
        reason = "resolved_url_doi_mismatch"
    elif doi_match:
        status = "identity_verified"
        accepted = True
        reason = "expected_doi_present"
    else:
        supports = sum([title_score >= 0.82, author_match, journal_match, year_match])
        accepted = title_score >= 0.88 or (title_score >= 0.72 and supports >= 3)
        status = "identity_verified" if accepted else "identity_uncertain"
        reason = "text_bibliographic_match" if accepted else "insufficient_text_identity_evidence"
    return accepted, {
        "policy_version": "v15.1-multifactor-content-identity-2",
        "status": status,
        "reason": reason,
        "title_score": round(title_score, 3),
        "doi_match": doi_match,
        "doi_conflict": doi_conflict,
        "url_doi": url_doi,
        "body_doi_candidates": sorted(body_dois)[:8],
        "author_match": author_match,
        "journal_match": journal_match,
        "year_match": year_match,
        "candidate_url": candidate_url,
        "metadata_assessment": assessment if 'assessment' in locals() else None,
    }


def _merge_metadata_candidate(work: dict[str, Any], candidate: dict[str, Any], method: str) -> dict[str, Any]:
    """Merge a post-dedup completion only after multifactor identity verification."""
    assessment = merge_verified_candidate(work, candidate, method=method)
    return {
        "method": method,
        "status": assessment.get("status"),
        "reason": assessment.get("reason"),
        "identity": assessment,
    }


def complete_scholarly_metadata(http: HttpClient, work: dict[str, Any], ncbi_api_key: str = "") -> dict[str, Any]:
    """Complete abstract/identifier metadata after cross-provider deduplication."""
    work = dict(work)
    completion_audit: list[dict[str, Any]] = []
    ids = work.get("source_ids") or {}
    pmid = clean_space(ids.get("pmid"))
    doi = clean_space(work.get("doi")).lower()

    if pmid:
        try:
            from .scholarly import _pubmed_fetch  # local import avoids an import cycle
            rows = _pubmed_fetch(http, [pmid], ncbi_api_key)
            if rows:
                completion_audit.append(_merge_metadata_candidate(work, rows[0], "PubMed post-dedup completion"))
            else:
                completion_audit.append({"method": "PubMed post-dedup completion", "status": "not_found"})
        except Exception as exc:
            completion_audit.append({"method": "PubMed post-dedup completion", "status": "failed", "error": clean_space(exc)[:300]})

    query = f"EXT_ID:{pmid}" if pmid else f"DOI:{doi}" if doi else ""
    if query:
        try:
            payload = http.get_json(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": query, "format": "json", "pageSize": 5, "resultType": "core"},
            )
            rows = (payload.get("resultList") or {}).get("result") or []
            if rows:
                row = rows[0]
                candidate = {
                    "source": "Europe PMC post-dedup completion",
                    "source_ids": {"pmid": row.get("pmid"), "pmcid": row.get("pmcid")},
                    "doi": clean_space(row.get("doi")).lower() or None,
                    "title": clean_space(row.get("title")),
                    "abstract": clean_space(row.get("abstractText")),
                    "authors": unique_strings(str(row.get("authorString") or "").split(",")),
                    "journal": clean_space(row.get("journalTitle")),
                    "year": row.get("pubYear"),
                    "first_publication_date": row.get("firstPublicationDate"),
                    "published_date": row.get("firstPublicationDate"),
                    "publication_types": unique_strings(row.get("pubTypeList") or []),
                    "url": f"https://europepmc.org/article/MED/{row.get('pmid')}" if row.get("pmid") else "",
                }
                completion_audit.append(_merge_metadata_candidate(work, candidate, "Europe PMC post-dedup completion"))
            else:
                completion_audit.append({"method": "Europe PMC post-dedup completion", "status": "not_found"})
        except Exception as exc:
            completion_audit.append({"method": "Europe PMC post-dedup completion", "status": "failed", "error": clean_space(exc)[:300]})

    if doi:
        try:
            payload = http.get_json(f"https://api.crossref.org/works/{doi}")
            row = payload.get("message") or {}
            title = clean_space(" ".join(row.get("title") or []))
            authors = []
            for author in row.get("author") or []:
                name = clean_space(f"{author.get('given') or ''} {author.get('family') or ''}")
                if name:
                    authors.append(name)
            candidate = {
                "source": "Crossref post-dedup completion",
                "source_ids": {},
                "doi": clean_space(row.get("DOI")).lower() or doi,
                "title": title,
                "abstract": strip_tags(row.get("abstract") or ""),
                "authors": authors,
                "journal": clean_space(" ".join(row.get("container-title") or [])),
                "year": ((row.get("published-online") or row.get("published") or {}).get("date-parts") or [[None]])[0][0],
                "publication_types": [row.get("type")] if row.get("type") else [],
                "url": clean_space(row.get("URL")),
            }
            completion_audit.append(_merge_metadata_candidate(work, candidate, "Crossref post-dedup completion"))
        except Exception as exc:
            completion_audit.append({"method": "Crossref post-dedup completion", "status": "failed", "error": clean_space(exc)[:300]})

        try:
            payload = http.get_json(f"https://api.openalex.org/works/https://doi.org/{doi}")
            abstract_index = payload.get("abstract_inverted_index") or {}
            tokens: list[tuple[int, str]] = []
            for token, positions in abstract_index.items():
                for position in positions or []:
                    tokens.append((int(position), str(token)))
            abstract = " ".join(token for _, token in sorted(tokens))
            candidate = {
                "source": "OpenAlex post-dedup completion",
                "source_ids": {"pmid": clean_space(((payload.get("ids") or {}).get("pmid") or "").split("/")[-1])},
                "doi": clean_space(((payload.get("ids") or {}).get("doi") or doi)).replace("https://doi.org/", "").lower(),
                "title": clean_space(payload.get("title")),
                "abstract": clean_space(abstract),
                "authors": [clean_space(((x.get("author") or {}).get("display_name"))) for x in payload.get("authorships") or [] if clean_space(((x.get("author") or {}).get("display_name")))],
                "journal": clean_space((((payload.get("primary_location") or {}).get("source") or {}).get("display_name"))),
                "year": payload.get("publication_year"),
                "published_date": payload.get("publication_date"),
                "publication_types": [payload.get("type")] if payload.get("type") else [],
                "url": clean_space(((payload.get("primary_location") or {}).get("landing_page_url"))),
            }
            completion_audit.append(_merge_metadata_candidate(work, candidate, "OpenAlex post-dedup completion"))
        except Exception as exc:
            completion_audit.append({"method": "OpenAlex post-dedup completion", "status": "failed", "error": clean_space(exc)[:300]})

    work["metadata_completion_audit"] = completion_audit
    return work


def _full_text_excerpt(work: dict[str, Any], max_chars: int = 1800) -> str:
    """Build a traceable abstract-like English excerpt from verified full text."""
    sections = work.get("full_text_sections") or {}
    ordered = []
    if isinstance(sections, dict):
        for key in ("abstract", "introduction", "methods", "results", "discussion", "conclusion", "full_text", "other"):
            value = clean_space(sections.get(key))
            if value:
                ordered.append(value)
    source = clean_space(" ".join(ordered) or work.get("full_text"))
    sentences = split_sentences(source, max_sentences=80)
    selected: list[str] = []
    priorities = (
        re.compile(r"\b(we (?:conducted|used|analy[sz]ed|found|observed|identified)|methods?|results?|findings?|conclusion|study|review)\b", re.I),
        re.compile(r"\b(hazard ratio|odds ratio|confidence interval|\d+(?:\.\d+)?%|p\s*[=<])\b", re.I),
    )
    for pattern in priorities:
        for sentence in sentences:
            if sentence not in selected and pattern.search(sentence):
                selected.append(sentence)
                if len(clean_space(" ".join(selected))) >= max_chars * 0.65:
                    break
        if len(clean_space(" ".join(selected))) >= max_chars * 0.65:
            break
    for sentence in sentences:
        if sentence not in selected:
            selected.append(sentence)
        if len(clean_space(" ".join(selected))) >= max_chars:
            break
    return truncate(clean_space(" ".join(selected)), max_chars)


def complete_scholarly_work(
    http: HttpClient,
    work: dict[str, Any],
    mailto: str,
    ncbi_api_key: str = "",
    max_chars: int = 18000,
) -> dict[str, Any]:
    completed = complete_scholarly_metadata(http, work, ncbi_api_key)
    return enrich_scholarly_work(http, completed, mailto, max_chars=max_chars)


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
    if doi:
        if mailto:
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
        # DOI resolution is public and must not depend on an Unpaywall email.
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
            candidate_metadata: dict[str, Any] = {"url": final_url}
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
                xml_soup = BeautifulSoup(response.text, "xml")
                candidate_metadata = _page_identity_candidate(xml_soup, final_url)
                article_doi = xml_soup.find("article-id", attrs={"pub-id-type": "doi"})
                article_pmid = xml_soup.find("article-id", attrs={"pub-id-type": "pmid"})
                article_pmcid = xml_soup.find("article-id", attrs={"pub-id-type": "pmcid"})
                candidate_metadata["doi"] = clean_space(article_doi.get_text(" ") if article_doi else candidate_metadata.get("doi")).lower() or None
                candidate_metadata["source_ids"] = {
                    "pmid": clean_space(article_pmid.get_text(" ") if article_pmid else "") or None,
                    "pmcid": clean_space(article_pmcid.get_text(" ") if article_pmcid else "") or None,
                }
            else:
                raw = response.text
                sniff = raw.lstrip()[:500].lower()
                if sniff.startswith("<?xml") or sniff.startswith("<article") or "<article " in sniff:
                    sections = _jats_sections(raw)
                    text = clean_space(" ".join(sections.values()))
                    parse_method = "jats_xml_sniffed"
                    candidate_metadata = _page_identity_candidate(BeautifulSoup(raw, "xml"), final_url)
                else:
                    extracted = trafilatura.extract(raw, include_comments=False, include_tables=True, favor_recall=True) or ""
                    soup = BeautifulSoup(raw, "lxml")
                    abstract = _meta_content(soup, [("name", "citation_abstract"), ("name", "description"), ("property", "og:description")])
                    text = clean_space(extracted or abstract)
                    sections = {"full_text": text} if text else {}
                    parse_method = "publisher_html"
                    candidate_metadata = _page_identity_candidate(soup, final_url)
            payload_quality = classify_scholarly_payload(
                text, status_code=getattr(response, "status_code", None), content_type=content_type, url=final_url
            )
            if not payload_quality.get("valid"):
                attempt.update({"status": "invalid_page", "payload_quality": payload_quality, "chars": len(text), "parse_method": parse_method})
                audit["attempts"].append(attempt)
                continue
            accepted, identity = _identity_score(work, text, final_url, candidate_metadata)
            attempt_status = "accepted" if accepted else identity.get("status") or "identity_rejected"
            attempt.update({"status": attempt_status, "identity": identity, "payload_quality": payload_quality, "chars": len(text), "parse_method": parse_method})
            audit["attempts"].append(attempt)
            register_identity_assessment(work, identity, method=f"{method}:{final_url}")
            if not accepted or work.get("identifier_conflict"):
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
    if work.get("full_text") and not clean_space(work.get("abstract")):
        work["full_text_excerpt"] = _full_text_excerpt(work)
        work["full_text_excerpt_source"] = {
            "method": best_method,
            "url": best_url,
            "identity_status": clean_space(work.get("content_identity_status")) or "identity_verified",
        }
    work["full_text_method"] = best_method
    work["full_text_url"] = best_url
    work["evidence_level"] = "E2" if work.get("full_text") else ("E1" if work.get("abstract") else "E0")
    work["content_audit"] = audit
    work["content_completion_status"] = "evidence_ready" if work.get("abstract") or work.get("full_text") else "metadata_only"
    return work

