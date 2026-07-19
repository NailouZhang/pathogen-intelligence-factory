#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pifactory.http import HttpClient
from pifactory.llm import LLMError, LLMRouter
from pifactory.utils import dump_json, utc_now_iso


ROWS = [
    ("CROSSREF_MAILTO", "recommended", "Crossref polite pool and contact identity"),
    ("UNPAYWALL_EMAIL", "recommended", "legal open-access location lookup; may equal CROSSREF_MAILTO"),
    ("NCBI_API_KEY", "recommended", "higher PubMed E-utilities request allowance"),
    ("GEMINI_API_KEY", "analysis_provider", "Gemini structured-analysis provider"),
    ("GROQ_API_KEY", "analysis_provider", "Groq OpenAI-compatible structured-analysis provider"),
    ("OPENROUTER_API_KEY", "analysis_provider", "OpenRouter free-model router and rescue provider"),
    ("MISTRAL_API_KEY", "analysis_provider", "Mistral structured-analysis provider"),
    ("SILICONFLOW_API_KEY", "analysis_provider", "SiliconFlow structured-analysis provider with balance check"),
    ("BIGMODEL_API_KEY", "analysis_provider", "Zhipu BigModel structured-analysis provider; defaults to free GLM-4.7-Flash"),
    ("DEEPSEEK_API_KEY", "analysis_provider", "DeepSeek structured-analysis provider with optional granted-balance-only guard"),
    ("OPENALEX_API_KEY", "required_for_openalex", "OpenAlex currently requires an API key"),
    ("SEMANTIC_SCHOLAR_API_KEY", "optional_but_recommended", "anonymous retrieval remains enabled with conservative pacing"),
    ("RELIEFWEB_APPNAME", "pending_approval", "pre-approved ReliefWeb appname; a bundled default is available"),
    ("PUBLISHER_REPO_TOKEN", "required_for_wechat_dispatch", "repository_dispatch to the private publisher repository"),
]


def _probe_validator(data: Any) -> tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "probe response is not an object"
    return (str(data.get("status", "")).lower() == "ok", "status must equal ok")


def _probe_provider(router: LLMRouter, provider: str) -> dict[str, Any]:
    account = router.provider_account_info(provider)
    try:
        result = router.json_task(
            system="Return a tiny JSON object and no prose.",
            prompt='Return exactly {"status":"ok"}.',
            provider_order=(provider,),
            validator=_probe_validator,
            max_models_per_provider=1,
            temperature=0.0,
            task_name="credential_preflight",
        )
        return {
            "provider": provider,
            "status": "passed",
            "model": result.model,
            "account": account,
            "attempts": result.attempts,
        }
    except LLMError as exc:
        category = getattr(exc, "category", "unknown")
        action_hint = ""
        if category == "authentication_failed":
            secret_name = {
                "gemini": "GEMINI_API_KEY",
                "groq": "GROQ_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "mistral": "MISTRAL_API_KEY",
                "siliconflow": "SILICONFLOW_API_KEY",
                "bigmodel": "BIGMODEL_API_KEY",
                "deepseek": "DEEPSEEK_API_KEY",
            }.get(provider, "provider API key")
            action_hint = f"Regenerate the provider key and replace GitHub Secret {secret_name}."
        elif category == "quota_exhausted":
            action_hint = "Wait for quota reset or enable another configured provider."
        elif category == "rate_limited":
            action_hint = "Provider is in temporary cooldown; the router will use another provider."
        return {
            "provider": provider,
            "status": "failed" if category != "no_provider_configured" else "not_configured",
            "failure_category": category,
            "error": str(exc)[:700],
            "action_hint": action_hint,
            "account": account,
            "attempts": getattr(exc, "attempts", []) or [],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit credentials without printing secret values.")
    parser.add_argument("--probe-llm", action="store_true", help="send a minimal JSON request to each configured LLM provider")
    parser.add_argument("--analysis-only", action="store_true", help="return status based only on multi-provider analysis readiness")
    parser.add_argument("--require-analysis-provider", action="store_true", help="return non-zero unless at least one LLM provider passes")
    parser.add_argument("--json-out", default="", help="write a machine-readable safe audit JSON file")
    args = parser.parse_args()

    print("Credential/readiness audit (values are never printed)\n")
    rows: list[dict[str, Any]] = []
    missing_required = 0
    for name, level, purpose in ROWS:
        configured = bool(os.getenv(name, "").strip())
        if name == "RELIEFWEB_APPNAME" and not configured:
            configured = True
            note = "default configured; approval may still be pending"
        else:
            note = "configured" if configured else "not configured"
        print(f"[{note:44}] {name:28} {level:28} {purpose}")
        rows.append({"name": name, "level": level, "purpose": purpose, "configured": configured, "note": note})
        if level.startswith("required") and not configured:
            missing_required += 1

    provider_names = ("gemini", "groq", "openrouter", "mistral", "siliconflow", "bigmodel", "deepseek")
    provider_env = {
        "gemini": "GEMINI_API_KEY",
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "bigmodel": "BIGMODEL_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    configured_analysis = [name for name in provider_names if os.getenv(provider_env[name], "").strip()]
    probes: list[dict[str, Any]] = []
    passed_providers: list[str] = []

    if args.probe_llm:
        print("\nLive LLM JSON preflight:")
        router = LLMRouter(HttpClient(os.getenv("PIF_USER_AGENT", "pathogen-intelligence-factory/llm-preflight")))
        for provider in provider_names:
            result = _probe_provider(router, provider)
            probes.append(result)
            if result["status"] == "passed":
                passed_providers.append(provider)
                print(f"[passed] {provider}: {result.get('model')}")
            elif result["status"] == "not_configured":
                print(f"[not configured] {provider}")
            else:
                print(f"[failed] {provider}: {result.get('failure_category')} - {result.get('error')}")
                if result.get("action_hint"):
                    print(f"         action: {result['action_hint']}")

    if args.probe_llm:
        status = "ready" if passed_providers else ("unavailable" if not configured_analysis else "failed")
    else:
        status = "ready" if configured_analysis else "unavailable"

    audit = {
        "schema_version": 2,
        "generated_at": utc_now_iso(),
        "status": status,
        "analysis_provider_configured": configured_analysis,
        "analysis_provider_passed": passed_providers,
        "live_probe_performed": args.probe_llm,
        "provider_orders": {
            "extract": list(LLMRouter(HttpClient("pif/order-only")).provider_order("extract")),
            "rescue": list(LLMRouter(HttpClient("pif/order-only")).provider_order("rescue")),
        },
        "provider_endpoints": {
            "siliconflow": LLMRouter(HttpClient("pif/endpoint-only")).provider_base_url("siliconflow"),
            "bigmodel": LLMRouter(HttpClient("pif/endpoint-only")).provider_base_url("bigmodel"),
            "deepseek": LLMRouter(HttpClient("pif/endpoint-only")).provider_base_url("deepseek"),
        },
        "providers": probes,
        "credentials": rows,
    }
    if args.json_out:
        dump_json(Path(args.json_out), audit)
        print(f"\nSafe audit written to {args.json_out}")

    print(f"\nSiliconFlow API endpoint: {audit['provider_endpoints']['siliconflow']}")
    print("A 429 response is treated as a cooldown, not automatically as permanent quota exhaustion.")
    print("OpenRouter, SiliconFlow and DeepSeek account information is recorded when supported; secrets are never printed.")

    if args.require_analysis_provider or args.analysis_only:
        return 0 if status == "ready" else 3
    return 2 if missing_required or not configured_analysis else 0


if __name__ == "__main__":
    raise SystemExit(main())
