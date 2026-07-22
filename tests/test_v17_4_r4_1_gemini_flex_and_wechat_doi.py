from __future__ import annotations

from types import SimpleNamespace

from pifactory.http import HttpClient
from pifactory.llm import LLMRouter, classify_llm_failure
from pifactory.provider_state import ProviderRuntimeState


def test_gemini_zero_model_quota_is_model_local():
    error = RuntimeError(
        "HTTP status 429: RESOURCE_EXHAUSTED; Quota exceeded for metric: "
        "generate_content_free_tier_requests, limit: 0, model: gemini-2.0-flash"
    )
    assert classify_llm_failure(error) == "model_quota_exhausted"


def test_model_local_quota_does_not_disable_provider():
    state = ProviderRuntimeState(provider="gemini")
    state.mark_failure("gemini-old", "model_quota_exhausted")
    assert state.available()
    assert not state.model_available("gemini-old")
    assert state.model_available("gemini-new")


def test_gemini_discovery_filters_retired_2_0_and_prefers_lite(monkeypatch):
    class FakeHTTP:
        def get_json(self, *args, **kwargs):
            return {
                "models": [
                    {"name": "models/gemini-2.0-flash", "baseModelId": "gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-2.5-flash", "baseModelId": "gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-2.5-flash-lite", "baseModelId": "gemini-2.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/gemini-3.1-flash-lite", "baseModelId": "gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
                ]
            }

    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    monkeypatch.setenv("PIF_GEMINI_PREFER_LITE", "true")
    router = LLMRouter(FakeHTTP(), provider_keys={"gemini": "key"})
    models = router.discover_models("gemini", refresh=True)
    assert "gemini-2.0-flash" not in models
    assert models[0] == "gemini-3.1-flash-lite"
    audit = router.model_discovery_snapshot("gemini")
    assert "gemini-2.0-flash" in audit["ignored_retired_models"]


def test_gemini_gets_wider_bounded_model_window(monkeypatch):
    monkeypatch.setenv("PIF_GEMINI_MAX_MODELS_PER_PROVIDER", "4")
    assert LLMRouter._provider_model_limit("gemini", 2, 2) == 4
    assert LLMRouter._provider_model_limit("groq", 2, 2) == 2
