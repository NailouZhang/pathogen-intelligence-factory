from __future__ import annotations

import hashlib
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
        "discover_models": "false",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "models_env": "PIF_DEEPSEEK_MODELS",
        "discover_models": "false",
    },
}

DEFAULT_MODELS: dict[str, list[str]] = {
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
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
    ) -> None:
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.category = category or "unknown"


def _extract_json(text: str) -> dict[str, Any] | list[Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start_obj = raw.find("{")
        end_obj = raw.rfind("}")
        start_arr = raw.find("[")
        end_arr = raw.rfind("]")
        candidates: list[str] = []
        if start_obj >= 0 and end_obj > start_obj:
            candidates.append(raw[start_obj : end_obj + 1])
        if start_arr >= 0 and end_arr > start_arr:
            candidates.append(raw[start_arr : end_arr + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise LLMError("Model response did not contain valid JSON", category="invalid_json")


def classify_llm_failure(error: Any) -> str:
    """Map provider/HTTP/schema failures to stable, audit-friendly categories."""

    if isinstance(error, LLMError) and error.category not in {"", "unknown"}:
        return error.category
    text = clean_space(error).lower()
    if not text or text == "[]":
        return "no_provider_configured"
    if "validation_failed" in text or "schema validation" in text or "validator" in text:
        return "validation_failed"
    if "valid json" in text or "jsondecode" in text or "json" in text and "parse" in text:
        return "invalid_json"
    if any(token in text for token in ("401", "403", "unauthorized", "forbidden", "invalid api key", "api_key_invalid", "authentication")):
        return "authentication_failed"
    # HTTP 429 is always a temporary rate-limit signal.  Check it before
    # generic quota wording because providers often return messages such as
    # "429 quota exceeded" for a retryable per-minute or concurrency limit.
    if any(token in text for token in (
        "429", "rate limit", "resource_exhausted", "too many requests", "请求过于频繁", "并发超额",
    )):
        return "rate_limited"
    if any(token in text for token in (
        "402", "insufficient credit", "insufficient balance", "negative credit", "payment required",
        "余额不足", "余额已用完", "账户余额", "赠送余额不可用", "free granted balance is unavailable",
    )):
        return "quota_exhausted"
    if any(token in text for token in ("quota", "billing", "insufficient_quota", "daily limit", "monthly limit", "token budget exhausted")):
        return "quota_exhausted"
    if any(token in text for token in ("timeout", "timed out", "read timed out", "connect timeout")):
        return "timeout"
    if any(token in text for token in ("context length", "maximum context", "token limit", "request too large", "payload too large")):
        return "context_or_output_limit"
    if any(token in text for token in ("no candidates", "no choices", "empty response")):
        return "empty_response"
    if any(token in text for token in ("500", "502", "503", "504", "server error", "service unavailable")):
        return "provider_unavailable"
    if any(token in text for token in ("connection", "dns", "network", "http request failed")):
        return "network_error"
    return "unknown"


def summarize_attempt_categories(attempts: list[dict[str, Any]]) -> str:
    failures = [clean_space(row.get("failure_category")) for row in attempts if row.get("status") == "failed"]
    failures = [value for value in failures if value]
    if not failures:
        return "no_provider_configured" if not attempts else "unknown"
    return Counter(failures).most_common(1)[0][0]


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


@dataclass
class LLMResult:
    data: dict[str, Any] | list[Any]
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

    def _configured_models(self, provider: str) -> list[str]:
        if provider == "gemini":
            configured = _split_csv(os.getenv("PIF_GEMINI_MODELS", ""))
            single = os.getenv("GEMINI_MODEL", "").strip()
        else:
            config = OPENAI_COMPATIBLE_PROVIDERS[provider]
            configured = _split_csv(os.getenv(config["models_env"], ""))
            single = os.getenv(config["model_env"], "").strip()
        ordered: list[str] = []
        for name in ([single] if single else []) + configured + DEFAULT_MODELS.get(provider, []):
            if name and name not in ordered:
                ordered.append(name)
        return ordered

    def _discover_gemini_models(self) -> list[str]:
        return self._discover_models("gemini")

    def _discover_groq_models(self) -> list[str]:
        return self._discover_models("groq")

    def _discover_models(self, provider: str) -> list[str]:
        if provider in self._model_cache:
            return self._model_cache[provider]
        preferred = self._configured_models(provider)
        discovered: list[str] = []
        key = self.keys.get(provider, "")
        if key and OPENAI_COMPATIBLE_PROVIDERS.get(provider, {}).get("discover_models", "true") != "false":
            try:
                if provider == "gemini":
                    payload = self.http.get_json(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        params={"key": key, "pageSize": 100},
                    )
                    for model in payload.get("models", []):
                        methods = model.get("supportedGenerationMethods") or []
                        name = str(model.get("name", "")).removeprefix("models/")
                        if "generateContent" in methods and name and not any(
                            bad in name.lower() for bad in ("image", "embedding", "aqa")
                        ):
                            discovered.append(name)
                else:
                    config = OPENAI_COMPATIBLE_PROVIDERS[provider]
                    payload = self.http.get_json(
                        f"{self.provider_base_url(provider)}/models",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    items = payload.get("data", payload if isinstance(payload, list) else [])
                    for item in items or []:
                        name = clean_space(item.get("id") if isinstance(item, dict) else "")
                        low = name.lower()
                        if not name or any(
                            bad in low for bad in (
                                "whisper", "speech", "tts", "guard", "moderation", "embedding",
                                "rerank", "image", "vision", "audio", "compound",
                            )
                        ):
                            continue
                        if provider == "openrouter" and os.getenv("PIF_OPENROUTER_FREE_ONLY", "true").lower() in {"1", "true", "yes", "on"}:
                            pricing = item.get("pricing") if isinstance(item, dict) else None
                            free_pricing = isinstance(pricing, dict) and all(
                                str(pricing.get(field, "0")) in {"0", "0.0", "0.000000"}
                                for field in ("prompt", "completion")
                            )
                            if not (name.endswith(":free") or free_pricing):
                                continue
                        discovered.append(name)
            except Exception:
                pass

        def score(name: str) -> tuple[int, str]:
            low = name.lower()
            value = 0
            if provider == "openrouter" and name == "openrouter/free":
                value += 200
            if any(token in low for token in ("small", "flash-lite", "mini", "8b", "7b")):
                value += 90
            if any(token in low for token in ("qwen", "mistral", "llama", "gemma", "glm", "deepseek")):
                value += 50
            if any(token in low for token in ("reasoning", "thinking", "r1")):
                value -= 30
            return (-value, name)

        ordered: list[str] = []
        for name in preferred + sorted(discovered, key=score):
            if name and name not in ordered:
                ordered.append(name)
        self._model_cache[provider] = ordered[:20]
        return self._model_cache[provider]

    def _gemini_call(self, model: str, system: str, prompt: str, temperature: float) -> tuple[Any, dict[str, Any], str]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "maxOutputTokens": max(256, int(os.getenv("PIF_LLM_MAX_OUTPUT_TOKENS", "1400"))),
            },
        }
        response = self.http.request(
            "POST",
            url,
            params={"key": self.keys["gemini"]},
            json=payload,
            timeout=int(os.getenv("PIF_LLM_HTTP_TIMEOUT", "55")),
            retry_attempts=1,
        )
        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini returned no candidates: {body}", category="empty_response")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(part.get("text", "")) for part in parts)
        usage = body.get("usageMetadata") or {}
        normalized = {
            "prompt_tokens": usage.get("promptTokenCount") or 0,
            "completion_tokens": usage.get("candidatesTokenCount") or 0,
            "total_tokens": usage.get("totalTokenCount") or 0,
        }
        return _extract_json(text), normalized, clean_space(body.get("modelVersion")) or model

    def _openai_compatible_call(
        self,
        provider: str,
        model: str,
        system: str,
        prompt: str,
        temperature: float,
    ) -> tuple[Any, dict[str, Any], str]:
        config = OPENAI_COMPATIBLE_PROVIDERS[provider]
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "max_tokens": max(256, int(os.getenv("PIF_LLM_MAX_OUTPUT_TOKENS", "1400"))),
        }
        disable_thinking = os.getenv("PIF_LLM_DISABLE_THINKING", "true").lower() in {"1", "true", "yes", "on"}
        if provider == "siliconflow" and disable_thinking:
            payload["enable_thinking"] = False
        if provider in {"bigmodel", "deepseek"} and disable_thinking:
            payload["thinking"] = {"type": "disabled"}
        if provider == "deepseek" and os.getenv("PIF_DEEPSEEK_GRANTED_BALANCE_ONLY", "true").lower() in {"1", "true", "yes", "on"}:
            account = self.provider_account_info("deepseek", refresh=True)
            if account.get("status") != "ok":
                category = clean_space(account.get("failure_category")) or "provider_unavailable"
                raise LLMError(
                    "DeepSeek balance check failed before a free-balance-only request",
                    category=category,
                )
            if not account.get("granted_balance_available"):
                raise LLMError(
                    "DeepSeek free granted balance is unavailable; paid balance is protected by PIF_DEEPSEEK_GRANTED_BALANCE_ONLY=true",
                    category="quota_exhausted",
                )
        if provider == "mistral":
            payload["prompt_cache_key"] = clean_space(os.getenv("PIF_MISTRAL_PROMPT_CACHE_KEY", "pif-structured-analysis-v1"))
        headers = {"Authorization": f"Bearer {self.keys[provider]}"}
        if provider == "openrouter":
            referer = os.getenv("PIF_OPENROUTER_HTTP_REFERER", "").strip()
            title = os.getenv("PIF_OPENROUTER_APP_TITLE", "Pathogen Intelligence Factory").strip()
            if referer:
                headers["HTTP-Referer"] = referer
            if title:
                headers["X-Title"] = title
        response = self.http.request(
            "POST",
            f"{self.provider_base_url(provider)}/chat/completions",
            headers=headers,
            json=payload,
            timeout=int(os.getenv("PIF_LLM_HTTP_TIMEOUT", "55")),
            retry_attempts=1,
        )
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(f"{provider} returned no choices: {body}", category="empty_response")
        message = choices[0].get("message") or {}
        return _extract_json(message.get("content", "")), body.get("usage") or {}, clean_space(body.get("model")) or model

    def _groq_call(self, model: str, system: str, prompt: str, temperature: float) -> tuple[Any, dict[str, Any], str]:
        return self._openai_compatible_call("groq", model, system, prompt, temperature)

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

    def json_task(
        self,
        *,
        system: str,
        prompt: str,
        provider_order: tuple[str, ...] | None = None,
        temperature: float = 0.1,
        validator: Any | None = None,
        max_models_per_provider: int = 3,
        task_name: str = "json_task",
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
            if not state.available():
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
                if not state.model_available(model):
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
                        raw_result = caller(model, system, prompt, temperature)
                        data, usage, response_model = self._normalize_call_result(raw_result, model)
                        if validator:
                            valid, reason = validator(data)
                            if not valid:
                                raise LLMError(f"validation_failed: {reason}", category="validation_failed")
                        state.mark_success(model, usage)
                        self._persist_states()
                        attempt.update({
                            "status": "success",
                            "response_model": response_model,
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
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
                        state.mark_failure(model, category, cooldown_seconds=self._failure_cooldown_seconds(exc, cooldown_seconds))
                        self._persist_states()
                        attempt.update({
                            "status": "failed",
                            "failure_category": category,
                            "error_type": type(exc).__name__,
                            "error": self._safe_error_text(exc),
                            "elapsed_ms": round((time.monotonic() - started) * 1000),
                        })
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
            "providers": {
                name: {
                    "configured": bool(self.keys.get(name)),
                    **state.safe_dict(),
                }
                for name, state in self.states.items()
            },
        }
