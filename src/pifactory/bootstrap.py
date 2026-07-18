from __future__ import annotations

import json
from typing import Any

from .authority_sources import fetch_authoritative_documents, source_bundle_hash
from .config import Settings, load_seed
from .http import HttpClient
from .llm import LLMError, LLMRouter
from .profile_contract import deterministic_profile, merge_llm_refinement, validate_profile
from .query_plan import compile_profile_queries
from .utils import dump_json, sha256_text, utc_now_iso


def _fallback_profile(seed: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Backward-compatible name used by demo tests and the pipeline."""
    if not sources:
        sources = [
            {
                "url": item.get("url"),
                "name": item.get("name"),
                "organization": item.get("organization"),
                "role": item.get("role"),
                "usable": True,
                "sha256": "deterministic-demo-source",
                "cache_status": "demo_stub",
            }
            for item in seed.get("authoritative_sources") or []
            if isinstance(item, dict) and item.get("url")
        ]
    profile = deterministic_profile(seed, sources)
    profile = compile_profile_queries(profile)
    valid, issues = validate_profile(profile, seed)
    if not valid:
        profile["status"] = "failed"
        profile["blocking_issues"] = issues
    return profile


def _llm_validator(data: Any) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "output is not a JSON object"
    if str(data.get("schema_version")) != "3.0":
        return False, "schema_version must be 3.0"
    vocabulary = data.get("vocabulary") or {}
    if not isinstance(vocabulary, dict):
        return False, "missing vocabulary object"
    if not vocabulary.get("identity_anchor_terms"):
        return False, "missing identity_anchor_terms"
    if not isinstance(data.get("validation"), dict):
        return False, "missing validation report"
    if data.get("status") not in {"ready", "needs_review", "failed"}:
        return False, "invalid status"
    return True, "ok"


def build_profile(settings: Settings, http: HttpClient, llm: LLMRouter) -> dict[str, Any]:
    seed = load_seed(settings.project_root, settings.profile_id)
    seed_hash = sha256_text(json.dumps(seed, ensure_ascii=False, sort_keys=True))
    documents = fetch_authoritative_documents(settings, seed, http)
    base = deterministic_profile(seed, documents)
    usable = [x for x in documents if x.get("usable") and x.get("text")]
    minimum = int((seed.get("source_policy") or {}).get("minimum_usable_sources", 1))

    profile = base
    if llm.available and len(usable) >= minimum:
        system = (settings.project_root / "prompts" / "profile_bootstrap.md").read_text(encoding="utf-8")
        prompt_payload = {
            "profile_id": settings.profile_id,
            "manual_topic_contract": seed,
            "authoritative_source_documents": [
                {
                    "url": x["url"],
                    "organization": x.get("organization"),
                    "role": x.get("role"),
                    "sha256": x.get("sha256"),
                    "text": x.get("text", "")[:24000],
                }
                for x in usable
            ],
            "execution_constraints": {
                "web_search_allowed": False,
                "model_memory_completion_allowed": False,
                "scope_expansion_allowed": False,
                "final_query_compilation_is_deterministic": True,
                "return_json_only": True,
            },
        }
        try:
            result = llm.json_task(
                system=system,
                prompt=json.dumps(prompt_payload, ensure_ascii=False),
                validator=_llm_validator,
                max_models_per_provider=2,
                temperature=0.0,
            )
            proposal = dict(result.data)
            proposal["generated_by"] = f"{result.provider}:{result.model}"
            profile = merge_llm_refinement(base, proposal, seed)
            profile["llm_attempts"] = result.attempts
        except LLMError as exc:
            profile["llm_failure"] = str(exc)[:2000]
            profile["generated_by"] = "deterministic_seed_contract_after_llm_failure"
    elif len(usable) < minimum:
        profile["source_warning"] = f"usable sources {len(usable)} < requested minimum {minimum}; deterministic seed contract used"

    profile["profile_id"] = settings.profile_id
    profile["seed_hash"] = seed_hash
    profile["source_bundle_hash"] = source_bundle_hash(documents)
    profile["generated_at"] = utc_now_iso()
    profile["profile_contract"] = "strict-virus-retrieval-profile/v3"
    profile = compile_profile_queries(profile)
    valid, issues = validate_profile(profile, seed)
    if not valid:
        profile["status"] = "failed"
        profile.setdefault("blocking_issues", []).extend(x for x in issues if x not in profile.get("blocking_issues", []))
    elif profile.get("status") != "needs_review":
        profile["status"] = "ready"

    target = settings.state_dir.parent / "profiles" / settings.profile_id / "profile.json"
    dump_json(target, profile)
    dump_json(target.parent / "source_documents.json", [
        {k: x.get(k) for k in ("url", "name", "organization", "role", "retrieved_at", "sha256", "usable", "cache_status", "failure_reason", "fetch_failure")}
        for x in documents
    ])
    return profile
