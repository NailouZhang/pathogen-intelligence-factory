from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .authority_discovery import discover_authoritative_urls
from .config import Settings, load_seed
from .http import HttpClient
from .llm import LLMError, LLMRouter
from .utils import clean_space, dump_json, sha256_text, unique_strings, utc_now_iso


def _html_to_text(raw: str, limit: int = 18000) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "form"]):
        tag.decompose()
    text = clean_space(soup.get_text(" "))
    return text[:limit]


def _fetch_authoritative_context(settings: Settings, seed: dict[str, Any], http: HttpClient) -> list[dict[str, Any]]:
    secrets = settings.secrets
    discovered = discover_authoritative_urls(
        seed,
        http,
        google_api_key=secrets.get("GOOGLE_CSE_API_KEY", ""),
        google_cse_id=secrets.get("GOOGLE_CSE_ID", ""),
    )
    records: list[dict[str, Any]] = []
    for row in discovered:
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        try:
            raw = http.get_text(url)
            text = _html_to_text(raw)
            if len(text) < 200:
                continue
            records.append({
                "url": url,
                "title": row.get("title"),
                "snippet": row.get("snippet"),
                "discovery_method": row.get("discovery_method"),
                "retrieved_at": utc_now_iso(),
                "text": text,
                "content_hash": sha256_text(text),
            })
        except Exception as exc:
            records.append({
                "url": url,
                "discovery_method": row.get("discovery_method"),
                "error": clean_space(exc)[:300],
            })
    return records


def _fallback_profile(seed: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    terms = unique_strings(
        list(seed.get("seed_terms") or [])
        + list(seed.get("virus_names") or [])
        + list(seed.get("disease_names_en") or [])
        + list(seed.get("disease_names_zh") or [])
    )
    en_terms = [t for t in terms if not re.search(r"[\u4e00-\u9fff]", t)]
    zh_terms = [t for t in terms if re.search(r"[\u4e00-\u9fff]", t)]
    anchor = en_terms[0] if en_terms else seed.get("profile_id", "pathogen")
    return {
        "schema_version": "2.1",
        "profile_id": seed.get("profile_id") or anchor.lower().replace(" ", "_"),
        "display_name_en": seed.get("display_name_en") or anchor,
        "display_name_zh": seed.get("display_name_zh") or anchor,
        "taxonomy": seed.get("taxonomy") or {},
        "english_terms": en_terms,
        "chinese_terms": zh_terms,
        "virus_names": unique_strings(seed.get("virus_names") or en_terms),
        "disease_names_en": unique_strings(seed.get("disease_names_en") or []),
        "disease_names_zh": unique_strings(seed.get("disease_names_zh") or []),
        "accepted_names": unique_strings(seed.get("accepted_names") or seed.get("virus_names") or en_terms),
        "historical_names": unique_strings(seed.get("historical_names") or []),
        "hosts": unique_strings(seed.get("hosts") or []),
        "vectors": unique_strings(seed.get("vectors") or []),
        "reservoirs": unique_strings(seed.get("reservoirs") or []),
        "transmission_terms": unique_strings(seed.get("transmission_terms") or []),
        "clinical_terms": unique_strings(seed.get("clinical_terms") or []),
        "epidemiology_terms": unique_strings(seed.get("epidemiology_terms") or []),
        "geography_terms": unique_strings(seed.get("geography_terms") or []),
        "genes_proteins": unique_strings(seed.get("genes_proteins") or []),
        "genome_terms": unique_strings(seed.get("genome_terms") or []),
        "diagnostics_terms": unique_strings(seed.get("diagnostics_terms") or []),
        "prevention_terms": unique_strings(seed.get("prevention_terms") or []),
        "negative_terms": unique_strings(seed.get("negative_terms") or []),
        "translation_glossary": seed.get("translation_glossary") or [],
        "query_groups": seed.get("query_groups") or [
            {"id": "core_taxonomy", "terms": en_terms[:8], "topics": ["taxonomy", "species", "lineage"]},
            {"id": "clinical", "terms": en_terms[:6], "topics": ["infection", "disease", "severity", "treatment"]},
            {"id": "epidemiology", "terms": en_terms[:6], "topics": ["outbreak", "surveillance", "epidemiology", "case"]},
            {"id": "host_ecology", "terms": en_terms[:6], "topics": ["reservoir", "host", "vector", "ecology", "spillover"]},
            {"id": "genomics", "terms": en_terms[:6], "topics": ["genome", "sequence", "phylogeny", "variant"]},
            {"id": "diagnostics", "terms": en_terms[:6], "topics": ["diagnosis", "PCR", "serology", "assay"]},
            {"id": "prevention", "terms": en_terms[:6], "topics": ["vaccine", "prevention", "control", "public health"]},
        ],
        "authoritative_sources": sources,
        "generated_by": "deterministic_seed_fallback",
        "generated_at": utc_now_iso(),
    }


def build_profile(settings: Settings, http: HttpClient, llm: LLMRouter) -> dict[str, Any]:
    seed = load_seed(settings.project_root, settings.profile_id)
    seed_hash = sha256_text(json.dumps(seed, ensure_ascii=False, sort_keys=True))
    sources = _fetch_authoritative_context(settings, seed, http)
    fallback = _fallback_profile(seed, sources)
    usable = [record for record in sources if record.get("text")]
    if not usable or not llm.available:
        profile = fallback
    else:
        prompt_path = settings.project_root / "prompts" / "profile_bootstrap.md"
        system = prompt_path.read_text(encoding="utf-8")
        context = [{"url": r["url"], "text": r["text"][:10000]} for r in usable]
        prompt = json.dumps({
            "profile_id_seed": settings.profile_id,
            "manual_seed": seed,
            "authoritative_page_text": context,
            "required_output_language": "Bilingual English and Simplified Chinese",
        }, ensure_ascii=False)

        def validator(data: Any) -> tuple[bool, str]:
            if not isinstance(data, dict):
                return False, "not an object"
            english_terms = data.get("english_terms") or []
            query_groups = data.get("query_groups") or []
            glossary = data.get("translation_glossary") or []
            if len(english_terms) < 8:
                return False, "fewer than 8 professional English terms"
            if len(query_groups) < 5:
                return False, "fewer than 5 query groups"
            if not glossary:
                return False, "missing translation_glossary"
            required_group_tokens = {"core", "clinical", "epidemi", "ecolog", "genom"}
            ids = " ".join(str(x.get("id", "")).lower() for x in query_groups if isinstance(x, dict))
            if sum(token in ids for token in required_group_tokens) < 4:
                return False, "query groups do not cover core domains"
            return True, "ok"

        try:
            result = llm.json_task(system=system, prompt=prompt, validator=validator, max_models_per_provider=2)
            profile = dict(fallback)
            profile.update(result.data)
            profile["profile_id"] = settings.profile_id
            profile["authoritative_sources"] = sources
            profile["generated_by"] = f"{result.provider}:{result.model}"
            profile["llm_attempts"] = result.attempts
            profile["generated_at"] = utc_now_iso()
        except LLMError:
            profile = fallback
    profile["seed_hash"] = seed_hash
    profile["profile_contract"] = "strict-pathogen-profile/v1"
    target = settings.state_dir.parent / "profiles" / settings.profile_id / "profile.json"
    dump_json(target, profile)
    return profile
