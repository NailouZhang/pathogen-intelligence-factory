from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from .http import HttpClient
from .provider_state import ProviderRuntimeState, ProviderStateStore
from .utils import clean_space, utc_now_iso


OPENAI_COMPATIBLE_PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "model_env": "GROQ_MODEL",
        "models_env": "PIF_GROQ_MODELS",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
        "models_env": "PIF_OPENROUTER_MODELS",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "key_env": "MISTRAL_API_KEY",
        "model_env": "MISTRAL_MODEL",
        "models_env": "PIF_MISTRAL_MODELS",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "base_url_env": "SILICONFLOW_BASE_URL",
        "key_env": "SILICONFLOW_API_KEY",
        "model_env": "SILICONFLOW_MODEL",
        "models_env": "PIF_SILICONFLOW_MODELS",
    },
    "bigmodel": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "base_url_env": "BIGMODEL_BASE_URL",
        "key_env": "BIGMODEL_API_KEY",
        "model_env": "BIGMODEL_MODEL",
        "models_env": "PIF_BIGMODEL_MODELS",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "models_env": "PIF_DEEPSEEK_MODELS",
    },
}

DEFAULT_MODELS: dict[str, list[str]] = {
    "gemini": ["gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash"],
    "groq": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    "openrouter": ["openrouter/free"],
    "mistral": ["mistral-small-latest"],
    "siliconflow": ["Qwen/Qwen3-8B"],
    "bigmodel": ["glm-4.7-flash"],
    "deepseek": ["deepseek-v4-flash"],
}


class LLMError(RuntimeError):
    """Structured LLM failure that preserves every safe provider attempt."""

    def __init__(
        self,
        message: str,
        *,
        attempts: list[dict[str, Any]] | None = None,
        category: str = "unknown",
        candidates: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.category = category or "unknown"
        # Invalid-but-parsed candidates are retained for bounded field-level
        # repair. They contain no credentials and remain in the private audit.
        self.candidates = list(candidates or [])


def _balanced_json_candidates(raw: str) -> list[str]:
    candidates: list[str] = []
    for opening, closing in (("{", "}"), ("[", "]")):
        for start in (index for index, char in enumerate(raw) if char == opening):
            depth = 0
            in_string = False
            escaped = False
            for index in range(start, len(raw)):
                char = raw[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif char == "\\":
                        escaped = True
                    elif char == '"':
                        in_string = False
                    continue
                if char == '"':
                    in_string = True
                elif char == opening:
                    depth += 1
                elif char == closing:
                    depth -= 1
                    if depth == 0:
                        candidates.append(raw[start : index + 1])
                        break
            if candidates:
                break
    return candidates


def _attach_parser_audit(value: Any, audit: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        output = dict(value)
        output["_pif_parser_audit"] = audit
        return output
    return value


def _extract_json(text: str, *, truncated: bool = False) -> dict[str, Any] | list[Any]:
    raw = str(text or "").replace("\ufeff", "").strip()
    raw = re.sub(r"^```(?:json|javascript|python)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```\s*$", "", raw)
    attempts: list[tuple[str, str]] = [("direct_json", raw)]
    attempts.extend(("balanced_json", candidate) for candidate in _balanced_json_candidates(raw))

    seen: set[str] = set()
    for method, candidate in attempts:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        variants = [(method, candidate)]
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        if cleaned != candidate:
            variants.append((f"{method}_trailing_comma_repair", cleaned))
        for variant_method, variant in variants:
            try:
                value = json.loads(variant)
                return _attach_parser_audit(value, {
                    "policy_version": "v17.2-flexible-json-parser-1",
                    "method": variant_method,
                    "response_chars": len(raw),
                    "repaired": variant_method != "direct_json",
                    "truncated_signal": bool(truncated),
                })
            except json.JSONDecodeError:
                pass
            try:
                value = ast.literal_eval(variant)
                if isinstance(value, (dict, list)):
                    return _attach_parser_audit(value, {
                        "policy_version": "v17.2-flexible-json-parser-1",
                        "method": f"{variant_method}_python_literal_repair",
                        "response_chars": len(raw),
                        "repaired": True,
                        "truncated_signal": bool(truncated),
                    })
            except (SyntaxError, ValueError):
                pass
    category = "context_or_output_limit" if truncated or (raw.count("{") > raw.count("}")) else "invalid_json"
    raise LLMError(
        "Model response did not contain valid complete JSON",
        category=category,
    )


def classify_llm_failure(error: Any) -> str:
    """Map provider/HTTP/schema failures to stable, audit-friendly categories."""

    if isinstance(error, LLMError) and error.category not in {"", "unknown"}:
        return error.category
    text = clean_space(error).lower()
    if not text or text == "[]":
        return "no_provider_configured"
    if "validation_failed" in text or "schema validation" in text or "validator" in text:
        return "validation_failed"
    if "valid json" in text or "jsondecode" in text or ("json" in text and "parse" in text):
        return "invalid_json"
    if any(token in text for token in (
        "model disabled", "model is disabled", "model access denied", "permission denied for model",
        "model is not enabled", "model permission", "model is restricted",
    )):
        return "model_not_found"
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden", "invalid api key", "api_key_invalid", "authentication")):
        return "authentication_failed"
    if any(token in text for token in ("429", "rate limit", "resource_exhausted", "too many requests", "请求过于频繁", "并发超额")):
        return "rate_limited"
    if any(token in text for token in (
        "402", "insufficient credit", "insufficient balance", "negative credit", "payment required",
        "余额不足", "余额已用完", "账户余额", "赠送余额不可用", "free granted balance is unavailable",
    )):
        return "quota_exhausted"
    if any(token in text for token in ("quota", "billing", "insufficient_quota", "daily limit", "monthly limit", "token budget exhausted")):
        return "quota_exhausted"
    if any(token in text for token in ("timeout", "timed out", "read timed out", "connect timeout", "deadline exceeded")):
        return "timeout"
    if any(token in text for token in ("context length", "maximum context", "token limit", "request too large", "payload too large")):
        return "context_or_output_limit"
    if any(token in text for token in ("no candidates", "no choices", "empty response", "empty content")):
        return "empty_response"
    if any(token in text for token in (
        "model not found", "unknown model", "model_not_found", "does not exist", "model is not available",
        "invalid model", "404: model", "404 model",
    )):
        return "model_not_found"
    if any(token in text for token in (
        "unsupported parameter", "unsupported_parameter", "unrecognized request argument", "unknown field",
        "response_format is not supported", "thinking is not supported", "enable_thinking is not supported",
    )):
        return "unsupported_parameter"
    if any(token in text for token in ("400", "422", "bad request", "invalid request", "invalid_request_error")):
        return "invalid_request"
    if any(token in text for token in ("500", "502", "503", "504", "server error", "service unavailable", "upstream error")):
        return "provider_unavailable"
    if any(token in text for token in (
        "connection", "dns", "network", "http request failed", "ssl", "certificate verify", "proxyerror",
        "connection reset", "remote disconnected", "name resolution", "temporary failure in name resolution", "eof occurred",
    )):
        return "network_error"
    return "unknown"


def summarize_attempt_categories(attempts: list[dict[str, Any]]) -> str:
    failures = [clean_space(row.get("failure_category")) for row in attempts if row.get("status") == "failed"]
    failures = [value for value in failures if value]
    if not failures:
        skipped = [clean_space(row.get("failure_category")) for row in attempts if row.get("status") == "skipped"]
        skipped = [value for value in skipped if value and value != "provider_not_configured"]
        if skipped:
            return Counter(skipped).most_common(1)[0][0]
        return "no_provider_configured" if not attempts else "unknown"
    # Prefer the most actionable category over a generic/secondary parser error.
    priority = (
        "authentication_failed", "quota_exhausted", "rate_limited", "model_not_found",
        "unsupported_parameter", "invalid_request", "timeout", "network_error",
        "provider_unavailable", "model_discovery_failed", "context_or_output_limit", "invalid_json", "empty_response",
        "validation_failed", "unknown",
    )
    counts = Counter(failures)
    for category in priority:
        if counts.get(category):
            return category
    return counts.most_common(1)[0][0]


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


@dataclass
class LLMResult:
    data: dict[str, Any] | list[Any]
    provider: str
    model: str
    attempts: list[dict[str, Any]]


@dataclass
class LLMTextResult:
    text: str
    provider: str
    model: str
    attempts: list[dict[str, Any]]


class LLMRouter:
    """Observable multi-provider router with per-model cooldown and usage audit."""

    def __init__(
        self,
        http: HttpClient,
        gemini_key: str = "",
        groq_key: str = "",
        openrouter_key: str = "",
        mistral_key: str = "",
        siliconflow_key: str = "",
        bigmodel_key: str = "",
        deepseek_key: str = "",
        provider_keys: dict[str, str] | None = None,
    ) -> None:
        self.http = http
        supplied = provider_keys or {}
        self.keys = {
            "gemini": gemini_key or supplied.get("gemini", "") or os.getenv("GEMINI_API_KEY", "").strip(),
            "groq": groq_key or supplied.get("groq", "") or os.getenv("GROQ_API_KEY", "").strip(),
            "openrouter": openrouter_key or supplied.get("openrouter", "") or os.getenv("OPENROUTER_API_KEY", "").strip(),
            "mistral": mistral_key or supplied.get("mistral", "") or os.getenv("MISTRAL_API_KEY", "").strip(),
            "siliconflow": siliconflow_key or supplied.get("siliconflow", "") or os.getenv("SILICONFLOW_API_KEY", "").strip(),
            "bigmodel": bigmodel_key or supplied.get("bigmodel", "") or os.getenv("BIGMODEL_API_KEY", "").strip(),
            "deepseek": deepseek_key or supplied.get("deepseek", "") or os.getenv("DEEPSEEK_API_KEY", "").strip(),
        }
        # Backwards-compatible public attributes used by earlier code/tests.
        self.gemini_key = self.keys["gemini"]
        self.groq_key = self.keys["groq"]
        self.openrouter_key = self.keys["openrouter"]
        self.mistral_key = self.keys["mistral"]
        self.siliconflow_key = self.keys["siliconflow"]
        self.bigmodel_key = self.keys["bigmodel"]
        self.deepseek_key = self.keys["deepseek"]
        self._model_cache: dict[str, list[str]] = {}
        self._model_discovery_audit: dict[str, dict[str, Any]] = {}
        self.task_failures: list[dict[str, Any]] = []
        self.state_store = ProviderStateStore(os.getenv("PIF_PROVIDER_STATE_FILE", "").strip() or None)
        self.states = self.state_store.load(list(self.keys))
        state_changed = False
        for provider, key in self.keys.items():
            fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else ""
            state = self.states[provider]
            # A newly configured or replaced key must not inherit an old
            # authentication/quota disablement from the daily state file.
            if fingerprint and fingerprint != state.key_fingerprint:
                state.reset_for_key_change(fingerprint)
                state_changed = True
            elif not key and state.key_fingerprint:
                state.key_fingerprint = ""
                state_changed = True
        if state_changed:
            self._persist_states()

    @property
    def available(self) -> bool:
        return any(self.keys.values())

    def configured_providers(self) -> list[str]:
        return [name for name, key in self.keys.items() if key]

    def provider_base_url(self, provider: str) -> str:
        """Return the normalized API base URL for an OpenAI-compatible provider.

        SiliconFlow China-issued API keys are scoped to api.siliconflow.cn.
        An explicit SILICONFLOW_BASE_URL may override the default for testing or
        a future regional endpoint, but an empty value always falls back to the
        official China endpoint bundled with this release.
        """
        config = OPENAI_COMPATIBLE_PROVIDERS[provider]
        env_name = clean_space(config.get("base_url_env"))
        configured = clean_space(os.getenv(env_name, "")) if env_name else ""
        base_url = (configured or clean_space(config.get("base_url"))).rstrip("/")
        # Accept either a provider base URL or the full Chat Completions URL.
        # Internally all calls append /chat/completions exactly once.
        suffix = "/chat/completions"
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)].rstrip("/")
        return base_url

    def provider_order(self, purpose: str = "extract") -> tuple[str, ...]:
        env_name = {
            "extract": "PIF_LLM_EXTRACT_PROVIDER_ORDER",
            "rescue": "PIF_LLM_RESCUE_PROVIDER_ORDER",
            "overview": "PIF_LLM_OVERVIEW_PROVIDER_ORDER",
            "relevance": "PIF_LLM_RELEVANCE_PROVIDER_ORDER",
            "translation": "PIF_TRANSLATION_PROVIDER_ORDER",
        }.get(purpose, "PIF_LLM_PROVIDER_ORDER")
        default = {
            "extract": "gemini,bigmodel,siliconflow,mistral,deepseek,openrouter,groq",
            "rescue": "gemini,deepseek,bigmodel,mistral,siliconflow,openrouter,groq",
            "overview": "gemini,deepseek,bigmodel,mistral,siliconflow,openrouter,groq",
            "relevance": "gemini,bigmodel,siliconflow,mistral,deepseek,openrouter,groq",
            "translation": "gemini,bigmodel,mistral,siliconflow,deepseek,openrouter,groq",
        }.get(purpose, "gemini,bigmodel,siliconflow,mistral,deepseek,openrouter,groq")
        values = _split_csv(os.getenv(env_name, "") or default)
        known = [value.lower() for value in values if value.lower() in self.keys]
        return tuple(dict.fromkeys(known))

    def paid_requests_allowed(self) -> bool:
        return os.getenv("PIF_LLM_ALLOW_PAID", "false").strip().lower() in {"1", "true", "yes", "on"}

    def _billing_guard(self, provider: str, model: str) -> tuple[bool, str]:
        """Best-effort no-paid-route guard.

        Provider account billing configuration is outside the process and cannot
        always be queried.  With paid requests disabled we only allow providers
        and models explicitly present in the operator's free allowlists.
        """
        if self.paid_requests_allowed():
            return True, "paid_requests_allowed"
        providers = {x.lower() for x in _split_csv(os.getenv(
            "PIF_LLM_FREE_PROVIDER_ALLOWLIST",
            "gemini,bigmodel,siliconflow,mistral,deepseek,openrouter,groq",
        ))}
        if provider not in providers:
            return False, "provider_not_in_free_allowlist"
        # The process cannot reliably discover account billing mode for every
        # provider. Provider admission therefore remains an explicit operator
        # allowlist. OpenRouter is the one route with a machine-readable free
        # model convention and is additionally restricted to /free models.
        if provider == "openrouter" and os.getenv("PIF_OPENROUTER_FREE_ONLY", "true").strip().lower() in {"1", "true", "yes", "on"}:
            if "free" not in model.casefold():
                return False, "openrouter_non_free_model_blocked"
        return True, "provider_in_operator_free_allowlist"

    def record_task_failure(self, task_name: str, error: LLMError, **context: Any) -> None:
        row = {
            "task": task_name,
            "at": utc_now_iso(),
            "failure_category": error.category,
            "error": self._safe_error_text(error),
            "attempts": list(error.attempts or []),
            "context": context,
        }
        self.task_failures.append(row)
        if len(self.task_failures) > 500:
            del self.task_failures[:-500]

    def _safe_error_text(self, error: Any) -> str:
        text = clean_space(error)
        for secret in self.keys.values():
            if secret:
                text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"([?&](?:key|api_key|apikey)=)[^&\s]+", r"\1[REDACTED]", text, flags=re.I)
        text = re.sub(r"(bearer\s+)[A-Za-z0-9._~-]+", r"\1[REDACTED]", text, flags=re.I)
        return text[:900]

    def _explicit_models(self, provider: str) -> list[str]:
        """Return operator preferences without treating them as guaranteed availability."""
        if provider == "gemini":
            configured = _split_csv(os.getenv("PIF_GEMINI_MODELS", ""))
            single = os.getenv("GEMINI_MODEL", "").strip()
        else:
            config = OPENAI_COMPATIBLE_PROVIDERS[provider]
            configured = _split_csv(os.getenv(config["models_env"], ""))
            single = os.getenv(config["model_env"], "").strip()
        ordered: list[str] = []
        for name in ([single] if single else []) + configured:
            if name and name not in ordered:
                ordered.append(name)
        return ordered

    def _configured_models(self, provider: str) -> list[str]:
        """Backward-compatible preference plus last-resort fallback list."""
        ordered: list[str] = []
        for name in self._explicit_models(provider) + DEFAULT_MODELS.get(provider, []):
            if name and name not in ordered:
                ordered.append(name)
        return ordered

    @staticmethod
    def _model_is_text_chat(provider: str, item: dict[str, Any], name: str) -> bool:
        low = name.casefold()
        blocked = (
            "whisper", "speech", "tts", "guard", "moderation", "embedding", "embed-",
            "rerank", "image", "vision", "audio", "video", "ocr", "transcribe", "compound",
        )
        if not name or any(token in low for token in blocked):
            return False
        if item.get("active") is False or item.get("archived") is True:
            return False
        capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        if capabilities and capabilities.get("completion_chat") is False:
            return False
        if provider == "siliconflow":
            model_type = clean_space(item.get("type") or item.get("model_type")).casefold()
            if model_type and model_type not in {"text", "chat", "llm"}:
                return False
        return True

    def _model_score(self, provider: str, item: dict[str, Any], name: str, explicit: set[str]) -> tuple[int, str]:
        low = name.casefold()
        value = 0
        if name in explicit:
            value += 1000
        if item.get("active") is not False and item.get("archived") is not True:
            value += 120
        if not any(token in low for token in ("preview", "experimental", "exp-", "beta", "deprecated")):
            value += 100
        if any(token in low for token in ("flash-lite", "small", "mini", "ministral", "instant", "8b", "7b")):
            value += 100
        elif "flash" in low:
            value += 85
        if any(token in low for token in ("qwen", "mistral", "llama", "gemma", "glm", "deepseek", "gemini")):
            value += 45
        if provider == "openrouter" and name.endswith(":free"):
            value += 180
        if any(token in low for token in ("reasoning", "thinking", "r1", "pro", "large", "120b", "70b")):
            value -= 30
        context = item.get("context_window") or item.get("max_context_length") or item.get("inputTokenLimit") or 0
        try:
            if int(context) >= 16000:
                value += 10
        except (TypeError, ValueError):
            pass
        return (-value, name.casefold())

    def _discover_gemini_models(self) -> list[str]:
        return self.discover_models("gemini")

    def _discover_groq_models(self) -> list[str]:
        return self.discover_models("groq")

    def discover_models(self, provider: str, *, refresh: bool = False) -> list[str]:
        provider = provider.casefold()
        if provider not in self.keys:
            return []
        if refresh:
            self._model_cache.pop(provider, None)
            self._model_discovery_audit.pop(provider, None)
        return list(self._discover_models(provider))

    def model_discovery_snapshot(self, provider: str | None = None) -> dict[str, Any]:
        if provider is not None:
            return dict(self._model_discovery_audit.get(provider.casefold()) or {})
        return {name: dict(value) for name, value in self._model_discovery_audit.items()}

    def _discover_models(self, provider: str) -> list[str]:
        if provider in self._model_cache:
            return self._model_cache[provider]
        explicit = self._explicit_models(provider)
        fallback = self._configured_models(provider)
        explicit_set = set(explicit)
        discovered_records: list[dict[str, Any]] = []
        key = self.keys.get(provider, "")
        discovery_enabled = os.getenv("PIF_LLM_DISCOVER_MODELS", "true").strip().casefold() in {"1", "true", "yes", "on"}
        provider_flag = os.getenv(f"PIF_{provider.upper()}_DISCOVER_MODELS", "true").strip().casefold() in {"1", "true", "yes", "on"}
        endpoint = ""
        discovery_error = ""
        discovery_category = ""
        if key and discovery_enabled and provider_flag:
            try:
                if provider == "gemini":
                    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"
                    page_token = ""
                    for _ in range(3):
                        params: dict[str, Any] = {"pageSize": 1000}
                        if page_token:
                            params["pageToken"] = page_token
                        payload = self.http.get_json(
                            endpoint, headers={"x-goog-api-key": key}, params=params,
                            timeout=min(30, self._provider_timeout(provider)), retry_attempts=2,
                        )
                        for item in payload.get("models", []) if isinstance(payload, dict) else []:
                            if not isinstance(item, dict):
                                continue
                            methods = item.get("supportedGenerationMethods") or item.get("supportedActions") or []
                            name = clean_space(item.get("baseModelId") or str(item.get("name", "")).removeprefix("models/"))
                            if "generateContent" in methods and self._model_is_text_chat(provider, item, name):
                                discovered_records.append({**item, "id": name})
                        page_token = clean_space(payload.get("nextPageToken") if isinstance(payload, dict) else "")
                        if not page_token:
                            break
                else:
                    endpoint = f"{self.provider_base_url(provider)}/models"
                    params = {"type": "text", "sub_type": "chat"} if provider == "siliconflow" else None
                    payload = self.http.get_json(
                        endpoint, headers={"Authorization": f"Bearer {key}"}, params=params,
                        timeout=min(30, self._provider_timeout(provider)), retry_attempts=2,
                    )
                    items = payload.get("data", payload if isinstance(payload, list) else []) if isinstance(payload, (dict, list)) else []
                    for item in items or []:
                        if not isinstance(item, dict):
                            continue
                        name = clean_space(item.get("id") or item.get("name"))
                        if not self._model_is_text_chat(provider, item, name):
                            continue
                        if provider == "openrouter" and os.getenv("PIF_OPENROUTER_FREE_ONLY", "true").casefold() in {"1", "true", "yes", "on"}:
                            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
                            zero = {"0", "0.0", "0.000000", "0e-10", "0.0000000000"}
                            free_pricing = bool(pricing) and all(str(pricing.get(field, "0")).casefold() in zero for field in ("prompt", "completion"))
                            if not (name.endswith(":free") or free_pricing):
                                continue
                        discovered_records.append(item)
            except Exception as exc:
                discovery_category = classify_llm_failure(exc)
                discovery_error = self._safe_error_text(exc)

        unique_records: dict[str, dict[str, Any]] = {}
        for item in discovered_records:
            name = clean_space(item.get("id") or item.get("name"))
            if name and name not in unique_records:
                unique_records[name] = item
        ranked_discovered = sorted(
            unique_records,
            key=lambda name: self._model_score(provider, unique_records[name], name, explicit_set),
        )
        discovered_set = set(ranked_discovered)
        valid_explicit = [name for name in explicit if name in discovered_set]
        unmatched_explicit = [name for name in explicit if name not in discovered_set]
        ordered: list[str] = []
        # Discover-first means stale GitHub Variables become preferences only when
        # the provider confirms that the model is currently available.
        if ranked_discovered:
            candidates = valid_explicit + ranked_discovered + unmatched_explicit + fallback
            status = "discovered"
        else:
            candidates = explicit + fallback
            status = "fallback"
        for name in candidates:
            if name and name not in ordered:
                ordered.append(name)
        limit = max(5, int(os.getenv("PIF_LLM_DISCOVERY_MAX_CANDIDATES", "30")))
        self._model_cache[provider] = ordered[:limit]
        self._model_discovery_audit[provider] = {
            "status": status,
            "endpoint": endpoint,
            "discovery_enabled": bool(discovery_enabled and provider_flag),
            "discovered_count": len(ranked_discovered),
            "discovered_models": ranked_discovered[:limit],
            "explicit_preferences": explicit,
            "fallback_models": DEFAULT_MODELS.get(provider, []),
            "selected_candidates": self._model_cache[provider],
            "failure_category": discovery_category,
            "error": discovery_error,
        }
        return self._model_cache[provider]

    def _provider_timeout(self, provider: str) -> int:
        defaults = {
            "gemini": 75,
            "groq": 60,
            "openrouter": 90,
            "mistral": 75,
            "siliconflow": 90,
            "bigmodel": 120,
            "deepseek": 90,
        }
        specific = os.getenv(f"PIF_LLM_{provider.upper()}_TIMEOUT", "").strip()
        generic = os.getenv("PIF_LLM_HTTP_TIMEOUT", "").strip()
        raw = specific or generic or str(defaults.get(provider, 75))
        try:
            return max(15, int(raw))
        except ValueError:
            return defaults.get(provider, 75)

    @staticmethod
    def _max_output_tokens(override: int | None = None) -> int:
        if override is not None:
            return max(32, int(override))
        return max(256, int(os.getenv("PIF_LLM_MAX_OUTPUT_TOKENS", "1400")))

    def _gemini_call(
        self,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        *,
        json_mode: bool = True,
        max_output_tokens: int | None = None,
    ) -> tuple[Any, dict[str, Any], str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": self._max_output_tokens(max_output_tokens),
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        response = self.http.request(
            "POST",
            url,
            headers={"x-goog-api-key": self.keys["gemini"]},
            json=payload,
            timeout=self._provider_timeout("gemini"),
            retry_attempts=1,
        )
        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini returned no candidates: {body}", category="empty_response")
        candidate = candidates[0]
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(str(part.get("text", "")) for part in parts).strip()
        if not text:
            raise LLMError(f"Gemini returned empty content: {body}", category="empty_response")
        finish_reason = clean_space(candidate.get("finishReason"))
        usage = body.get("usageMetadata") or {}
        normalized = {
            "prompt_tokens": usage.get("promptTokenCount") or 0,
            "completion_tokens": usage.get("candidatesTokenCount") or 0,
            "total_tokens": usage.get("totalTokenCount") or 0,
        }
        content: Any = _extract_json(text, truncated=finish_reason in {"MAX_TOKENS", "RECITATION"}) if json_mode else text
        return content, normalized, clean_space(body.get("modelVersion")) or model

    def _openai_compatible_call(
        self,
        provider: str,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
        *,
        json_mode: bool = True,
        max_output_tokens: int | None = None,
    ) -> tuple[Any, dict[str, Any], str]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            ("max_completion_tokens" if provider == "groq" else "max_tokens"): self._max_output_tokens(max_output_tokens),
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        disable_thinking = os.getenv("PIF_LLM_DISABLE_THINKING", "true").lower() in {"1", "true", "yes", "on"}
        if provider == "siliconflow" and disable_thinking:
            payload["enable_thinking"] = False
        if provider in {"bigmodel", "deepseek"} and disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        if provider == "deepseek" and os.getenv("PIF_DEEPSEEK_GRANTED_BALANCE_ONLY", "false").lower() in {"1", "true", "yes", "on"}:
            account = self.provider_account_info("deepseek", refresh=True)
            if account.get("status") != "ok":
                category = clean_space(account.get("failure_category")) or "provider_unavailable"
                raise LLMError("DeepSeek balance check failed before a granted-balance-only request", category=category)
            if not account.get("granted_balance_available"):
                raise LLMError(
                    "DeepSeek free granted balance is unavailable; paid balance is protected by PIF_DEEPSEEK_GRANTED_BALANCE_ONLY=true",
                    category="quota_exhausted",
                )
        if provider == "mistral":
            payload["prompt_cache_key"] = clean_space(os.getenv("PIF_MISTRAL_PROMPT_CACHE_KEY", "pif-structured-analysis-v1"))
        headers = {"Authorization": f"Bearer {self.keys[provider]}", "Accept": "application/json"}
        if provider == "openrouter":
            referer = os.getenv("PIF_OPENROUTER_HTTP_REFERER", "").strip()
            title = os.getenv("PIF_OPENROUTER_APP_TITLE", "Pathogen Intelligence Factory").strip()
            if referer:
                headers["HTTP-Referer"] = referer
            if title:
                headers["X-Title"] = title

        endpoint = f"{self.provider_base_url(provider)}/chat/completions"
        try:
            response = self.http.request(
                "POST", endpoint, headers=headers, json=payload,
                timeout=self._provider_timeout(provider), retry_attempts=1,
            )
        except Exception as exc:
            # Several nominally OpenAI-compatible free models reject optional
            # JSON/thinking/cache parameters even though the endpoint itself works.
            # Retry exactly once with the minimal portable payload while keeping
            # the prompt's explicit JSON contract.
            if json_mode and classify_llm_failure(exc) in {"unsupported_parameter", "invalid_request"}:
                minimal = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system + "\nReturn one valid JSON object only."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": temperature,
                    ("max_completion_tokens" if provider == "groq" else "max_tokens"): self._max_output_tokens(max_output_tokens),
                }
                response = self.http.request(
                    "POST", endpoint, headers=headers, json=minimal,
                    timeout=self._provider_timeout(provider), retry_attempts=1,
                )
            else:
                raise
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(f"{provider} returned no choices: {body}", category="empty_response")
        choice = choices[0]
        message = choice.get("message") or {}
        content_text = clean_space(message.get("content"))
        if not content_text:
            # Some reasoning-capable endpoints put the final answer in an
            # alternate field. Never expose reasoning; only accept final text.
            content_text = clean_space(message.get("final") or choice.get("text"))
        if not content_text:
            raise LLMError(f"{provider} returned empty content: {body}", category="empty_response")
        finish_reason = clean_space(choice.get("finish_reason"))
        content: Any = _extract_json(content_text, truncated=finish_reason in {"length", "max_tokens"}) if json_mode else content_text
        return content, body.get("usage") or {}, clean_space(body.get("model")) or model

    def _groq_call(
        self, model: str, system: str, prompt: str, temperature: float, *,
        json_mode: bool = True, max_output_tokens: int | None = None,
    ) -> tuple[Any, dict[str, Any], str]:
        caller = self._openai_compatible_call
        try:
            parameters = inspect.signature(caller).parameters
            supports_options = "json_mode" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            supports_options = False
        if supports_options:
            return caller(
                "groq", model, system, prompt, temperature,
                json_mode=json_mode, max_output_tokens=max_output_tokens,
            )
        return caller("groq", model, system, prompt, temperature)

    def provider_account_info(self, provider: str, *, refresh: bool = False) -> dict[str, Any]:
        """Return safe, provider-supported account/credit information when available."""
        state = self.states.get(provider)
        if state is not None and state.account and not refresh:
            return dict(state.account)
        key = self.keys.get(provider, "")
        if not key:
            return {"status": "not_configured"}
        try:
            if provider == "openrouter":
                body = self.http.get_json(
                    "https://openrouter.ai/api/v1/key",
                    headers={"Authorization": f"Bearer {key}"},
                )
                data = body.get("data") or {}
                result = {
                    "status": "ok",
                    "is_free_tier": data.get("is_free_tier"),
                    "limit": data.get("limit"),
                    "limit_remaining": data.get("limit_remaining"),
                    "usage_daily": data.get("usage_daily"),
                    "usage_weekly": data.get("usage_weekly"),
                    "usage_monthly": data.get("usage_monthly"),
                }
                self.states[provider].account = result
                self._persist_states()
                return result
            if provider == "siliconflow":
                body = self.http.get_json(
                    f"{self.provider_base_url('siliconflow')}/user/info",
                    headers={"Authorization": f"Bearer {key}"},
                )
                data = body.get("data") or {}
                result = {
                    "status": "ok" if body.get("status") is not False else "failed",
                    "account_status": data.get("status"),
                    "balance": data.get("balance"),
                    "charge_balance": data.get("chargeBalance"),
                    "total_balance": data.get("totalBalance"),
                }
                self.states[provider].account = result
                self._persist_states()
                return result
            if provider == "deepseek":
                body = self.http.get_json(
                    f"{self.provider_base_url('deepseek')}/user/balance",
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                )
                balances = body.get("balance_infos") if isinstance(body.get("balance_infos"), list) else []
                granted_total = 0.0
                topped_up_total = 0.0
                safe_balances: list[dict[str, Any]] = []
                for row in balances:
                    if not isinstance(row, dict):
                        continue
                    try:
                        granted = float(row.get("granted_balance") or 0)
                    except (TypeError, ValueError):
                        granted = 0.0
                    try:
                        topped_up = float(row.get("topped_up_balance") or 0)
                    except (TypeError, ValueError):
                        topped_up = 0.0
                    granted_total += granted
                    topped_up_total += topped_up
                    safe_balances.append({
                        "currency": row.get("currency"),
                        "total_balance": row.get("total_balance"),
                        "granted_balance": row.get("granted_balance"),
                        "topped_up_balance": row.get("topped_up_balance"),
                    })
                minimum = max(0.0, float(os.getenv("PIF_DEEPSEEK_MIN_GRANTED_BALANCE", "0.10")))
                result = {
                    "status": "ok",
                    "is_available": bool(body.get("is_available")),
                    "granted_balance_available": bool(body.get("is_available")) and granted_total >= minimum,
                    "granted_balance_total": round(granted_total, 6),
                    "topped_up_balance_total": round(topped_up_total, 6),
                    "minimum_granted_balance": minimum,
                    "balances": safe_balances,
                }
                self.states[provider].account = result
                if not body.get("is_available"):
                    self.states[provider].status = "quota_exhausted"
                    self.states[provider].disabled_reason = "deepseek_balance_unavailable"
                self._persist_states()
                return result
            result = {"status": "not_supported"}
            self.states[provider].account = result
            self._persist_states()
            return result
        except Exception as exc:
            result = {
                "status": "failed",
                "failure_category": classify_llm_failure(exc),
                "error": self._safe_error_text(exc),
            }
            self.states[provider].account = result
            self._persist_states()
            return result

    def _normalize_call_result(self, value: Any, model: str) -> tuple[Any, dict[str, Any], str]:
        if isinstance(value, tuple) and len(value) == 3:
            return value[0], value[1] or {}, clean_space(value[2]) or model
        return value, {}, model

    def _persist_states(self) -> None:
        self.state_store.save(self.states)

    def _failure_cooldown_seconds(self, error: Any, default: int) -> int:
        text = clean_space(error)
        match = re.search(r"retry[- ]?after[^0-9]{0,20}(\d+)", text, flags=re.I)
        if match:
            return max(1, int(match.group(1)))
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        try:
            return max(1, int(float(value)))
        except (TypeError, ValueError):
            return max(1, default)

    @staticmethod
    def _accepts_keyword(callable_obj: Any, keyword: str) -> bool:
        try:
            signature = inspect.signature(callable_obj)
        except (TypeError, ValueError):
            return False
        return keyword in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    def _invoke_json_caller(
        self, caller: Any, model: str, system: str, prompt: str,
        temperature: float, max_output_tokens: int | None,
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if max_output_tokens is not None and self._accepts_keyword(caller, "max_output_tokens"):
            kwargs["max_output_tokens"] = max_output_tokens
        return caller(model, system, prompt, temperature, **kwargs)

    def json_task(
        self,
        *,
        system: str,
        prompt: str,
        provider_order: tuple[str, ...] | None = None,
        temperature: float = 0.1,
        validator: Any | None = None,
        normalizer: Any | None = None,
        max_models_per_provider: int = 3,
        task_name: str = "json_task",
        max_output_tokens: int | None = None,
        ignore_runtime_cooldown: bool = False,
    ) -> LLMResult:
        attempts: list[dict[str, Any]] = []
        if provider_order is None:
            provider_order = self.provider_order("extract")
        else:
            provider_order = tuple(provider_order)
        if not provider_order:
            attempt = {
                "task": task_name, "provider": "", "model": "", "at": utc_now_iso(),
                "status": "failed", "failure_category": "no_provider_configured",
                "error": "The caller supplied an empty provider order; extract fallback was not applied.",
            }
            raise LLMError(
                "No provider is configured for the requested task order",
                attempts=[attempt], category="no_provider_configured",
            )
        runtime_cap = max(1, int(os.getenv("PIF_LLM_MAX_MODELS_PER_PROVIDER", "2")))
        max_models_per_provider = min(max_models_per_provider, runtime_cap)
        attempts_per_model = max(1, int(os.getenv("PIF_LLM_ATTEMPTS_PER_MODEL", "1")))
        cooldown_seconds = max(1, int(os.getenv("PIF_LLM_PROVIDER_COOLDOWN_SECONDS", "60")))
        configured_provider_seen = False
        invalid_candidates: list[dict[str, Any]] = []

        for provider in provider_order:
            provider = provider.lower()
            key = self.keys.get(provider, "")
            state = self.states.get(provider)
            if not key or state is None:
                attempts.append({
                    "task": task_name,
                    "provider": provider,
                    "model": "",
                    "at": utc_now_iso(),
                    "status": "skipped",
                    "failure_category": "provider_not_configured",
                    "error": "API key not configured",
                })
                continue
            configured_provider_seen = True
            if not ignore_runtime_cooldown and not state.available():
                attempts.append({
                    "task": task_name,
                    "provider": provider,
                    "model": "",
                    "at": utc_now_iso(),
                    "status": "skipped",
                    "failure_category": state.status,
                    "error": state.disabled_reason or "provider is cooling down",
                })
                continue

            models = self._discover_models(provider)[:max_models_per_provider]
            if not models:
                attempts.append({
                    "task": task_name,
                    "provider": provider,
                    "model": "",
                    "at": utc_now_iso(),
                    "status": "failed",
                    "failure_category": "model_discovery_failed",
                    "error": "No usable text generation model discovered or configured",
                })
                continue
            caller = self._gemini_call if provider == "gemini" else (
                self._groq_call if provider == "groq" else
                lambda model, system, prompt, temperature, p=provider: self._openai_compatible_call(p, model, system, prompt, temperature)
            )

            for model in models:
                billing_allowed, billing_reason = self._billing_guard(provider, model)
                if not billing_allowed:
                    attempts.append({
                        "task": task_name, "provider": provider, "model": model,
                        "at": utc_now_iso(), "status": "skipped",
                        "failure_category": "paid_route_blocked", "error": billing_reason,
                    })
                    continue
                if not ignore_runtime_cooldown and not state.model_available(model):
                    attempts.append({
                        "task": task_name,
                        "provider": provider,
                        "model": model,
                        "at": utc_now_iso(),
                        "status": "skipped",
                        "failure_category": "model_cooldown",
                        "error": "model is temporarily cooling down",
                    })
                    continue
                for retry_index in range(attempts_per_model):
                    attempt = {
                        "task": task_name,
                        "provider": provider,
                        "model": model,
                        "retry_index": retry_index,
                        "at": utc_now_iso(),
                        "system_chars": len(system),
                        "prompt_chars": len(prompt),
                    }
                    started = time.monotonic()
                    try:
                        raw_result = self._invoke_json_caller(caller, model, system, prompt, temperature, max_output_tokens)
                        data, usage, response_model = self._normalize_call_result(raw_result, model)
                        parser_audit = dict(data.pop("_pif_parser_audit", {}) or {}) if isinstance(data, dict) else {}
                        normalization_audit: dict[str, Any] = {}
                        if normalizer:
                            normalized = normalizer(data)
                            if isinstance(normalized, tuple) and len(normalized) == 2:
                                data, normalization_audit = normalized
                            else:
                                data = normalized
                        if validator:
                            validation = validator(data)
                            valid = bool(validation[0]) if isinstance(validation, tuple) else bool(validation)
                            reason = validation[1] if isinstance(validation, tuple) and len(validation) > 1 else "validation failed"
                            if not valid:
                                detail = reason if isinstance(reason, (dict, list)) else {"message": clean_space(reason)}
                                invalid_candidates.append({
                                    "provider": provider,
                                    "model": response_model,
                                    "data": data,
                                    "validation": detail,
                                    "parser_audit": parser_audit,
                                    "normalization_audit": normalization_audit,
                                })
                                raise LLMError(
                                    f"validation_failed: {json.dumps(detail, ensure_ascii=False)[:1200]}",
                                    category="validation_failed",
                                    candidates=invalid_candidates,
                                )
                        state.mark_success(model, usage)
                        self._persist_states()
                        attempt.update({
                            "status": "success",
                            "response_model": response_model,
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                            "parser_audit": parser_audit,
                            "normalization_audit": normalization_audit,
                            "usage": {
                                "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                                "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                                "total_tokens": int(usage.get("total_tokens") or 0),
                            },
                        })
                        attempts.append(attempt)
                        return LLMResult(data=data, provider=provider, model=response_model, attempts=attempts)
                    except Exception as exc:
                        category = classify_llm_failure(exc)
                        if isinstance(exc, LLMError) and getattr(exc, "candidates", None):
                            invalid_candidates = list(exc.candidates)
                        state.mark_failure(model, category, cooldown_seconds=self._failure_cooldown_seconds(exc, cooldown_seconds))
                        self._persist_states()
                        attempt.update({
                            "status": "failed",
                            "failure_category": category,
                            "error_type": type(exc).__name__,
                            "error": self._safe_error_text(exc),
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                        })
                        if category == "validation_failed" and invalid_candidates:
                            latest_invalid = invalid_candidates[-1]
                            attempt["validation"] = latest_invalid.get("validation") or {}
                            attempt["parser_audit"] = latest_invalid.get("parser_audit") or {}
                            attempt["normalization_audit"] = latest_invalid.get("normalization_audit") or {}
                        attempts.append(attempt)
                        retryable = category in {
                            "rate_limited", "timeout", "provider_unavailable", "network_error", "empty_response",
                        }
                        if retry_index + 1 < attempts_per_model and retryable:
                            time.sleep(min(4.0, 0.75 * (2 ** retry_index)))
                            continue
                        break

        if not configured_provider_seen:
            category = "no_provider_configured"
            message = "No configured LLM provider is available"
        else:
            category = summarize_attempt_categories(attempts)
            message = f"All configured LLM attempts failed ({category})"
            last_error = next((row.get("error") for row in reversed(attempts) if row.get("status") in {"failed", "skipped"}), "")
            if last_error:
                message += f": {last_error}"
        raise LLMError(message, attempts=attempts, category=category, candidates=invalid_candidates)

    def text_task(
        self,
        *,
        system: str,
        prompt: str,
        provider_order: tuple[str, ...] | None = None,
        temperature: float = 0.05,
        max_models_per_provider: int = 2,
        task_name: str = "text_task",
        max_output_tokens: int | None = None,
    ) -> LLMTextResult:
        """Run a plain-text task without forcing provider JSON mode.

        Translation uses this only after structured JSON translation has failed
        or returned an unusable shape. It never replaces structured analysis.
        """
        attempts: list[dict[str, Any]] = []
        order = tuple(provider_order) if provider_order is not None else self.provider_order("translation")
        if not order:
            raise LLMError("No provider is configured for the requested text task", category="no_provider_configured")
        runtime_cap = max(1, int(os.getenv("PIF_LLM_MAX_MODELS_PER_PROVIDER", "2")))
        max_models_per_provider = min(max_models_per_provider, runtime_cap)
        configured_seen = False
        for provider in order:
            provider = provider.lower()
            key = self.keys.get(provider, "")
            state = self.states.get(provider)
            if not key or state is None:
                attempts.append({"task": task_name, "provider": provider, "model": "", "status": "skipped", "failure_category": "provider_not_configured", "error": "API key not configured", "at": utc_now_iso()})
                continue
            configured_seen = True
            if not state.available():
                attempts.append({"task": task_name, "provider": provider, "model": "", "status": "skipped", "failure_category": state.status, "error": state.disabled_reason or "provider is cooling down", "at": utc_now_iso()})
                continue
            models = self._discover_models(provider)[:max_models_per_provider]
            if not models:
                attempts.append({
                    "task": task_name, "provider": provider, "model": "", "status": "failed",
                    "failure_category": "model_discovery_failed",
                    "error": "No usable text generation model discovered or configured", "at": utc_now_iso(),
                })
                continue
            for model in models:
                if not state.model_available(model):
                    attempts.append({"task": task_name, "provider": provider, "model": model, "status": "skipped", "failure_category": "model_cooldown", "error": "model is unavailable for this run", "at": utc_now_iso()})
                    continue
                allowed, reason = self._billing_guard(provider, model)
                if not allowed:
                    attempts.append({"task": task_name, "provider": provider, "model": model, "status": "skipped", "failure_category": "paid_route_blocked", "error": reason, "at": utc_now_iso()})
                    continue
                started = time.monotonic()
                attempt = {"task": task_name, "provider": provider, "model": model, "at": utc_now_iso(), "system_chars": len(system), "prompt_chars": len(prompt)}
                try:
                    if provider == "gemini":
                        value = self._gemini_call(model, system, prompt, temperature, json_mode=False, max_output_tokens=max_output_tokens)
                    elif provider == "groq":
                        value = self._groq_call(model, system, prompt, temperature, json_mode=False, max_output_tokens=max_output_tokens)
                    else:
                        value = self._openai_compatible_call(provider, model, system, prompt, temperature, json_mode=False, max_output_tokens=max_output_tokens)
                    text, usage, response_model = self._normalize_call_result(value, model)
                    text = clean_space(text)
                    if not text:
                        raise LLMError("Provider returned empty content", category="empty_response")
                    state.mark_success(model, usage)
                    self._persist_states()
                    attempt.update({"status": "success", "response_model": response_model, "elapsed_ms": round((time.monotonic() - started) * 1000)})
                    attempts.append(attempt)
                    return LLMTextResult(text=text, provider=provider, model=response_model, attempts=attempts)
                except Exception as exc:
                    category = classify_llm_failure(exc)
                    state.mark_failure(model, category, cooldown_seconds=self._failure_cooldown_seconds(exc, 60))
                    self._persist_states()
                    attempt.update({"status": "failed", "failure_category": category, "error_type": type(exc).__name__, "error": self._safe_error_text(exc), "elapsed_ms": round((time.monotonic() - started) * 1000)})
                    attempts.append(attempt)
                    continue
        category = summarize_attempt_categories(attempts) if configured_seen else "no_provider_configured"
        last_error = next((row.get("error") for row in reversed(attempts) if row.get("status") == "failed"), "")
        message = f"All configured LLM text attempts failed ({category})"
        if last_error:
            message += f": {last_error}"
        raise LLMError(message, attempts=attempts, category=category)

    def usage_snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "generated_at": utc_now_iso(),
            "shared_state_file": str(self.state_store.path or ""),
            "provider_order": {
                "extract": list(self.provider_order("extract")),
                "rescue": list(self.provider_order("rescue")),
                "overview": list(self.provider_order("overview")),
                "relevance": list(self.provider_order("relevance")),
                "translation": list(self.provider_order("translation")),
            },
            "billing_guard": {
                "allow_paid": self.paid_requests_allowed(),
                "free_provider_allowlist": _split_csv(os.getenv("PIF_LLM_FREE_PROVIDER_ALLOWLIST", "gemini,bigmodel,siliconflow,mistral,deepseek,openrouter,groq")),
            },
            "task_failures": list(self.task_failures),
            "model_discovery": self.model_discovery_snapshot(),
            "providers": {
                name: {
                    "configured": bool(self.keys.get(name)),
                    **state.safe_dict(),
                }
                for name, state in self.states.items()
            },
        }
