from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .config import Settings
from .http import HttpClient
from .utils import clean_space, dump_json, load_json, sha256_text, utc_now_iso


class AuthoritySourceError(RuntimeError):
    pass


def _cache_name(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".json"


def _safe_public_https_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return False
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"}:
        return False
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        # DNS may be temporarily unavailable in offline validation. The URL is
        # still structurally safe and the fetch layer will record the failure.
        return True
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def html_to_authority_text(raw: str, limit: int = 30000) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "form", "noscript", "svg"]):
        tag.decompose()
    for selector in ("header", ".cookie", "#cookie", ".navigation", ".footer", ".sidebar"):
        for node in soup.select(selector):
            node.decompose()
    text = clean_space(soup.get_text(" "))
    return text[:limit]


def configured_authority_sources(seed: dict[str, Any]) -> list[dict[str, Any]]:
    sources = seed.get("authoritative_sources") or []
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(sources, start=1):
        if isinstance(item, str):
            item = {"name": f"source-{index}", "url": item}
        if not isinstance(item, dict):
            continue
        url = clean_space(item.get("url"))
        if not url or url in seen:
            continue
        if not _safe_public_https_url(url):
            raise AuthoritySourceError(f"不安全或无效的权威来源 URL：{url}")
        seen.add(url)
        output.append({
            "name": clean_space(item.get("name")) or f"source-{index}",
            "organization": clean_space(item.get("organization")),
            "role": clean_space(item.get("role")) or "supporting",
            "url": url,
            "required": bool(item.get("required", False)),
        })
    return output


def fetch_authoritative_documents(
    settings: Settings,
    seed: dict[str, Any],
    http: HttpClient,
) -> list[dict[str, Any]]:
    """Fetch only the exact URLs declared in seed.yaml.

    No search engine, site search, result-page scraping, or model browsing is
    performed. Successful snapshots are cached and reused when a source is
    temporarily unavailable.
    """
    sources = configured_authority_sources(seed)
    policy = seed.get("source_policy") or {}
    if policy.get("allow_search_discovery") is not False:
        raise AuthoritySourceError("source_policy.allow_search_discovery 必须明确为 false")
    cache_root = settings.state_dir.parent / "profiles" / settings.profile_id / "source-cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    output: list[dict[str, Any]] = []

    for source in sources:
        cache_file = cache_root / _cache_name(source["url"])
        cached = load_json(cache_file, {}) or {}
        record = dict(source)
        try:
            raw = http.get_text(source["url"], timeout=45)
            text = html_to_authority_text(raw)
            if len(text) < 160:
                raise AuthoritySourceError("正文清洗后不足 160 个字符")
            record.update({
                "usable": True,
                "retrieved_at": utc_now_iso(),
                "text": text,
                "sha256": sha256_text(text),
                "cache_status": "refreshed",
                "failure_reason": None,
            })
            dump_json(cache_file, record)
        except Exception as exc:
            if policy.get("use_cache_on_fetch_failure", True) and cached.get("text"):
                record.update(cached)
                record.update({
                    "usable": True,
                    "cache_status": "cached_after_fetch_failure",
                    "fetch_failure": clean_space(exc)[:500],
                })
            else:
                record.update({
                    "usable": False,
                    "retrieved_at": utc_now_iso(),
                    "text": "",
                    "sha256": "",
                    "cache_status": "unavailable",
                    "failure_reason": clean_space(exc)[:500],
                })
        output.append(record)

    required_failures = [x["url"] for x in output if x.get("required") and not x.get("usable")]
    if required_failures:
        raise AuthoritySourceError(f"必需权威来源不可用：{required_failures}")
    return output


def source_bundle_hash(documents: list[dict[str, Any]]) -> str:
    payload = [
        {"url": x.get("url"), "sha256": x.get("sha256"), "usable": x.get("usable")}
        for x in documents
    ]
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
