from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import fitz
import trafilatura
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import partial_ratio, ratio, token_set_ratio

from .http import HttpClient
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


def _candidate_news_urls(record: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for value in (
        record.get("resolved_url"), record.get("url"), record.get("source_url"),
        record.get("canonical_url"), record.get("link"), record.get("original_url"),
        record.get("publisher_url"),
    ):
        if value:
            urls.append(clean_space(value))
    urls.extend(clean_space(x) for x in (record.get("candidate_urls") or []) if clean_space(x))
    for duplicate in record.get("duplicate_sources") or []:
        if isinstance(duplicate, dict) and duplicate.get("url"):
            urls.append(clean_space(duplicate.get("url")))
    # Prefer direct publisher URLs over aggregators while retaining the latter
    # as discovery pages that may expose canonical/article links.
    return sorted(unique_strings(urls), key=lambda x: (1 if _is_aggregator_url(x) else 0, len(x)))


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


def _external_news_urls(soup: BeautifulSoup, raw: str, base_url: str) -> list[str]:
    """Discover publisher article URLs from aggregator/landing pages."""
    candidates: list[str] = []
    for attr, value in (("property", "og:url"), ("name", "twitter:url"), ("name", "citation_public_url")):
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            candidates.append(urljoin(base_url, clean_space(tag.get("content"))))
    canonical = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
    if canonical and canonical.get("href"):
        candidates.append(urljoin(base_url, clean_space(canonical.get("href"))))
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
                for key in ("url", "mainEntityOfPage"):
                    value = item.get(key)
                    if isinstance(value, dict):
                        value = value.get("@id") or value.get("url")
                    if isinstance(value, str):
                        candidates.append(urljoin(base_url, value))
    for anchor in soup.find_all("a", href=True):
        href = _decode_embedded_url(urljoin(base_url, anchor.get("href")))
        if href.startswith(("http://", "https://")):
            candidates.append(href)
    # Some aggregators embed escaped article URLs in scripts. Keep only normal
    # HTTP URLs and filter obvious assets/social/navigation domains.
    for found in re.findall(r'https?://[^"\'<>\s]+', raw):
        candidates.append(found.replace("\u0026", "&").replace("\\/", "/"))
    blocked = (
        "news.google.", "google.com/", "googleusercontent.com", "gstatic.com",
        "bing.com/", "microsoft.com/", "facebook.com/", "twitter.com/",
        "x.com/", "youtube.com/", "doubleclick.net/",
    )
    base_host = urlparse(base_url).netloc.lower()
    output: list[str] = []
    for candidate in unique_strings(candidates):
        decoded = _decode_embedded_url(candidate)
        try:
            parsed = urlparse(decoded)
        except Exception:
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        lower = decoded.lower()
        if any(token in lower for token in blocked):
            continue
        if parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".js")):
            continue
        # Prefer external publisher pages; same-host links are still useful for
        # non-aggregator source sites.
        if parsed.netloc.lower() != base_host or not _is_aggregator_url(base_url):
            output.append(decoded)
    return unique_strings(output)[:20]


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
        len(value) >= 140
        and len(tokens) >= 24
        and similarity < 0.90
        and (len(sentences) >= 2 or len(value) >= 260)
    )
    return valid, {
        "chars": len(value),
        "tokens": len(tokens),
        "sentences": len(sentences),
        "title_body_similarity": round(similarity, 3),
        "title_only": similarity >= 0.90 and len(value) < 300,
    }


def _extract_news_candidates(raw: str, soup: BeautifulSoup) -> list[tuple[str, str]]:
    jsonld_title, jsonld_body = _extract_jsonld(soup)
    candidates: list[tuple[str, str]] = []
    if jsonld_body:
        candidates.append(("jsonld_articleBody", jsonld_body))
    precision = trafilatura.extract(
        raw,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    ) or ""
    if precision:
        candidates.append(("trafilatura_precision", precision))
    recall = trafilatura.extract(
        raw,
        include_comments=False,
        include_tables=False,
        favor_recall=True,
    ) or ""
    if recall:
        candidates.append(("trafilatura_recall", recall))
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


def resolve_and_extract_news(http: HttpClient, record: dict[str, Any], max_chars: int = 18000) -> dict[str, Any]:
    audit: dict[str, Any] = {
        "attempted_urls": [],
        "extraction_attempts": [],
        "retrieved_at": utc_now_iso(),
    }
    queue = _candidate_news_urls(record)
    visited: set[str] = set()
    best_text = ""
    best_title = clean_space(record.get("title"))
    best_method = "none"
    best_score = float("-inf")
    best_quality: dict[str, Any] = {}
    final_url = clean_space(record.get("url"))

    while queue and len(visited) < 10:
        url = queue.pop(0)
        if not url or url in visited:
            continue
        visited.add(url)
        audit["attempted_urls"].append(url)
        try:
            response = http.request("GET", url, allow_redirects=True, timeout=25, retry_attempts=2)
            final_url = response.url
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "text" not in content_type:
                audit["extraction_attempts"].append({"url": url, "status": "unsupported_content_type", "content_type": content_type})
                continue
            raw = response.text
            soup = BeautifulSoup(raw, "lxml")
            jsonld_title, _ = _extract_jsonld(soup)
            candidate_title = jsonld_title or _meta_content(
                soup,
                [("property", "og:title"), ("name", "twitter:title"), ("name", "citation_title")],
            )
            if candidate_title:
                best_title = candidate_title
            canonical = soup.find("link", rel=lambda x: x and "canonical" in str(x).lower())
            canonical_url = ""
            if canonical and canonical.get("href"):
                canonical_url = urljoin(final_url, canonical.get("href"))
                record["canonical_url"] = canonical_url
                if canonical_url not in visited and canonical_url not in queue:
                    queue.append(canonical_url)
            discovered = _external_news_urls(soup, raw, final_url)
            for candidate_url in discovered:
                if candidate_url not in visited and candidate_url not in queue:
                    queue.append(candidate_url)
            if discovered:
                audit.setdefault("discovered_urls", []).extend(discovered)
            for method, extracted in _extract_news_candidates(raw, soup):
                valid, score, quality = _news_text_quality(extracted, best_title or record.get("title"))
                audit["extraction_attempts"].append(
                    {
                        "url": final_url,
                        "method": method,
                        "status": "valid" if valid else "rejected",
                        **quality,
                    }
                )
                if valid and score > best_score:
                    best_text = extracted
                    best_method = method
                    best_score = score
                    best_quality = quality
                    record["canonical_url"] = canonical_url or final_url
            if best_text and len(best_text) >= 2200:
                break
        except Exception as exc:
            audit.setdefault("errors", []).append({"url": url, "error": clean_space(exc)[:400]})

    rss_excerpt = remove_boilerplate(record.get("excerpt") or "")
    summary_valid, summary_quality = _news_summary_quality(rss_excerpt, record.get("title"))
    if best_text:
        content_status = "full" if len(best_text) >= 1500 else "partial"
        content = truncate(best_text, max_chars)
    elif summary_valid:
        # A substantive syndicated summary is useful evidence when the original
        # site blocks automated extraction. It is clearly labelled and never
        # represented as full original body text. This prevents a complete news
        # blackout while preserving provenance and uncertainty.
        content_status = "syndicated_summary"
        content = truncate(rss_excerpt, min(max_chars, 6000))
        best_method = "rss_syndicated_summary"
        best_quality = summary_quality
    elif rss_excerpt:
        content_status = "title_only_rejected" if summary_quality.get("title_only") else "excerpt_only"
        content = ""
        best_method = "rss_excerpt_not_substantive"
        best_quality = summary_quality
    else:
        content_status = "unavailable"
        content = ""

    record["resolved_url"] = final_url
    record["retrieved_at"] = audit["retrieved_at"]
    record["content_title"] = best_title
    record["content"] = content
    record["content_status"] = content_status
    record["content_method"] = best_method
    record["content_hash"] = sha256_text(content) if content else None
    record["title_body_similarity"] = best_quality.get("title_body_similarity")
    record["content_audit"] = {**audit, "selected_quality": best_quality, "provenance": content_status}
    return record


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

